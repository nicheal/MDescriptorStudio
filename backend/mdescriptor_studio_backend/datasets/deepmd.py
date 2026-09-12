"""DeepMD dataset adapter backed by the dpdata package (ADR-19).

The hand-written set.*/npy parser was retired; layout and label rules are
dpdata's `deepmd/npy` semantics: type.raw at the root plus set.*/coord.npy,
with box.npy (or a `nopbc` marker file) and optional energy/force/virial.npy.
Frames are concatenated in sorted set order; a dataset is labeled only when
energy.npy is present (dpdata raises DataError on LabeledSystem otherwise).
The whole system is loaded eagerly into memory at construction — the old
memmap lazy path is gone (documented trade-off of ADR-19).
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from ..errors import AppError, INVALID_DATASET, INVALID_PARAMS
from ..security import ensure_no_reparse_points
from .base import DatasetAdapter, DatasetFrame, ScanMeta, pbc_summary
from .deepmd_symbols import _SYMBOL_TO_Z, _Z_TO_SYMBOL  # noqa: F401 (re-exported)

MAX_DEEPMD_FRAMES = 250_000
MAX_DEEPMD_ATOMS = 10_000_000
MAX_DEEPMD_BYTES = 2 * 1024 * 1024 * 1024
MAX_DEEPMD_FILES = 100_000


def _preflight_npy_layout(path: Path, sets: list[Path]) -> None:
    """Inspect NPY headers before dpdata eagerly materializes the system."""
    try:
        ensure_no_reparse_points(path)
        type_path = path / "type.raw"
        ensure_no_reparse_points(type_path)
        type_tokens = type_path.read_text(encoding="utf-8", errors="strict").split()
        type_count = len(type_tokens)
        if type_count <= 0 or type_count > MAX_DEEPMD_ATOMS:
            raise AppError(INVALID_DATASET, "DeepMD atom count is outside the supported limit")

        total_bytes = type_path.stat().st_size
        total_files = 1
        total_frames = 0
        total_atoms = 0
        for set_path in sets:
            ensure_no_reparse_points(set_path)
            coord_path = set_path / "coord.npy"
            ensure_no_reparse_points(coord_path)
            if not coord_path.is_file():
                raise AppError(INVALID_DATASET, "DeepMD coord.npy is missing")
            total_bytes += coord_path.stat().st_size
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
                ensure_no_reparse_points(child)
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
        import dpdata  # deferred: heavy import, only needed for DeepMD datasets

        path = self.source_path
        sets = sorted(p for p in path.glob("set.*") if p.is_dir()) if path.is_dir() else []
        if not sets or not (path / "type.raw").exists():
            raise AppError(
                INVALID_DATASET,
                f"no DeepMD frames found (expected type.raw + set.*/coord.npy): {path}",
            )
        _preflight_npy_layout(path, sets)
        try:
            if any((s / "energy.npy").exists() for s in sets):
                system = dpdata.LabeledSystem(str(path), fmt="deepmd/npy")
            else:
                system = dpdata.System(str(path), fmt="deepmd/npy")
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001 - dpdata raises bare OS/Value errors
            raise AppError(
                INVALID_DATASET, f"invalid DeepMD dataset ({type(exc).__name__}): {exc}"
            ) from exc

        data = system.data
        symbols = list(data["atom_names"])
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
        type_raw = np.asarray(data["atom_types"], dtype=np.int64).reshape(-1)
        self.natoms = int(type_raw.size)
        self.numbers = np.asarray([z_by_type[t] for t in type_raw], dtype=np.int64)

        self._coords = data["coords"]
        raw_cells = data.get("cells")
        if raw_cells is None:
            raw_cells = np.zeros((self._coords.shape[0], 3, 3))
        self._cells = np.asarray(raw_cells, dtype=np.float64)
        self._energies = data.get("energies")
        self._forces = data.get("forces")
        self._virials = data.get("virials")

        dets = np.linalg.det(self._cells) if self._cells.size else np.array([])
        finite = np.isfinite(dets)
        # a nopbc set (zero boxes throughout, optional marker file) is fine as
        # isolated; a degenerate box inside an otherwise periodic system is
        # corrupt data and must stay visible (health panel), not be masked
        self._periodic_system = bool((finite & (np.abs(dets) > 1e-8)).any())

        if self._coords.ndim != 3 or self._coords.shape[1] != self.natoms:
            raise AppError(
                INVALID_DATASET,
                f"coord.npy holds {self._coords.shape[1] if self._coords.ndim == 3 else '?'}"
                f" atoms/frame but type.raw declares {self.natoms} atoms",
            )
        self.number_of_frames = int(self._coords.shape[0])

    # -- metadata -----------------------------------------------------------
    def scan(self) -> ScanMeta:
        file_size = 0
        file_count = 0
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
                ensure_no_reparse_points(directory_path / dirname)
            for filename in filenames:
                child = directory_path / filename
                ensure_no_reparse_points(child)
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
        box = np.asarray(self._cells[index], dtype=np.float64).reshape(3, 3)
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
        energy = None
        if self._energies is not None:
            energy = float(np.asarray(self._energies[index]).reshape(-1)[0])
        forces = None
        if self._forces is not None:
            forces = np.asarray(self._forces[index], dtype=np.float64).reshape(self.natoms, 3)
        virial = None
        if self._virials is not None:
            virial = np.asarray(self._virials[index], dtype=np.float64).reshape(3, 3)
        return DatasetFrame(
            numbers=self.numbers,
            positions=np.asarray(self._coords[index], dtype=np.float64).reshape(self.natoms, 3),
            cell=box if periodic else np.zeros((3, 3)),
            pbc=np.array([periodic] * 3),
            energy=energy,
            forces=forces,
            virial=virial,
            index=index,
            id=f"frame_{index}",
        )
