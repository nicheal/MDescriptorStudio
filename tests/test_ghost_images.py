"""Unit tests for periodic_boundary_ghosts (Explore page boundary images)."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from mdescriptor_studio_backend.services.dataset_service import periodic_boundary_ghosts


def _cubic(l: float) -> np.ndarray:
    return np.eye(3) * l


def test_ghost_lands_on_correct_side_of_boundary() -> None:
    """A—B bond across the boundary must be completed by images adjacent to
    the partners (B-L near A, A+L near B), not one full lattice vector out.
    Regression: the shift signs used to be inverted, producing detached
    ghosts at A-L / B+L and no bond completion."""
    pos = np.array([[0.5, 5.0, 5.0], [9.5, 5.0, 5.0]])  # min-image distance 1.0
    ghosts = periodic_boundary_ghosts(["C", "C"], pos, _cubic(10.0))
    assert len(ghosts) == 2
    got = sorted(tuple(np.round(p, 3)) for _, p in ghosts)
    assert got == [(-0.5, 5.0, 5.0), (10.5, 5.0, 5.0)]


def test_unwrapped_frame_yields_no_stray_ghosts() -> None:
    """deepmd frames are often centered on the origin (negative coordinates):
    no displayed atom pair straddles a face, so any image would be a stray —
    regression: 293 garbage ghosts at ~±100 Å on a real dpdata-C50Cl1 frame."""
    pos = np.array([[-1.0, 0.0, 0.0], [0.5, 0.3, 0.2], [-0.5, 1.2, 0.4], [2.0, -0.4, 1.1]])
    ghosts = periodic_boundary_ghosts(["C"] * 4, pos, _cubic(100.0))
    assert ghosts == []


def test_corner_image_across_two_faces() -> None:
    """A bond wrapping in two axes at once needs the diagonal image."""
    pos = np.array([[0.2, 0.2, 2.5], [4.9, 4.9, 2.5]])  # min-image ~0.42
    ghosts = periodic_boundary_ghosts(["C", "C"], pos, _cubic(5.0))
    got = np.array([p for _, p in ghosts])
    for expected in [(5.2, 5.2, 2.5), (-0.1, -0.1, 2.5)]:
        assert np.linalg.norm(got - np.array(expected), axis=1).min() < 1e-6


def test_every_ghost_is_bonded_to_a_displayed_atom() -> None:
    """Invariant: kept images sit within cutoff of some real atom (vesta-style
    boundary padding without detached duplicates)."""
    rng = np.random.default_rng(7)
    cell = _cubic(9.97)
    pos = rng.uniform(0.0, 9.97, size=(64, 3))
    for _, g in periodic_boundary_ghosts(["C"] * 64, pos, cell):
        assert np.linalg.norm(pos - g, axis=1).min() <= 2.4 + 1e-6


def test_empty_and_degenerate_inputs() -> None:
    assert periodic_boundary_ghosts([], np.zeros((0, 3)), _cubic(5.0)) == []
    # singular cell has no well-defined images
    assert periodic_boundary_ghosts(["C"], np.array([[0.0, 0.0, 0.0]]), np.zeros((3, 3))) == []
