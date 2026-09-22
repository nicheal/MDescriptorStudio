"""Dataset layer: frames, adapter contract, format detection (design doc §9–§12)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..errors import AppError, UNSUPPORTED_FORMAT


@dataclass
class DatasetFrame:
    """GUI-side frame; energy/forces/virial never enter StructureBatch (§10)."""

    numbers: np.ndarray  # (natoms,) int atomic numbers
    positions: np.ndarray  # (natoms, 3) float64
    cell: np.ndarray  # (3, 3) float64, zero for isolated
    pbc: np.ndarray  # (3,) bool
    energy: float | None = None
    forces: np.ndarray | None = None  # (natoms, 3)
    virial: np.ndarray | None = None  # (3, 3)
    index: int = 0
    id: str = field(default="")


@dataclass
class ScanMeta:
    number_of_frames: int
    file_size: int
    elements: list[str]  # may be empty when only statistics can determine it
    periodicity: dict  # {"fully_periodic": bool, "isolated": bool, "mixed": bool, "flags": [..]}


class DatasetAdapter(ABC):
    format_name: str

    def __init__(self, source_path: Path):
        self.source_path = Path(source_path)

    @abstractmethod
    def scan(self) -> ScanMeta: ...

    @abstractmethod
    def __len__(self) -> int: ...

    @abstractmethod
    def get_frame(self, index: int) -> DatasetFrame: ...

    def iter_frames(self):
        for i in range(len(self)):
            yield self.get_frame(i)


def is_deepmd_set_dir(path: Path) -> bool:
    """Whether one directory has the DeepMD set shape consumed by the reader."""
    return path.is_dir() and path.name.startswith("set.") and (path / "coord.npy").is_file()


def detect_format(path: Path) -> str:
    if path.is_dir():
        if (path / "type.raw").exists() and any(
            is_deepmd_set_dir(d) for d in path.iterdir()
        ):
            return "deepmd"
        raise AppError(
            UNSUPPORTED_FORMAT,
            f"directory without DeepMD npy layout (type.raw + set.*/coord.npy missing): {path}",
        )
    if path.is_file() and path.suffix.lower() in (".xyz", ".extxyz"):
        return "extxyz"
    raise AppError(UNSUPPORTED_FORMAT, f"unsupported dataset path: {path}")


def create_adapter(path: Path, fmt: str | None = None) -> DatasetAdapter:
    """Construct a reader through the format registry.

    Imported lazily so ``datasets.readers`` can depend on the frame/base types
    in this module without a circular import at package import time.
    """
    from .readers import create_reader

    return create_reader(path, fmt)


def pbc_summary(pbc_tuples: set[tuple[bool, bool, bool]]) -> dict:
    flags = sorted(
        "".join("XYZ"[i] if v else "." for i, v in enumerate(t)) for t in pbc_tuples
    )
    all_true = pbc_tuples == {(True, True, True)}
    all_false = pbc_tuples == {(False, False, False)}
    return {
        "fully_periodic": all_true,
        "isolated": all_false,
        "mixed": not (all_true or all_false),
        "flags": flags,
    }
