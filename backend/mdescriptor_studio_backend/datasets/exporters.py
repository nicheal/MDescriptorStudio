"""Writers for the built-in dataset formats.

Both writers consume selected adapter frames and never touch the source files.
They are the only export implementation: dataset-view materialization and the
``analysis.export`` job both call them, so a written system is guaranteed to
reload through :mod:`datasets.extxyz` / :mod:`datasets.deepmd`.

The extxyz writer mirrors the reader's comment-line conventions (Lattice /
Properties / energy / virial / pbc). The DeepMD writer reproduces dpdata's raw
npy layout, which names the frame-property files ``energy.npy``,
``force.npy`` and ``virial.npy`` (singular).
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from ..errors import AppError, INVALID_DATASET
from ..security import ensure_no_reparse_points, open_text_for_write
from .deepmd_symbols import _Z_TO_SYMBOL

# Root files that make a directory a DeepMD system (plus any set.* directory).
_DEEPMD_ROOT_FILES = ("type.raw", "type_map.raw", "nopbc")


def _symbols(frame) -> list[str]:
    return [_Z_TO_SYMBOL.get(int(z), f"Z{int(z)}") for z in np.asarray(frame.numbers, dtype=np.int64)]


def write_extxyz(path: Path, frames: Iterable, *, extra_comments: dict | None = None) -> int:
    """Write frames to an extended-xyz file; returns the frame count.

    ``extra_comments`` maps a frame's 0-based position to an additional
    comment string (generation-run provenance for accepted candidates).
    Keys outside the written range are ignored.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open_text_for_write(path, newline="\n") as fh:
        for frame in frames:
            symbols = _symbols(frame)
            n = len(symbols)
            lattice = " ".join(f"{v:.6f}" for v in np.asarray(frame.cell, dtype=np.float64).reshape(-1))
            pbc = " ".join("T" if bool(v) else "F" for v in np.asarray(frame.pbc).reshape(3))
            props = "Properties=species:S:1:pos:R:3"
            if frame.forces is not None:
                props += ":forces:R:3"
            comment = f'Lattice="{lattice}" {props} pbc="{pbc}"'
            if extra_comments and written in extra_comments:
                comment += " " + str(extra_comments[written]).strip()
            if frame.energy is not None:
                comment += f" energy={float(frame.energy):.10g}"
            if frame.virial is not None:
                virial = " ".join(
                    f"{v:.6f}" for v in np.asarray(frame.virial, dtype=np.float64).reshape(-1)
                )
                comment += f' virial="{virial}"'
            fh.write(f"{n}\n{comment}\n")
            positions = np.asarray(frame.positions, dtype=np.float64).reshape(n, 3)
            forces = (
                np.asarray(frame.forces, dtype=np.float64).reshape(n, 3)
                if frame.forces is not None
                else None
            )
            for i, (sym, pos) in enumerate(zip(symbols, positions)):
                line = f"{sym} {pos[0]:.8f} {pos[1]:.8f} {pos[2]:.8f}"
                if forces is not None:
                    line += f" {forces[i][0]:.8f} {forces[i][1]:.8f} {forces[i][2]:.8f}"
                fh.write(line + "\n")
            written += 1
    return written


