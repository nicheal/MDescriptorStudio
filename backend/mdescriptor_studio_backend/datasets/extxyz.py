"""extXYZ adapter: byte-offset frame index for O(1) random access on large files."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from ..errors import AppError, INVALID_DATASET, INVALID_PARAMS
from .base import DatasetAdapter, DatasetFrame, ScanMeta, pbc_summary
from .deepmd_symbols import _SYMBOL_TO_Z

_KEY_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"|(\w+)\s*=\s*(\S+)')
_Z_RE = re.compile(r"^([A-Za-z]{1,3})")


class ExtXYZAdapter(DatasetAdapter):
    format_name = "extxyz"

    def __init__(self, source_path: Path):
        super().__init__(source_path)
        if not self.source_path.is_file():
            raise AppError(INVALID_DATASET, f"not a file: {self.source_path}")
        self._offsets: list[int] = []
        self._frame_meta: list[dict] = []
        self._build_index()

    # -- index ---------------------------------------------------------------
    def _build_index(self) -> None:
        try:
            with open(self.source_path, "r", encoding="utf-8", errors="replace") as f:
                while True:
                    start = f.tell()
                    line = f.readline()
                    if not line:
                        break
                    try:
                        natoms = int(line.strip())
                    except ValueError:
                        break  # trailing garbage
                    comment = f.readline()
                    if not comment:
                        break
                    meta = _parse_comment(comment)
                    self._offsets.append(start)
                    self._frame_meta.append({"natoms": natoms, **meta})
                    for _ in range(natoms):
                        if not f.readline():
                            break
        except OSError as exc:
            raise AppError(INVALID_DATASET, f"cannot read {self.source_path}: {exc}") from exc

    def __len__(self) -> int:
        return len(self._offsets)

    # -- scan ------------------------------------------------------------------
    def scan(self) -> ScanMeta:
        periodicity = pbc_summary({tuple(m["pbc"]) for m in self._frame_meta})
        return ScanMeta(
            number_of_frames=len(self._offsets),
            file_size=self.source_path.stat().st_size,
            elements=[],  # filled by statistics pass
            properties={},  # filled by statistics pass
            periodicity=periodicity,
        )

    # -- frames ------------------------------------------------------------------
    def get_frame(self, index: int) -> DatasetFrame:
        if index < 0 or index >= len(self._offsets):
            raise AppError(INVALID_PARAMS, f"frame index out of range: {index}")
        meta = self._frame_meta[index]
        natoms = meta["natoms"]
        with open(self.source_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self._offsets[index])
            f.readline()  # natoms line
            comment = f.readline()
            meta = {**_parse_comment(comment), "natoms": natoms}
            cols = meta["columns"]
            names = [c[0] for c in cols]
            species_i = names.index("species")
            pos_i = names.index("pos") if "pos" in names else names.index("positions")
            forces_i = (
                names.index("forces")
                if "forces" in names
                else names.index("force") if "force" in names else None
            )
            n_cols = sum(c for _, c in cols)
            species: list[str] = []
            positions = np.empty((natoms, 3), dtype=np.float64)
            forces = np.empty((natoms, 3), dtype=np.float64) if forces_i is not None else None
            for row in range(natoms):
                tokens = f.readline().split()
                if len(tokens) < n_cols:
                    raise AppError(INVALID_DATASET, f"frame {index} row {row}: truncated")
                try:
                    sym = _Z_RE.match(tokens[species_i])
                    species.append(sym.group(1) if sym else tokens[species_i])
                    positions[row] = [
                        float(tokens[pos_i]),
                        float(tokens[pos_i + 1]),
                        float(tokens[pos_i + 2]),
                    ]
                    if forces is not None:
                        forces[row] = [
                            float(tokens[forces_i]),
                            float(tokens[forces_i + 1]),
                            float(tokens[forces_i + 2]),
                        ]
                except (ValueError, IndexError) as exc:
                    raise AppError(
                        INVALID_DATASET, f"frame {index} row {row}: malformed atom line ({exc})"
                    ) from exc
        numbers = np.array([_SYMBOL_TO_Z.get(s, 0) for s in species], dtype=np.int64)
        lattice = meta.get("lattice")
        cell = lattice.reshape(3, 3) if lattice is not None else np.zeros((3, 3))
        periodic = bool(np.abs(cell).sum() > 1e-8)
        energy = meta.get("energy")
        virial = None
        if meta.get("virial") is not None:
            virial = np.asarray(meta["virial"], dtype=np.float64).reshape(3, 3)
        return DatasetFrame(
            numbers=numbers,
            positions=positions,
            cell=cell if periodic else np.zeros((3, 3)),
            pbc=np.array([periodic] * 3),
            energy=float(energy) if energy is not None else None,
            forces=forces,
            virial=virial,
            index=index,
            id=f"frame_{index}",
        )


def _parse_comment(comment: str) -> dict:
    meta: dict = {"lattice": None, "energy": None, "virial": None, "columns": None}
    lattice = None
    pbc = None
    for m in _KEY_RE.finditer(comment):
        key = m.group(1) or m.group(3)
        value = m.group(2) if m.group(1) else m.group(4)
        if key == "Lattice":
            lattice = np.fromstring(value.strip(), sep=" ", dtype=np.float64)
        elif key == "pbc":
            pbc = tuple(v.upper() == "T" for v in value.split())
        elif key == "energy":
            meta["energy"] = float(value)
        elif key == "virial":
            meta["virial"] = np.fromstring(value.strip(), sep=" ", dtype=np.float64)
        elif key == "Properties":
            cols: list[tuple[str, int]] = []
            parts = value.split(":")
            if len(parts) % 3 != 0:
                raise AppError(
                    INVALID_DATASET,
                    f"malformed Properties spec: {value!r} (expected name:type:size triples)",
                )
            for i in range(0, len(parts), 3):
                cols.append((parts[i], int(parts[i + 2])))
            meta["columns"] = cols
    if lattice is not None and lattice.size == 9:
        meta["lattice"] = lattice
    if pbc is None:
        nonzero = lattice is not None and np.abs(lattice).sum() > 1e-8
        pbc = (nonzero, nonzero, nonzero)
    meta["pbc"] = pbc
    if meta["columns"] is None:
        meta["columns"] = [("species", 1), ("pos", 3)]
    return meta
