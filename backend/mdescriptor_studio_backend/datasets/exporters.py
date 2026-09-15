"""Writers for materialized dataset-view selections.

Both writers consume selected adapter frames and never touch the source files.
The extxyz writer mirrors the reader's
comment-line conventions (Lattice / Properties / energy / virial / pbc); the
DeepMD writer reproduces the raw npy layout the loader expects.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import numpy as np

from ..errors import AppError, INVALID_DATASET
from .deepmd_symbols import _Z_TO_SYMBOL


def write_extxyz(path: Path, frames: Iterable) -> int:
    """Write frames to an extended-xyz file; returns the frame count."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for frame in frames:
            symbols = [_Z_TO_SYMBOL.get(int(z), f"Z{z}") for z in frame.numbers]
            n = len(symbols)
            lattice = " ".join(f"{v:.6f}" for v in np.asarray(frame.cell, dtype=np.float64).reshape(-1))
            pbc = " ".join("T" if bool(v) else "F" for v in frame.pbc)
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
                if forces is not None:
                    fx, fy, fz = forces[i]
                    fh.write(
                        f"{sym} {pos[0]:.8f} {pos[1]:.8f} {pos[2]:.8f}"
                        f" {fx:.8f} {fy:.8f} {fz:.8f}\n"
                    )
                else:
                    fh.write(f"{sym} {pos[0]:.8f} {pos[1]:.8f} {pos[2]:.8f}\n")
            written += 1
    return written


def write_deepmd(path: Path, frames: Iterable) -> int:
    """Write frames as a DeepMD raw directory (type.raw + set.000/*.npy).

    DeepMD systems require one atom count per set; frames with differing
    atom counts (possible for extxyz-style sources) are rejected up front.
    Frames without energies are tolerated by omitting energy.npy, matching
    how the loader treats a missing file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(parents=True, exist_ok=True)
    frame_list = list(frames)
    if not frame_list:
        raise AppError(INVALID_DATASET, "nothing to write: the view contains no frames")
    natoms = int(frame_list[0].numbers.size)
    if any(int(f.numbers.size) != natoms for f in frame_list):
        raise AppError(
            INVALID_DATASET,
            "DeepMD export requires a constant atom count; use an extxyz (.xyz) destination instead",
        )
    z_symbols = sorted({_Z_TO_SYMBOL.get(int(z), f"Z{z}") for f in frame_list for z in f.numbers})
    symbol_to_type = {s: i for i, s in enumerate(z_symbols)}
    coords, boxes, energies, forces, virials = [], [], [], [], []
    has_energy = has_force = has_virial = False
    for frame in frame_list:
        coords.append(np.asarray(frame.positions, dtype=np.float64).reshape(natoms, 3))
        cell = np.asarray(frame.cell, dtype=np.float64).reshape(3, 3)
        boxes.append(cell if bool(np.asarray(frame.pbc).any()) else np.zeros((3, 3)))
        if frame.energy is not None:
            energies.append(float(frame.energy))
            has_energy = True
        if frame.forces is not None:
            forces.append(np.asarray(frame.forces, dtype=np.float64).reshape(natoms, 3))
            has_force = True
        if frame.virial is not None:
            virials.append(np.asarray(frame.virial, dtype=np.float64).reshape(-1))
            has_virial = True
    type_map = " ".join(z_symbols)
    types = " ".join(
        str(symbol_to_type[_Z_TO_SYMBOL.get(int(z), f"Z{z}")])
        for z in frame_list[0].numbers
    )
    (path / "type_map.raw").write_text(type_map + "\n", encoding="utf-8")
    (path / "type.raw").write_text(types + "\n", encoding="utf-8")
    if all(not bool(np.asarray(f.pbc).any()) for f in frame_list):
        # dpdata's nopbc convention is per-system (all frames isolated)
        (path / "nopbc").write_text("", encoding="utf-8")
    set_dir = path / "set.000"
    set_dir.mkdir(parents=True, exist_ok=True)
    np.save(set_dir / "coord.npy", np.asarray(coords, dtype=np.float64), allow_pickle=False)
    np.save(set_dir / "box.npy", np.asarray(boxes, dtype=np.float64), allow_pickle=False)
    if has_energy:
        np.save(set_dir / "energy.npy", np.asarray(energies, dtype=np.float64), allow_pickle=False)
    if has_force:
        np.save(set_dir / "force.npy", np.asarray(forces, dtype=np.float64), allow_pickle=False)
    if has_virial:
        # frames without a virial contribute NaN, the loader's missing marker
        data = np.full((len(frame_list), 9), np.nan, dtype=np.float64)
        for i, v in enumerate(virials):
            data[i] = v
        np.save(set_dir / "virials.npy", data, allow_pickle=False)
    return len(frame_list)
