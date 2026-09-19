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
from pathlib import Path

import numpy as np

from ..errors import AppError, INVALID_DATASET
from ..security import ensure_no_reparse_points, open_text_for_write
from .deepmd_symbols import _Z_TO_SYMBOL

# Root files that make a directory a DeepMD system (plus any set.* directory).
_DEEPMD_ROOT_FILES = ("type.raw", "type_map.raw", "nopbc")


def _symbols(frame) -> list[str]:
    return [_Z_TO_SYMBOL.get(int(z), f"Z{int(z)}") for z in np.asarray(frame.numbers, dtype=np.int64)]


def write_extxyz(path: Path, frames: Iterable) -> int:
    """Write frames to an extended-xyz file; returns the frame count."""
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
    frame_list = list(frames)
    if not frame_list:
        raise AppError(INVALID_DATASET, "nothing to write: the view contains no frames")
    natoms = int(np.asarray(frame_list[0].numbers).size)
    if any(int(np.asarray(f.numbers).size) != natoms for f in frame_list):
        raise AppError(
            INVALID_DATASET,
            "DeepMD export requires a constant atom count; use an extxyz (.xyz) destination instead",
        )
    names = sorted({symbol for frame in frame_list for symbol in _symbols(frame)})
    type_of = {name: index for index, name in enumerate(names)}
    (path / "type_map.raw").write_text(" ".join(names) + "\n", encoding="utf-8")
    (path / "type.raw").write_text(
        " ".join(str(type_of[symbol]) for symbol in _symbols(frame_list[0])) + "\n",
        encoding="utf-8",
    )
    if all(not bool(np.asarray(frame.pbc).any()) for frame in frame_list):
        # dpdata's nopbc convention is per-system (all frames isolated).
        (path / "nopbc").write_text("", encoding="utf-8")
    coords = [np.asarray(f.positions, dtype=np.float64).reshape(natoms, 3) for f in frame_list]
    # Zero boxes for isolated frames keep box.npy present: dpdata only skips
    # loading it when the root nopbc marker exists.
    cells = [
        np.asarray(f.cell, dtype=np.float64).reshape(3, 3) if bool(np.asarray(f.pbc).any()) else np.zeros((3, 3))
        for f in frame_list
    ]
    set_dir = path / "set.000"
    set_dir.mkdir(parents=True, exist_ok=True)
    ensure_no_reparse_points(set_dir)
    np.save(set_dir / "coord.npy", np.asarray(coords), allow_pickle=False)
    np.save(set_dir / "box.npy", np.asarray(cells), allow_pickle=False)
    for name, values, shape in (
        ("energy.npy", [f.energy for f in frame_list], (len(frame_list),)),
        ("force.npy", [f.forces for f in frame_list], (len(frame_list), natoms, 3)),
        ("virial.npy", [f.virial for f in frame_list], (len(frame_list), 3, 3)),
    ):
        if any(value is None for value in values):
            continue
        np.save(set_dir / name, np.asarray(values, dtype=np.float64).reshape(shape), allow_pickle=False)
    return len(frame_list)


__all__ = ["write_deepmd", "write_extxyz"]
