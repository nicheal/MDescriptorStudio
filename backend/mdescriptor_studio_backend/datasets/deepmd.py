"""DeepMD-kit raw format adapter (type.raw / type_map.raw / set.*/*.npy)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..errors import AppError, INVALID_DATASET
from .base import DatasetAdapter, DatasetFrame, ScanMeta, pbc_summary


class DeepMDAdapter(DatasetAdapter):
    format_name = "deepmd"

    def __init__(self, source_path: Path):
        super().__init__(source_path)
        path = self.source_path
        try:
            self.type_map = self._read_symbols(path / "type_map.raw")
            type_raw = np.loadtxt(path / "type.raw", dtype=np.int64).reshape(-1)
            # memmaps keep random frame access cheap even for multi-GB sets
            self.coord = np.load(path / "coord.npy", mmap_mode="r")
            self.box = np.load(path / "box.npy", mmap_mode="r")
        except (OSError, ValueError) as exc:
            raise AppError(INVALID_DATASET, f"invalid DeepMD dataset: {exc}") from exc
        self.natoms = int(type_raw.size)
        if self.coord.ndim == 2:
            frames = self.coord.shape[0]
        else:
            frames = self.coord.shape[0] if self.coord.ndim == 3 else 0
        self.number_of_frames = int(frames)
        self.numbers = np.asarray(self.type_map, dtype=np.int64)[type_raw]
        self._energy = self._optional_npy("energy.npy")
        self._forces = self._optional_npy("force.npy")
        self._virial = self._optional_npy("virial.npy")

    @staticmethod
    def _read_symbols(p: Path) -> list[int]:
        text = p.read_text(encoding="utf-8").split()
        symbol_to_z = _SYMBOL_TO_Z
        return [symbol_to_z[s] for s in text]

    def _optional_npy(self, name: str):
        p = self.source_path / name
        if not p.exists():
            return None
        return np.load(p, mmap_mode="r")

    def scan(self) -> ScanMeta:
        file_size = sum(f.stat().st_size for f in self.source_path.rglob("*") if f.is_file())
        symbols = sorted({_Z_TO_SYMBOL[z] for z in self.numbers.tolist()})
        periodicity = self._periodicity()
        return ScanMeta(
            number_of_frames=self.number_of_frames,
            file_size=file_size,
            elements=symbols,
            properties={
                "energy": self._energy is not None,
                "forces": self._forces is not None,
                "virial": self._virial is not None,
            },
            periodicity=periodicity,
        )

    def _periodicity(self) -> dict:
        sample = np.asarray(self.box).reshape(self.box.shape[0], 3, 3)
        nonzero = np.abs(sample).sum(axis=(1, 2)) > 1e-8
        if nonzero.all():
            return pbc_summary({(True, True, True)})
        if (~nonzero).all():
            return pbc_summary({(False, False, False)})
        return pbc_summary({(True, True, True), (False, False, False)})

    def __len__(self) -> int:
        return self.number_of_frames

    def get_frame(self, index: int) -> DatasetFrame:
        if index < 0 or index >= self.number_of_frames:
            raise AppError("INVALID_PARAMS", f"frame index out of range: {index}")
        box = np.asarray(self.box[index], dtype=np.float64).reshape(3, 3)
        periodic = bool(np.abs(box).sum() > 1e-8)
        positions = np.asarray(self.coord[index], dtype=np.float64).reshape(self.natoms, 3)
        energy = None
        if self._energy is not None:
            energy = float(np.asarray(self._energy[index]).reshape(-1)[0])
        forces = None
        if self._forces is not None:
            forces = np.asarray(self._forces[index], dtype=np.float64).reshape(self.natoms, 3)
        virial = None
        if self._virial is not None:
            virial = np.asarray(self._virial[index], dtype=np.float64).reshape(3, 3)
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


_SYMBOL_TO_Z = {
    "H": 1, "He": 2, "Li": 3, "Be": 4, "B": 5, "C": 6, "N": 7, "O": 8, "F": 9,
    "Ne": 10, "Na": 11, "Mg": 12, "Al": 13, "Si": 14, "P": 15, "S": 16, "Cl": 17,
    "Ar": 18, "K": 19, "Ca": 20, "Sc": 21, "Ti": 22, "V": 23, "Cr": 24, "Mn": 25,
    "Fe": 26, "Co": 27, "Ni": 28, "Cu": 29, "Zn": 30, "Ga": 31, "Ge": 32, "As": 33,
    "Se": 34, "Br": 35, "Kr": 36, "Rb": 37, "Sr": 38, "Y": 39, "Zr": 40, "Nb": 41,
    "Mo": 42, "Tc": 43, "Ru": 44, "Rh": 45, "Pd": 46, "Ag": 47, "Cd": 48, "In": 49,
    "Sn": 50, "Sb": 51, "Te": 52, "I": 53, "Xe": 54, "Cs": 55, "Ba": 56, "La": 57,
    "Ce": 58, "Pr": 59, "Nd": 60, "Pm": 61, "Sm": 62, "Eu": 63, "Gd": 64, "Tb": 65,
    "Dy": 66, "Ho": 67, "Er": 68, "Tm": 69, "Yb": 70, "Lu": 71, "Hf": 72, "Ta": 73,
    "W": 74, "Re": 75, "Os": 76, "Ir": 77, "Pt": 78, "Au": 79, "Hg": 80, "Tl": 81,
    "Pb": 82, "Bi": 83, "Po": 84, "At": 85, "Rn": 86, "Fr": 87, "Ra": 88, "Ac": 89,
    "Th": 90, "Pa": 91, "U": 92, "Np": 93, "Pu": 94, "Am": 95, "Cm": 96, "Bk": 97,
    "Cf": 98, "Es": 99, "Fm": 100, "Md": 101, "No": 102, "Lr": 103, "Rf": 104,
    "Db": 105, "Sg": 106, "Bh": 107, "Hs": 108, "Mt": 109, "Ds": 110, "Rg": 111,
    "Cn": 112, "Nh": 113, "Fl": 114, "Mc": 115, "Lv": 116, "Ts": 117, "Og": 118,
}
_Z_TO_SYMBOL = {v: k for k, v in _SYMBOL_TO_Z.items()}
