"""Cross-check the native geometry kernel against the scipy reference.

Generates randomized frames across the interesting regimes (periodic cubic /
skewed / slab cells, molecules, single atoms, duplicate coordinates, short
contacts, large frames that exercise the grid path) and compares
(min_distance, short_contact) from both implementations per frame.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("NUMBA_DISABLE_JIT_CACHE", "1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np  # noqa: E402

from mdescriptor_studio_backend.datasets import native as native_mod  # noqa: E402
from mdescriptor_studio_backend.datasets import statistics as st  # noqa: E402
from mdescriptor_studio_backend.datasets.covalent_radii import (  # noqa: E402
    _ARRAY as RADII,
)

assert native_mod.native_available(), "native kernel failed to load"


def random_cell(rng: np.random.Generator, kind: str) -> np.ndarray:
    if kind == "cubic":
        return np.diag(rng.uniform(3.0, 25.0, 3))
    if kind == "skewed":
        a = np.eye(3) * rng.uniform(4.0, 15.0, 3)
        off = rng.uniform(-0.6, 0.6, (3, 3))
        np.fill_diagonal(off, 0.0)
        return a + off
    if kind == "slab":
        cell = np.diag([rng.uniform(8.0, 30.0), rng.uniform(8.0, 30.0), 1.0])
        cell[2, 2] = rng.uniform(0.4, 0.9)  # thin, non-periodic axis is separate
        return cell
    raise ValueError(kind)


def random_pbc(rng: np.random.Generator) -> np.ndarray:
    return rng.random(3) < 0.6


def make_frame(rng: np.random.Generator, index: int) -> dict:
    kind = ("cubic", "skewed", "slab")[index % 3]
    cell = random_cell(rng, kind)
    pbc = random_pbc(rng)
    big = index % 7 == 3  # exercise the grid path (n > 512)
    n = int(rng.integers(600, 1400)) if big else int(rng.integers(1, 80))
    positions = rng.uniform(-40.0, 40.0, (n, 3))
    if n >= 2 and rng.random() < 0.3:  # force a very close pair sometimes
        positions[1] = positions[0] + rng.uniform(0.0, 0.4, 3)
    if n >= 4 and rng.random() < 0.2:  # exact duplicates
        positions[3] = positions[2]
    numbers = rng.integers(1, 97, n)
    if big:  # a plausible bulk composition for the large frames
        numbers = rng.choice(np.array([11, 14, 22, 26, 79]), n)
    return {
        "positions": positions,
        "numbers": numbers.astype(np.int64),
        "cell": cell,
        "pbc": pbc,
    }


def reference(frame: dict) -> tuple[float | None, bool]:
    min_d = st._frame_min_distance(frame["positions"], frame["cell"], frame["pbc"])
    short = st._frame_short_contact(
        frame["positions"], frame["numbers"], frame["cell"], frame["pbc"], min_d
    )
    return min_d, short


def native(frame: dict) -> tuple[float | None, bool]:
    out = native_mod.frame_geometry(
        frame["positions"],
        frame["numbers"],
        frame["cell"],
        frame["pbc"],
        RADII,
        st.SHORT_CONTACT_COEFFICIENT,
        st._CELL_DET_TOL,
        st._MIN_DISTANCE_IMAGE_LIMIT,
    )
    assert out is not None
    return out


def main() -> int:
    rng = np.random.default_rng(20260906)
    failures = 0
    worst = 0.0
    total = 400
    for index in range(total):
        frame = make_frame(rng, index)
        exp_min, exp_short = reference(frame)
        got_min, got_short = native(frame)
        ok = exp_short == got_short
        if exp_min is None or got_min is None:
            ok = ok and (exp_min is None) == (got_min is None)
        else:
            diff = abs(exp_min - got_min)
            worst = max(worst, diff)
            ok = ok and diff <= 1e-9 * max(1.0, exp_min)
        if not ok:
            failures += 1
            print(
                f"MISMATCH frame {index}: min {exp_min!r} vs {got_min!r}, "
                f"short {exp_short} vs {got_short}"
            )
    # targeted edge cases
    edges = {
        "empty": (np.zeros((0, 3)), np.zeros(0, dtype=np.int64), np.zeros((3, 3)), [False] * 3),
        "nan_positions": (
            np.array([[np.nan, 0, 0], [1, 1, 1]]),
            np.array([1, 1]),
            np.eye(3) * 5,
            [True] * 3,
        ),
        "single_isolated": (np.array([[1.0, 2, 3]]), np.array([6]), np.zeros((3, 3)), [False] * 3),
        "nan_cell": (
            np.array([[0.0, 0, 0], [1.5, 0, 0]]),
            np.array([14, 14]),
            np.full((3, 3), np.nan),
            [True] * 3,
        ),
        "degenerate_cell": (
            np.array([[0.0, 0, 0], [1.5, 0, 0]]),
            np.array([14, 14]),
            np.diag([5.0, 5.0, 0.0]),
            [True] * 3,
        ),
        "single_atom_periodic": (
            np.array([[0.1, 0.2, 0.3]]),
            np.array([8]),
            np.diag([3.0, 4.0, 5.0]),
            [True] * 3,
        ),
        "extreme_z": (
            np.array([[0.0, 0, 0], [1.0, 0, 0]]),
            np.array([0, 500]),  # clamped radii lookup
            np.eye(3) * 10,
            [False] * 3,
        ),
        "own_image_contact": (  # cubic 2 A cell, single atom: d = sqrt(3)*2/... nearest image
            np.array([[0.0, 0, 0]]),
            np.array([6]),
            np.eye(3) * 2.0,
            [True] * 3,
        ),
    }
    for name, (positions, numbers, cell, pbc) in edges.items():
        exp_min, exp_short = reference(
            {
                "positions": np.asarray(positions, dtype=np.float64),
                "numbers": np.asarray(numbers, dtype=np.int64),
                "cell": np.asarray(cell, dtype=np.float64),
                "pbc": np.asarray(pbc, dtype=bool),
            }
        )
        got_min, got_short = native(
            {
                "positions": np.asarray(positions, dtype=np.float64),
                "numbers": np.asarray(numbers, dtype=np.int64),
                "cell": np.asarray(cell, dtype=np.float64),
                "pbc": np.asarray(pbc, dtype=bool),
            }
        )
        ok = exp_short == got_short and (exp_min is None) == (got_min is None)
        if exp_min is not None and got_min is not None:
            ok = ok and abs(exp_min - got_min) <= 1e-9 * max(1.0, exp_min)
        if not ok:
            failures += 1
            print(
                f"MISMATCH edge {name}: min {exp_min!r} vs {got_min!r}, "
                f"short {exp_short} vs {got_short}"
            )
    if failures == 0:
        print(f"OK: {total} random frames + {len(edges)} edge cases agree (worst |dmin| = {worst:.3e})")
        return 0
    print(f"FAILED: {failures} mismatches")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
