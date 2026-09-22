"""Lazy DeepMD ``npy`` dataset adapter (ADR-19).

The hand-written set.*/npy parser was retired; layout and label rules are
dpdata's `deepmd/npy` semantics: type.raw at the root plus set.*/coord.npy,
with box.npy (or a `nopbc` marker file) and optional energy/force/virial.npy.
Frames are concatenated in sorted set order. Each set stays memory-mapped and
only the requested frame is copied into a :class:`DatasetFrame`, so opening a
large source does not duplicate all of its arrays in RAM.
"""

from __future__ import annotations

import os
from bisect import bisect_right
from pathlib import Path

import numpy as np

from ..errors import AppError, INVALID_DATASET, INVALID_PARAMS
from ..security import ensure_no_reparse_points
from .base import DatasetAdapter, DatasetFrame, ScanMeta, is_deepmd_set_dir, pbc_summary
from .deepmd_symbols import _SYMBOL_TO_Z, _Z_TO_SYMBOL

MAX_DEEPMD_FRAMES = 250_000
MAX_DEEPMD_ATOMS = 10_000_000
MAX_DEEPMD_BYTES = 2 * 1024 * 1024 * 1024
MAX_DEEPMD_FILES = 100_000


def _preflight_npy_layout(path: Path, sets: list[Path]) -> None:
    """Inspect NPY headers before opening the source through memory maps."""
    try:
        # One verified-prefix set for this listing: every path below shares the
        # source directory and its set subdirectories, and each call used to
        # re-walk the whole chain file by file.
        checked: set[str] = set()
        ensure_no_reparse_points(path, checked)
        type_path = path / "type.raw"
        ensure_no_reparse_points(type_path, checked)
        type_tokens = type_path.read_text(encoding="utf-8", errors="strict").split()
        type_count = len(type_tokens)
        if type_count <= 0 or type_count > MAX_DEEPMD_ATOMS:
            raise AppError(INVALID_DATASET, "DeepMD atom count is outside the supported limit")

        # Every file in the source counts exactly once. This used to add
        # coord.npy here and then again in the walk over the set directory below,
        # while root files other than type.raw (type_map.raw, nopbc) counted zero
        # times - so the guard enforced a different quantity than scan() reports
        # for the same directory, and refused datasets already under the advertised
        # cap because the dominant array was weighted twice.
        total_bytes = sum(item.stat().st_size for item in path.iterdir() if item.is_file())
        total_files = 0
        total_frames = 0
        total_atoms = 0
        for set_path in sets:
            ensure_no_reparse_points(set_path, checked)
            coord_path = set_path / "coord.npy"
            ensure_no_reparse_points(coord_path, checked)
            if not coord_path.is_file():
                raise AppError(INVALID_DATASET, "DeepMD coord.npy is missing")
            coord = np.load(coord_path, mmap_mode="r", allow_pickle=False)
            try:
                if coord.ndim == 3:
                    valid_shape = coord.shape[1] == type_count and coord.shape[2] == 3
                elif coord.ndim == 2:
                    valid_shape = coord.shape[1] == type_count * 3
                else:
                    valid_shape = False
                if not valid_shape:
                    raise AppError(INVALID_DATASET, "DeepMD coord.npy shape is invalid")
                frames = int(coord.shape[0])
                atoms_per_frame = type_count
                total_frames += frames
                total_atoms += frames * atoms_per_frame
                if total_frames > MAX_DEEPMD_FRAMES or total_atoms > MAX_DEEPMD_ATOMS:
                    raise AppError(INVALID_DATASET, "DeepMD dataset exceeds the supported size")
                if frames * type_count * 3 * int(coord.dtype.itemsize) > MAX_DEEPMD_BYTES:
                    raise AppError(INVALID_DATASET, "DeepMD coordinate array exceeds the supported size")
            finally:
                mmap_handle = getattr(coord, "_mmap", None)
                if mmap_handle is not None:
                    mmap_handle.close()
            for child in set_path.iterdir():
                ensure_no_reparse_points(child, checked)
                if child.is_file():
                    total_files += 1
                    if total_files > MAX_DEEPMD_FILES:
                        raise AppError(INVALID_DATASET, "DeepMD dataset contains too many files")
                    total_bytes += child.stat().st_size
                    if total_bytes > MAX_DEEPMD_BYTES:
                        raise AppError(INVALID_DATASET, "DeepMD dataset exceeds the supported size")
        if total_bytes > MAX_DEEPMD_BYTES:
            raise AppError(INVALID_DATASET, "DeepMD dataset exceeds the supported size")
    except AppError:
        raise
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise AppError(INVALID_DATASET, "DeepMD NPY layout is invalid or unavailable") from exc


