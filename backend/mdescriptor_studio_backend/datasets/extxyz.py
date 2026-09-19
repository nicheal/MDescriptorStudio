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
MAX_ATOMS = 1_000_000
MAX_FRAME_BYTES = 512 * 1024 * 1024
MAX_COLUMNS = 64
MAX_LINE_BYTES = 8 * 1024 * 1024
MAX_FRAMES = 250_000


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
                    line = _readline_bounded(f)
                    if not line:
                        break
                    stripped = line.strip()
                    if not stripped:
                        continue  # blank lines between frames carry no information
                    try:
                        natoms = int(stripped)
                    except ValueError as exc:
                        # A non-numeric line here means the frame boundary already
                        # desynchronized (an extra atom row read as a header), not
                        # trailing whitespace. Truncating quietly would persist a
                        # frame count and fingerprint that no longer describe the
                        # file, so fail instead.
                        raise AppError(
                            INVALID_DATASET,
                            "extXYZ frame count is malformed; frame boundaries do not line up",
                            {"frame": len(self._offsets) + 1},
                        ) from exc
                    if natoms < 0 or natoms > MAX_ATOMS:
                        raise AppError(INVALID_DATASET, "extXYZ atom count is outside the supported limit")
                    comment = _readline_bounded(f)
                    if not comment:
                        raise AppError(INVALID_DATASET, "extXYZ frame is truncated: header line is missing")
                    try:
                        meta = _parse_comment(comment)
                    except (AppError, IndexError, TypeError, ValueError) as exc:
                        if isinstance(exc, AppError):
                            raise
                        raise AppError(INVALID_DATASET, "extXYZ frame header is malformed") from exc
                    if len(self._offsets) >= MAX_FRAMES:
                        raise AppError(INVALID_DATASET, "extXYZ dataset contains too many frames")
                    n_cols = sum(size for _, size in meta["columns"])
                    if natoms * max(16, n_cols * 8) > MAX_FRAME_BYTES:
                        raise AppError(INVALID_DATASET, "extXYZ frame exceeds the supported size limit")
                    self._offsets.append(start)
                    self._frame_meta.append({"natoms": natoms, **meta})
                    frame_bytes = len(comment.encode("utf-8"))
                    for _ in range(natoms):
                        atom_line = _readline_bounded(f)
                        if not atom_line:
                            raise AppError(INVALID_DATASET, "extXYZ frame is truncated")
                        frame_bytes += len(atom_line.encode("utf-8"))
                        if frame_bytes > MAX_FRAME_BYTES:
                            raise AppError(INVALID_DATASET, "extXYZ frame exceeds the supported size limit")
        except OSError as exc:
            raise AppError(INVALID_DATASET, "cannot read extXYZ source") from exc

    def __len__(self) -> int:
        return len(self._offsets)

    # -- scan ------------------------------------------------------------------
    def scan(self) -> ScanMeta:
        periodicity = pbc_summary({tuple(m["pbc"]) for m in self._frame_meta})
        return ScanMeta(
            number_of_frames=len(self._offsets),
            file_size=self.source_path.stat().st_size,
            elements=[],  # filled by statistics pass
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
            _readline_bounded(f)  # natoms line
            comment = _readline_bounded(f)
            try:
                meta = {**_parse_comment(comment), "natoms": natoms}
            except (AppError, IndexError, TypeError, ValueError) as exc:
                if isinstance(exc, AppError):
                    raise
                raise AppError(INVALID_DATASET, "extXYZ frame header is malformed") from exc
            cols = meta["columns"]
            n_cols = sum(c for _, c in cols)
            if natoms < 0 or natoms > MAX_ATOMS or n_cols <= 0 or n_cols > MAX_COLUMNS * 3:
                raise AppError(INVALID_DATASET, "extXYZ frame dimensions are outside the supported limit")
            estimated_bytes = natoms * max(16, n_cols * 8)
            if estimated_bytes > MAX_FRAME_BYTES:
                raise AppError(INVALID_DATASET, "extXYZ frame exceeds the supported size limit")
            # token offset per column group = sum of the preceding groups' sizes
            # (a column index is NOT a token offset: forces at group index 2
            # start at token 4 in the standard species:1:pos:3:forces:3 layout)
            offsets: dict[str, int] = {}
            base = 0
            for name, size in cols:
                offsets[name] = base
                base += size
            species_i = offsets.get("species", 0)
            pos_i = offsets.get("pos", offsets.get("positions"))
            if pos_i is None:
                raise AppError(INVALID_DATASET, f"frame {index}: no pos/positions column")
            forces_i = (
                offsets["forces"]
                if "forces" in offsets
                else offsets.get("force")
            )
            species: list[str] = []
            positions = np.empty((natoms, 3), dtype=np.float64)
            forces = np.empty((natoms, 3), dtype=np.float64) if forces_i is not None else None
            frame_bytes = len(comment.encode("utf-8"))
            for row in range(natoms):
                atom_line = _readline_bounded(f)
                frame_bytes += len(atom_line.encode("utf-8"))
                if frame_bytes > MAX_FRAME_BYTES:
                    raise AppError(INVALID_DATASET, "extXYZ frame exceeds the supported size limit")
                tokens = atom_line.split()
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
            for what, array in (("position", positions), ("force", forces)):
                if array is None:
                    continue
                bad_rows = np.flatnonzero((~np.isfinite(array)).any(axis=1))
                if bad_rows.size:
                    raise AppError(
                        INVALID_DATASET, f"frame {index} row {int(bad_rows[0])}: non-finite {what}"
                    )
        numbers = np.array(
            [_atomic_number(s, f"frame {index} row {row}") for row, s in enumerate(species)],
            dtype=np.int64,
        )
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


def _readline_bounded(stream) -> str:
    line = stream.readline(MAX_LINE_BYTES + 1)
    if len(line.encode("utf-8")) > MAX_LINE_BYTES:
        raise AppError(INVALID_DATASET, "extXYZ line exceeds the supported size limit")
    return line


def _atomic_number(symbol: str, where: str) -> int:
    """Resolve a species token instead of guessing at it.

    An unresolvable token used to become Z=0, which the radii table then
    promoted to hydrogen: a Properties column misread as species silently
    described every atom as H and skewed contact detection.
    """
    z = _SYMBOL_TO_Z.get(symbol) or _SYMBOL_TO_Z.get(symbol.capitalize())
    if z is None and symbol.isdigit():
        z = int(symbol)  # writers that store the nuclear charge directly
    if not z:
        raise AppError(INVALID_DATASET, f"{where}: unknown species {symbol!r}")
    return int(z)


def _finite(value, what: str):
    """Reject NaN/Infinity where they appear instead of where they are read.

    A non-finite number cannot be encoded into an IPC frame the renderer can
    parse, and it means the structure is unusable rather than merely missing
    data, so it must not reach statistics, previews or the descriptor engine.
    """
    if not np.isfinite(value).all():
        raise AppError(INVALID_DATASET, f"extXYZ {what} is not a finite number")
    return value


def _parse_comment(comment: str) -> dict:
    meta: dict = {"lattice": None, "energy": None, "virial": None, "columns": None}
    lattice = None
    pbc = None
    for m in _KEY_RE.finditer(comment):
        key = m.group(1) or m.group(3)
        value = m.group(2) if m.group(1) else m.group(4)
        if key == "Lattice":
            lattice = _finite(np.fromstring(value.strip(), sep=" ", dtype=np.float64), "Lattice")
            if lattice.size > 9:
                raise AppError(INVALID_DATASET, "extXYZ lattice contains too many values")
        elif key == "pbc":
            values = value.split()
            if len(values) != 3:
                raise AppError(INVALID_DATASET, "extXYZ pbc must contain exactly three flags")
            pbc = tuple(v.upper() == "T" for v in values)
        elif key == "energy":
            meta["energy"] = _finite(float(value), "energy")
        elif key == "virial":
            meta["virial"] = _finite(np.fromstring(value.strip(), sep=" ", dtype=np.float64), "virial")
            if meta["virial"].size != 9:
                raise AppError(INVALID_DATASET, "extXYZ virial must contain exactly nine values")
        elif key == "Properties":
            cols: list[tuple[str, int]] = []
            parts = value.split(":")
            if len(parts) % 3 != 0:
                raise AppError(
                    INVALID_DATASET,
                    f"malformed Properties spec: {value!r} (expected name:type:size triples)",
                )
            for i in range(0, len(parts), 3):
                if len(cols) >= MAX_COLUMNS:
                    raise AppError(INVALID_DATASET, "extXYZ has too many Properties columns")
                size = int(parts[i + 2])
                if not parts[i] or size <= 0 or size > MAX_COLUMNS * 3:
                    raise AppError(INVALID_DATASET, "extXYZ Properties column size is invalid")
                cols.append((parts[i], size))
            meta["columns"] = cols
    if lattice is not None:
        if lattice.size != 9:
            raise AppError(INVALID_DATASET, "extXYZ lattice must contain exactly nine values")
        meta["lattice"] = lattice
    if pbc is None:
        nonzero = lattice is not None and np.abs(lattice).sum() > 1e-8
        pbc = (nonzero, nonzero, nonzero)
    meta["pbc"] = pbc
    if meta["columns"] is None:
        meta["columns"] = [("species", 1), ("pos", 3)]
    return meta
