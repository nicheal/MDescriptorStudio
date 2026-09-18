"""Generate test fixtures and the synthetic performance dataset (ADR-9).

Usage:
    .venv/Scripts/python.exe scripts/make_fixtures.py [--out tests/data] [--perf-frames 12480]

Outputs:
    tests/data/deepmd_small/      ~10 frames DeepMD raw (Ga32As32)
    tests/data/extxyz_small.xyz   ~10 frames extxyz
    tests/data/perf_deepmd/       N frames DeepMD raw (default 12480, 64 atoms/frame)
    tests/data/perf_extxyz.xyz    N frames extxyz

ADR-9 keeps the generator in git and the performance fixtures reproducible on
demand, so the perf_* outputs stay local instead of costing 87 MB per checkout.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

SYMBOLS = ["Ga", "As"]
Z = [31, 33]
LATTICE = 9.97  # ~GaAs conventional a=5.653 * sqrt(3) cell edge for 64-atom cell


def make_frames(n_frames: int, natoms: int = 64, seed: int = 7):
    rng = np.random.default_rng(seed)
    types = np.array([i % 2 for i in range(natoms)], dtype=np.int64)  # alternating Ga/As
    numbers = np.array(Z, dtype=np.int64)[types]
    base = _lattice_positions(natoms)
    box = np.eye(3, dtype=np.float64) * LATTICE
    frames = []
    for i in range(n_frames):
        jitter = rng.normal(0, 0.08, size=(natoms, 3))
        positions = (base + jitter) % LATTICE
        energy = float(-4.28 * natoms / 2 + rng.normal(0, 1.5))  # ~ -4.28 eV/atom
        forces = rng.normal(0, 0.12, size=(natoms, 3))
        virial = rng.normal(0, 0.5, size=9).astype(np.float64)
        frames.append((positions, energy, forces, virial))
    return types, numbers, box, frames


def _lattice_positions(natoms: int) -> np.ndarray:
    """Zincblende-like 64-atom arrangement (2-atom basis on 4x4x4 fcc-ish grid)."""
    coords = []
    step = LATTICE / 4
    for i in range(4):
        for j in range(4):
            for k in range(4):
                coords.append([i * step, j * step, k * step])
                coords.append([i * step + step / 2] * 1 + [j * step + step / 2, k * step + step / 2])
        if len(coords) >= natoms:
            break
    return np.array(coords[:natoms], dtype=np.float64)


def write_deepmd(out_dir: Path, n_frames: int, natoms: int = 64, seed: int = 7) -> None:
    """Standard DeepMD npy layout (dpdata deepmd/npy semantics): type.raw +
    type_map.raw at the root, all arrays under set.000/."""
    out_dir.mkdir(parents=True, exist_ok=True)
    types, _, box, frames = make_frames(n_frames, natoms, seed)
    (out_dir / "type_map.raw").write_text(" ".join(SYMBOLS), encoding="utf-8")
    np.savetxt(out_dir / "type.raw", types, fmt="%d")
    set_dir = out_dir / "set.000"
    set_dir.mkdir(parents=True, exist_ok=True)
    coords = np.stack([f[0].reshape(-1) for f in frames])
    boxes = np.stack([box.reshape(-1) for _ in frames])
    energies = np.array([f[1] for f in frames], dtype=np.float64)
    forces = np.stack([f[2].reshape(-1) for f in frames])
    virials = np.stack([f[3] for f in frames])
    np.save(set_dir / "coord.npy", coords)
    np.save(set_dir / "box.npy", boxes)
    np.save(set_dir / "energy.npy", energies)
    np.save(set_dir / "force.npy", forces)
    np.save(set_dir / "virial.npy", virials)
    print(f"wrote {out_dir} ({n_frames} frames)")


def write_extxyz(out_path: Path, n_frames: int, natoms: int = 64, seed: int = 7) -> None:
    _, numbers, box, frames = make_frames(n_frames, natoms, seed)
    lat = " ".join(f"{v:.6f}" for v in box.reshape(-1))
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for i, (positions, energy, forces, virial) in enumerate(frames):
            f.write(f"{natoms}\n")
            f.write(
                f'Lattice="{lat}" Properties=species:S:1:pos:R:3:forces:R:3 '
                f'energy={energy:.8f} virial="{" ".join(f"{v:.6f}" for v in virial)}" '
                f"frame_index={i}\n"
            )
            for z, pos, force in zip(numbers, positions, forces):
                sym = SYMBOLS[0] if z == 31 else SYMBOLS[1]
                f.write(
                    f"{sym} {pos[0]:.6f} {pos[1]:.6f} {pos[2]:.6f} "
                    f"{force[0]:.6f} {force[1]:.6f} {force[2]:.6f}\n"
                )
    print(f"wrote {out_path} ({n_frames} frames)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tests/data")
    ap.add_argument("--perf-frames", type=int, default=12480)
    ap.add_argument("--skip-perf", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    write_deepmd(out / "deepmd_small", 10, 64, seed=11)
    write_extxyz(out / "extxyz_small.xyz", 10, 64, seed=12)
    if not args.skip_perf:
        write_deepmd(out / "perf_deepmd", args.perf_frames, 64, seed=7)
        write_extxyz(out / "perf_extxyz.xyz", args.perf_frames, 64, seed=7)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
