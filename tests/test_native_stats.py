"""Native statistics geometry kernel: parity with the scipy reference.

These tests keep the compiled kernel (datasets/_native) honest against the
scipy implementation in datasets/statistics.py, which stays the semantic
source of truth.  Without the DLL everything skips — the backend then runs
the fallback path anyway.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from mdescriptor_studio_backend.datasets import statistics as st  # noqa: E402
from mdescriptor_studio_backend.datasets import native as native_mod  # noqa: E402
from mdescriptor_studio_backend.datasets.covalent_radii import (  # noqa: E402
    _ARRAY as RADII,
)

pytestmark = pytest.mark.skipif(
    not native_mod.native_available(), reason="native kernel not built"
)


def _geometry(frame) -> tuple[float | None, bool]:
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


def _reference(frame) -> tuple[float | None, bool]:
    min_d = st._frame_min_distance(frame["positions"], frame["cell"], frame["pbc"])
    return min_d, st._frame_short_contact(
        frame["positions"], frame["numbers"], frame["cell"], frame["pbc"], min_d
    )


CASES = {
    "bulk_periodic": lambda rng: dict(
        positions=rng.uniform(0, 10, (64, 3)) * 1.0,
        numbers=rng.integers(11, 30, 64),
        cell=np.diag([10.0, 10.0, 10.0]),
        pbc=[True, True, True],
    ),
    "skewed_cell": lambda rng: dict(
        positions=rng.uniform(0, 8, (40, 3)),
        numbers=rng.integers(1, 97, 40),
        cell=np.array([[6.0, 0, 0], [2.5, 6.0, 0], [1.0, 1.5, 6.0]]),
        pbc=[True, True, True],
    ),
    "molecule": lambda rng: dict(
        positions=rng.uniform(-3, 3, (12, 3)),
        numbers=rng.integers(1, 9, 12),
        cell=np.zeros((3, 3)),
        pbc=[False, False, False],
    ),
    "single_atom_periodic": lambda rng: dict(
        positions=np.array([[0.1, 0.2, 0.3]]),
        numbers=np.array([8]),
        cell=np.diag([3.0, 4.0, 5.0]),
        pbc=[True, True, True],
    ),
    "duplicates": lambda rng: dict(
        positions=np.array([[0.0, 0, 0], [0.0, 0, 0], [5.0, 5, 5]]),
        numbers=np.array([26, 26, 26]),
        cell=np.diag([12.0, 12.0, 12.0]),
        pbc=[True, True, True],
    ),
    "short_contact_image": lambda rng: dict(
        # single Li atom in a 1.4 A cubic cell: nearest own image at 1.4 A,
        # under 0.7 * (2.03 + 2.03) — flagged via an image shift only
        positions=np.array([[0.0, 0, 0]]),
        numbers=np.array([3]),
        cell=np.diag([1.4, 1.4, 1.4]),
        pbc=[True, True, True],
    ),
    "nan_positions": lambda rng: dict(
        positions=np.array([[np.nan, 0, 0], [1.0, 1, 1]]),
        numbers=np.array([14, 14]),
        cell=np.diag([5.0, 5.0, 5.0]),
        pbc=[True, True, True],
    ),
    "degenerate_cell": lambda rng: dict(
        positions=np.array([[0.0, 0, 0], [1.5, 0, 0]]),
        numbers=np.array([14, 14]),
        cell=np.diag([5.0, 5.0, 0.0]),
        pbc=[True, True, True],
    ),
}


@pytest.mark.parametrize("name", list(CASES))
def test_frame_geometry_matches_reference(name: str) -> None:
    rng = np.random.default_rng(abs(hash(name)) % (2**32))
    frame = {
        key: np.asarray(value) for key, value in CASES[name](rng).items()
    }
    for key, value in frame.items():
        frame[key] = np.ascontiguousarray(value)
    exp_min, exp_short = _reference(frame)
    got_min, got_short = _geometry(frame)
    assert got_short == exp_short
    assert (got_min is None) == (exp_min is None)
    if exp_min is not None and got_min is not None:
        assert got_min == pytest.approx(exp_min, abs=1e-9)


def test_grid_path_matches_reference() -> None:
    """Frames above the brute-force limit exercise the grid search."""
    rng = np.random.default_rng(42)
    n = 900
    cell = np.diag([30.0, 30.0, 30.0])
    positions = rng.uniform(0, 30, (n, 3))
    positions[7] = positions[3] + 0.35  # force a close pair
    numbers = rng.choice(np.array([13, 26, 79]), n)
    frame = {
        "positions": np.ascontiguousarray(positions),
        "numbers": np.ascontiguousarray(numbers),
        "cell": cell,
        "pbc": np.array([True, True, True]),
    }
    exp_min, exp_short = _reference(frame)
    got_min, got_short = _geometry(frame)
    assert got_short == exp_short
    assert got_min == pytest.approx(exp_min, abs=1e-9)


def test_compute_statistics_identical_without_native(monkeypatch, tmp_path: Path) -> None:
    """compute_statistics produces identical output on the fallback path."""
    from mdescriptor_studio_backend.datasets import compute_statistics

    lines = []
    rng = np.random.default_rng(9)
    for _ in range(6):
        positions = rng.uniform(0, 8, (16, 3))
        pos_rows = "\n".join(
            f"Si {x:.6f} {y:.6f} {z:.6f}" for x, y, z in positions
        )
        lines.append(
            f"16\nLattice=\"8.0 0.0 0.0 0.0 8.0 0.0 0.0 0.0 8.0\" "
            f"energy=-40.0\n{pos_rows}\n"
        )
    xyz = tmp_path / "sample.xyz"
    xyz.write_text("".join(lines), encoding="utf-8")

    from mdescriptor_studio_backend.datasets import create_adapter

    adapter = create_adapter(xyz)
    with_native = compute_statistics(adapter)

    monkeypatch.setattr(
        st._native, "frame_geometry", lambda *args, **kwargs: None
    )
    without_native = compute_statistics(create_adapter(xyz))
    assert with_native == without_native
