"""DeepMD-kit raw format adapter.

Supports the standard layouts:
  - set.* subdirectories each holding coord.npy/box.npy (+ optional
    energy/force/virial), concatenated in sorted order (real-world datasets)
  - npy files at the dataset root (flat layout, single set)
Coordinates/forces may be (n, natoms*3) or (n, natoms, 3); energy (n,) or (n,1).
"""

from __future__ import annotations

import bisect
from pathlib import Path

import numpy as np

from ..errors import AppError, INVALID_DATASET, INVALID_PARAMS
from .base import DatasetAdapter, DatasetFrame, ScanMeta, pbc_summary
from .deepmd_symbols import _SYMBOL_TO_Z, _Z_TO_SYMBOL  # noqa: F401 (re-exported)


class DeepMDAdapter(DatasetAdapter):
    format_name = "deepmd"

    def __init__(self, source_path: Path):
        super().__init__(source_path)
        path = self.source_path
        try:
            self.type_map = self._read_symbols(path / "type_map.raw")
            type_raw = np.loadtxt(path / "type.raw", dtype=np.int64).reshape(-1)
        except AppError:
            raise
        except (OSError, ValueError) as exc:
            raise AppError(INVALID_DATASET, f"invalid DeepMD dataset: {exc}") from exc
        self.natoms = int(type_raw.size)
        self.numbers = np.asarray(self.type_map, dtype=np.int64)[type_raw]

        set_dirs = sorted(
            d for d in path.iterdir()
            if d.is_dir() and d.name.startswith("set") and (d / "coord.npy").exists()
        ) if path.is_dir() else []
        if set_dirs:
            self._sets = set_dirs
        elif (path / "coord.npy").exists():
            self._sets = [path]
        else:
            raise AppError(
                INVALID_DATASET,
                f"no DeepMD frames found (expected set.*/coord.npy or coord.npy): {path}",
            )

        self._cache: dict[Path, dict] = {}
        self._starts: list[int] = []
        total = 0
        for d in self._sets:
            coord = self._load(d, "coord.npy")
            n = int(coord.shape[0])
            if coord.shape[-1] != self.natoms * 3:
                raise AppError(
                    INVALID_DATASET,
                    f"{d.name}/coord.npy holds {coord.shape[-1]} coords/atom-frame"
                    f" but type.raw declares {self.natoms} atoms",
                )
            self._starts.append(total)
            total += n
        self.number_of_frames = total

    # -- loading -----------------------------------------------------------
    def _load(self, d: Path, name: str) -> np.ndarray | None:
        """Memmap loader; optional files (energy/force/virial/box) yield None."""
        data = self._cache.setdefault(d, {})
        arr = data.get(name)
        if arr is None:
            p = d / name
            if not p.exists():
                return None
            arr = np.load(p, mmap_mode="r")
            data[name] = arr
        return arr

    @staticmethod
    def _read_symbols(p: Path) -> list[int]:
        out = []
        for s in p.read_text(encoding="utf-8").split():
            z = _SYMBOL_TO_Z.get(s)
            if z is None:
                raise AppError(INVALID_DATASET, f"unknown element symbol {s!r} in {p.name}")
            out.append(z)
        return out

    # -- metadata -----------------------------------------------------------
    def scan(self) -> ScanMeta:
        file_size = sum(f.stat().st_size for f in self.source_path.rglob("*") if f.is_file())
        symbols = sorted({_Z_TO_SYMBOL[z] for z in self.numbers.tolist()})
        pbc_set: set[tuple[bool, bool, bool]] = set()
        props = {"energy": False, "forces": False, "virial": False}
        for d in self._sets:
            box = self._load(d, "box.npy")
            if box is None:
                pbc_set.add((False, False, False))
            else:
                nonzero = np.abs(np.asarray(box, dtype=np.float64)).sum(axis=1) > 1e-8
                if nonzero.all():
                    pbc_set.add((True, True, True))
                elif (~nonzero).all():
                    pbc_set.add((False, False, False))
                else:
                    pbc_set.add((True, True, True))
                    pbc_set.add((False, False, False))
            for key, name in (("energy", "energy.npy"), ("forces", "force.npy"), ("virial", "virial.npy")):
                if (d / name).exists():
                    props[key] = True
        return ScanMeta(
            number_of_frames=self.number_of_frames,
            file_size=file_size,
            elements=symbols,
            properties=props,
            periodicity=pbc_summary(pbc_set),
        )

    def __len__(self) -> int:
        return self.number_of_frames

    def get_frame(self, index: int) -> DatasetFrame:
        if index < 0 or index >= self.number_of_frames:
            raise AppError(INVALID_PARAMS, f"frame index out of range: {index}")
        set_idx = bisect.bisect_right(self._starts, index) - 1
        d = self._sets[set_idx]
        local = index - self._starts[set_idx]

        box_arr = self._load(d, "box.npy")
        if box_arr is not None:
            box = np.asarray(box_arr[local], dtype=np.float64).reshape(3, 3)
        else:
            box = np.zeros((3, 3))
        det = abs(float(np.linalg.det(box)))
        periodic = det > 1e-8
        positions = (
            np.asarray(self._load(d, "coord.npy")[local], dtype=np.float64)
            .reshape(-1)[: self.natoms * 3]
            .reshape(self.natoms, 3)
        )
        energy = None
        energy_arr = self._load(d, "energy.npy")
        if energy_arr is not None:
            energy = float(np.asarray(energy_arr[local]).reshape(-1)[0])
        forces = None
        force_arr = self._load(d, "force.npy")
        if force_arr is not None:
            forces = (
                np.asarray(force_arr[local], dtype=np.float64)
                .reshape(-1)[: self.natoms * 3]
                .reshape(self.natoms, 3)
            )
        virial = None
        virial_arr = self._load(d, "virial.npy")
        if virial_arr is not None:
            virial = np.asarray(virial_arr[local], dtype=np.float64).reshape(3, 3)
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