class DeepMDAdapter(DatasetAdapter):
    format_name = "deepmd"

    def __init__(self, source_path: Path):
        super().__init__(source_path)
        path = self.source_path
        sets = sorted(p for p in path.iterdir() if is_deepmd_set_dir(p)) if path.is_dir() else []
        if not sets or not (path / "type.raw").exists():
            raise AppError(
                INVALID_DATASET,
                f"no DeepMD frames found (expected type.raw + set.*/coord.npy): {path}",
            )
        _preflight_npy_layout(path, sets)
        try:
            type_tokens = (path / "type.raw").read_text(encoding="utf-8", errors="strict").split()
            type_raw = np.asarray([int(token) for token in type_tokens], dtype=np.int64)
            type_map_path = path / "type_map.raw"
            if not type_map_path.is_file():
                raise AppError(INVALID_DATASET, "type_map.raw is required for DeepMD datasets")
            symbols = type_map_path.read_text(encoding="utf-8", errors="strict").split()
            if not symbols:
                raise AppError(INVALID_DATASET, "type_map.raw is empty")
        except AppError:
            raise
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            raise AppError(
                INVALID_DATASET, f"invalid DeepMD type metadata ({type(exc).__name__}): {exc}"
            ) from exc
        z_by_type = [_SYMBOL_TO_Z.get(s) for s in symbols]
        missing = [s for s, z in zip(symbols, z_by_type) if z is None]
        if missing:
            # descriptors need real atomic numbers; dpdata would silently use
            # artificial "Type_N" names when type_map.raw is absent/unparseable
            raise AppError(
                INVALID_DATASET,
                f"type_map.raw is missing or holds non-element names {missing};"
                " a real element type map is required",
            )
        if type_raw.size == 0 or type_raw.size > MAX_DEEPMD_ATOMS:
            raise AppError(INVALID_DATASET, "DeepMD atom count is outside the supported limit")
        self.natoms = int(type_raw.size)
        if type_raw.size and (type_raw.min() < 0 or type_raw.max() >= len(z_by_type)):
            raise AppError(
                INVALID_DATASET,
                f"type.raw holds atom types outside the {len(z_by_type)} entries"
                f" of type_map.raw: {int(type_raw.min())}..{int(type_raw.max())}",
            )
        self.numbers = np.asarray([z_by_type[t] for t in type_raw], dtype=np.int64)

        def open_array(file_path: Path) -> np.ndarray:
            try:
                array = np.load(file_path, mmap_mode="r", allow_pickle=False)
            except (OSError, ValueError, TypeError) as exc:
                raise AppError(
                    INVALID_DATASET,
                    f"invalid DeepMD array {file_path.name}: {type(exc).__name__}: {exc}",
                ) from exc
            if not np.issubdtype(array.dtype, np.number):
                raise AppError(INVALID_DATASET, f"DeepMD array {file_path.name} is not numeric")
            return array

        def validate_array(array: np.ndarray, frames: int, width: int, name: str) -> None:
            actual = int(np.prod(array.shape[1:], dtype=np.int64)) if array.ndim >= 1 else 0
            if array.ndim == 0 or int(array.shape[0]) != frames or actual != width:
                raise AppError(
                    INVALID_DATASET,
                    f"DeepMD {name}.npy shape is invalid for {frames} frames",
                )

        no_pbc = (path / "nopbc").is_file()
        records: list[dict[str, object]] = []
        starts: list[int] = []
        total_frames = 0
        for set_path in sets:
            coord = open_array(set_path / "coord.npy")
            frames = int(coord.shape[0])
            validate_array(coord, frames, self.natoms * 3, "coord")
            starts.append(total_frames)
            record: dict[str, object] = {"coords": coord, "frames": frames, "cells": None}
            if not no_pbc and (set_path / "box.npy").is_file():
                cells = open_array(set_path / "box.npy")
                validate_array(cells, frames, 9, "box")
                record["cells"] = cells
            records.append(record)
            total_frames += frames

        def attach_optional(filename: str, width: int, key: str) -> None:
            present = [(set_path / filename).is_file() for set_path in sets]
            if any(present) and not all(present):
                raise AppError(
                    INVALID_DATASET,
                    f"DeepMD {filename} must be present in every set or none",
                )
            if not all(present):
                return
            for record, set_path in zip(records, sets):
                array = open_array(set_path / filename)
                validate_array(array, int(record["frames"]), width, filename[:-4])
                record[key] = array

        attach_optional("energy.npy", 1, "energies")
        attach_optional("force.npy", self.natoms * 3, "forces")
        attach_optional("virial.npy", 9, "virials")

        self._sets = records
        self._starts = starts
        self.number_of_frames = total_frames

        # A nopbc marker explicitly wins. Otherwise inspect boxes in bounded
        # chunks so periodicity detection never materializes a whole source.
        self._periodic_system = False
        if not no_pbc:
            for record in records:
                cells = record["cells"]
                if cells is None:
                    continue
                cell_array = cells.reshape(-1, 3, 3)
                for start in range(0, cell_array.shape[0], 8192):
                    chunk = np.asarray(cell_array[start : start + 8192], dtype=np.float64)
                    determinants = np.linalg.det(chunk)
                    finite = np.isfinite(chunk).all(axis=(1, 2))
                    if bool((finite & (np.abs(determinants) > 1e-8)).any()):
                        self._periodic_system = True
                        break
                if self._periodic_system:
                    break

    # -- metadata -----------------------------------------------------------
    def scan(self) -> ScanMeta:
        file_size = 0
        file_count = 0
        # Prefixes already verified during this walk. Every entry below re-walked
        # the whole chain from the drive root, 57 probes per file on a 1 010-file
        # source; a directory is still checked the first time the walk names it,
        # which is what stops os.walk descending into a junction.
        checked: set[str] = set()
        def raise_walk_error(error: OSError) -> None:
            raise AppError(INVALID_DATASET, "DeepMD source cannot be enumerated safely") from error

        for directory, dirnames, filenames in os.walk(
            self.source_path,
            topdown=True,
            onerror=raise_walk_error,
            followlinks=False,
        ):
            directory_path = Path(directory)
            for dirname in dirnames:
                ensure_no_reparse_points(directory_path / dirname, checked)
            for filename in filenames:
                child = directory_path / filename
                ensure_no_reparse_points(child, checked)
                if not child.is_file():
                    raise AppError(INVALID_DATASET, "DeepMD source contains a non-regular file")
                file_count += 1
                if file_count > MAX_DEEPMD_FILES:
                    raise AppError(INVALID_DATASET, "DeepMD dataset contains too many files")
                file_size += child.stat().st_size
                if file_size > MAX_DEEPMD_BYTES:
                    raise AppError(INVALID_DATASET, "DeepMD dataset exceeds the supported size")
        symbols = sorted({_Z_TO_SYMBOL[z] for z in self.numbers.tolist()})
        # frames report uniform pbc: periodic iff the system has any valid box
        # (degenerate boxes in a periodic system keep the claim — see get_frame)
        pbc_set = {(True, True, True)} if self._periodic_system else {(False, False, False)}
        return ScanMeta(
            number_of_frames=self.number_of_frames,
            file_size=file_size,
            elements=symbols,
            periodicity=pbc_summary(pbc_set),
        )

    def __len__(self) -> int:
        return self.number_of_frames

    def get_frame(self, index: int) -> DatasetFrame:
        if index < 0 or index >= self.number_of_frames:
            raise AppError(INVALID_PARAMS, f"frame index out of range: {index}")
        record_index = bisect_right(self._starts, index) - 1
        record = self._sets[record_index]
        local_index = index - self._starts[record_index]
        cells = record["cells"]
        if cells is None:
            box = np.zeros((3, 3), dtype=np.float64)
        else:
            box = np.asarray(cells[local_index], dtype=np.float64).reshape(3, 3)
        finite = bool(np.isfinite(box).all())
        det = abs(float(np.linalg.det(box))) if finite else 0.0
        if det > 1e-8:
            periodic = True
        elif self._periodic_system:
            # degenerate box in a periodic system: keep the periodic claim (and
            # the raw box, so statistics flags the invalid cell). Nonfinite
            # entries are zeroed so NaN never reaches statistics/JSON.
            periodic = True
            if not finite:
                box = np.zeros((3, 3))
        else:
            periodic = False
            box = np.zeros((3, 3))
        positions = np.asarray(record["coords"][local_index], dtype=np.float64).reshape(self.natoms, 3)
        if not np.isfinite(positions).all():
            raise AppError(INVALID_DATASET, f"DeepMD frame {index} contains non-finite positions")
        energy = None
        energies = record.get("energies")
        if energies is not None:
            raw_energy = float(np.asarray(energies[local_index]).reshape(-1)[0])
            energy = raw_energy if np.isfinite(raw_energy) else None
        forces = None
        raw_forces = record.get("forces")
        if raw_forces is not None:
            forces = np.asarray(raw_forces[local_index], dtype=np.float64).reshape(self.natoms, 3)
        virial = None
        raw_virials = record.get("virials")
        if raw_virials is not None:
            virial = np.asarray(raw_virials[local_index], dtype=np.float64).reshape(3, 3)
            if not np.isfinite(virial).all():
                virial = None
        return DatasetFrame(
            numbers=self.numbers,
            positions=positions,
            cell=box if periodic else np.zeros((3, 3)),
            pbc=np.array([periodic] * 3),
            energy=energy,
            forces=forces,
            virial=virial,
            index=index,
            id=f"frame_{index}",
        )
