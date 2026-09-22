"""Unit tests for periodic_boundary_ghosts (Explore page boundary images)."""

from pathlib import Path

import numpy as np

from mdescriptor_studio_backend.datasets.ghosts import periodic_boundary_ghosts


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
    got = sorted(tuple(np.round(p, 3)) for _, p, _parent in ghosts)
    assert got == [(-0.5, 5.0, 5.0), (10.5, 5.0, 5.0)]
    # B (index 1) mirrors to the left of the cell, A (index 0) to the right
    assert {parent for _, _, parent in ghosts} == {0, 1}


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
    got = np.array([p for _, p, _parent in ghosts])
    for expected in [(5.2, 5.2, 2.5), (-0.1, -0.1, 2.5)]:
        assert np.linalg.norm(got - np.array(expected), axis=1).min() < 1e-6


def test_partial_periodicity_only_generates_images_on_periodic_axes() -> None:
    pos = np.array([[0.2, 2.5, 2.5], [4.9, 2.5, 2.5]])
    ghosts = periodic_boundary_ghosts(
        ["C", "C"], pos, _cubic(5.0), pbc=np.array([True, False, False])
    )
    assert len(ghosts) == 2
    assert all(np.allclose(point[1:], [2.5, 2.5]) for _symbol, point, _parent in ghosts)


def test_cutoff_spanning_multiple_cells_keeps_all_nearby_images() -> None:
    ghosts = periodic_boundary_ghosts(["C"], np.zeros((1, 3)), _cubic(1.0), cutoff=2.1)
    distances = np.asarray([np.linalg.norm(position) for _, position, _parent in ghosts])
    # Integer lattice vectors with norm <= 2.1: 6 faces + 12 edges + 8
    # corners + 6 second-nearest axial images.
    assert len(ghosts) == 32
    assert np.all(distances <= 2.1 + 1e-9)
    assert np.all(distances > 1e-6)
    # single-atom frame: every image mirrors atom 0
    assert all(parent == 0 for _, _, parent in ghosts)


def test_every_ghost_is_bonded_to_a_displayed_atom() -> None:
    """Invariant: kept images sit within cutoff of some real atom (vesta-style
    boundary padding without detached duplicates)."""
    rng = np.random.default_rng(7)
    cell = _cubic(9.97)
    pos = rng.uniform(0.0, 9.97, size=(64, 3))
    for _, g, _parent in periodic_boundary_ghosts(["C"] * 64, pos, cell):
        assert np.linalg.norm(pos - g, axis=1).min() <= 2.4 + 1e-6


def test_ghost_parent_indices_map_back_to_real_atoms() -> None:
    """Each image must carry the index of the real atom it mirrors, so the GUI
    can map a click on a boundary image back to the real atom (the mirrored
    position is the parent shifted by an integer lattice vector)."""
    rng = np.random.default_rng(11)
    length = 8.0
    cell = _cubic(length)
    pos = rng.uniform(0.0, length, size=(48, 3))
    for _el, p, parent in periodic_boundary_ghosts(["C"] * 48, pos, cell, cutoff=3.2):
        assert 0 <= parent < len(pos)
        displacement = (np.asarray(p) - pos[parent]) / length
        assert np.allclose(displacement, np.round(displacement), atol=1e-6)


def test_empty_and_degenerate_inputs() -> None:
    assert periodic_boundary_ghosts([], np.zeros((0, 3)), _cubic(5.0)) == []
    # singular cell has no well-defined images
    assert periodic_boundary_ghosts(["C"], np.array([[0.0, 0.0, 0.0]]), np.zeros((3, 3))) == []


def test_degenerate_axis_yields_no_images_instead_of_freezing() -> None:
    """A cell vector short of a fraction of the cutoff needs ~2.4 billion image
    shifts on that axis. The enumeration must give up, not run into the next
    century holding an RPC worker thread."""
    cell = np.diag([10.0, 10.0, 1e-9])
    pos = np.array([[1.0, 1.0, 0.0], [9.0, 9.0, 0.0]])
    assert periodic_boundary_ghosts(["O", "H"], pos, cell) == []