def _write_spool_npy(spool: Path, target: Path, shape: tuple[int, ...]) -> None:
    """Turn a raw float64 spool into an NPY file without loading it all."""
    output = np.lib.format.open_memmap(
        target,
        mode="w+",
        dtype=np.float64,
        shape=shape,
    )
    row_width = int(np.prod(shape[1:], dtype=np.int64)) if len(shape) > 1 else 1
    row_bytes = row_width * np.dtype(np.float64).itemsize
    rows_per_chunk = max(1, (8 * 1024 * 1024) // row_bytes)
    try:
        with spool.open("rb") as source:
            for start in range(0, shape[0], rows_per_chunk):
                end = min(shape[0], start + rows_per_chunk)
                raw = source.read((end - start) * row_bytes)
                if len(raw) != (end - start) * row_bytes:
                    raise AppError(INVALID_DATASET, f"DeepMD export spool is truncated: {spool.name}")
                values = np.frombuffer(raw, dtype=np.float64).reshape((end - start,) + shape[1:])
                output[start:end] = values
            if source.read(1):
                raise AppError(INVALID_DATASET, f"DeepMD export spool is oversized: {spool.name}")
        output.flush()
    finally:
        del output


def write_deepmd(path: Path, frames: Iterable) -> int:
    """Write frames as a DeepMD raw directory (type.raw + set.000/*.npy).

    DeepMD systems require one atom count per set, so frames with differing
    atom counts are rejected up front. A frame property is written only when
    every frame carries it: dpdata reads these arrays positionally, so a
    partially labelled set would silently misalign frames.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    ensure_no_reparse_points(path)
    # dpdata merges every set.* it finds and reads the label arrays
    # positionally, so leftovers from an earlier export would be silently
    # stitched into this one: a smaller second selection would reload with the
    # old frame count and mismatched labels.
    stale = sorted(
        item.name
        for item in path.iterdir()
        if item.name.startswith("set.") or item.name in _DEEPMD_ROOT_FILES
    )
    if stale:
        raise AppError(
            INVALID_DATASET,
            f"DeepMD destination already contains {', '.join(stale[:3])};"
            " export into an empty directory",
        )
    with TemporaryDirectory(prefix="md-deepmd-") as temp_name:
        temp = Path(temp_name)
        spool_paths = {
            name: temp / f"{name}.bin"
            for name in ("coord", "box", "energy", "force", "virial")
        }
        count = 0
        natoms = 0
        first_numbers: np.ndarray | None = None
        first_symbols: list[str] = []
        all_isolated = True
        seen = {name: False for name in ("energy", "force", "virial")}
        complete = {name: True for name in ("energy", "force", "virial")}
        with ExitStack() as stack:
            spool_files = {
                name: stack.enter_context(spool_paths[name].open("wb"))
                for name in spool_paths
            }
            for frame in frames:
                numbers = np.asarray(frame.numbers, dtype=np.int64).reshape(-1)
                if count == 0:
                    natoms = int(numbers.size)
                    first_numbers = numbers.copy()
                    first_symbols = _symbols(frame)
                    if natoms == 0:
                        raise AppError(INVALID_DATASET, "DeepMD export cannot write an empty frame")
                elif numbers.size != natoms:
                    raise AppError(
                        INVALID_DATASET,
                        "DeepMD export requires a constant atom count; use an extxyz (.xyz) destination instead",
                    )
                elif not np.array_equal(numbers, first_numbers):
                    raise AppError(
                        INVALID_DATASET,
                        "DeepMD export requires the same species and atom order in every frame;"
                        " use an extxyz (.xyz) destination instead",
                    )

                positions = np.asarray(frame.positions, dtype=np.float64).reshape(natoms, 3)
                cell = np.asarray(frame.cell, dtype=np.float64).reshape(3, 3)
                periodic = bool(np.asarray(frame.pbc).any())
                all_isolated = all_isolated and not periodic
                if not periodic:
                    cell = np.zeros((3, 3), dtype=np.float64)
                spool_files["coord"].write(positions.tobytes(order="C"))
                spool_files["box"].write(cell.tobytes(order="C"))

                for name, value, shape in (
                    ("energy", frame.energy, (1,)),
                    ("force", frame.forces, (natoms, 3)),
                    ("virial", frame.virial, (3, 3)),
                ):
                    if value is None:
                        complete[name] = False
                        continue
                    seen[name] = True
                    spool_files[name].write(
                        np.asarray(value, dtype=np.float64).reshape(shape).tobytes(order="C")
                    )
                count += 1

        if count == 0:
            raise AppError(INVALID_DATASET, "nothing to write: the view contains no frames")
        assert first_numbers is not None
        names = sorted(set(first_symbols))
        type_of = {name: index for index, name in enumerate(names)}
        (path / "type_map.raw").write_text(" ".join(names) + "\n", encoding="utf-8")
        (path / "type.raw").write_text(
            " ".join(str(type_of[symbol]) for symbol in first_symbols) + "\n",
            encoding="utf-8",
        )
        if all_isolated:
            # DeepMD's nopbc convention is per-system (all frames isolated).
            (path / "nopbc").write_text("", encoding="utf-8")
        set_dir = path / "set.000"
        set_dir.mkdir(parents=True, exist_ok=True)
        ensure_no_reparse_points(set_dir)
        _write_spool_npy(spool_paths["coord"], set_dir / "coord.npy", (count, natoms, 3))
        _write_spool_npy(spool_paths["box"], set_dir / "box.npy", (count, 3, 3))
        for name, shape in (
            ("energy", (count,)),
            ("force", (count, natoms, 3)),
            ("virial", (count, 3, 3)),
        ):
            if seen[name] and complete[name]:
                _write_spool_npy(spool_paths[name], set_dir / f"{name}.npy", shape)
        return count


__all__ = ["write_deepmd", "write_extxyz"]
