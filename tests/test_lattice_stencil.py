"""Periodic-image stencil bounds shared by the viewer, the health pass and analysis.

The bound is on the lattice coefficients of a Cartesian displacement, so it
comes from the *columns* of the inverse cell. Each test here exists because a
caller had its own copy of that math: the analysis neighbour graph had no size
bound at all (a degenerate cell asked for ~1.8e11 images, which freezes an
uncancelable job slot), the short-contact stencil used the rows and skipped the
image contact it was scanning for, and the ghost builder sized its distance
block by candidate atoms alone.
"""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis.algorithms._common import _local_neighbor_graph
from mdescriptor_studio_backend.analysis.models import AtomDescriptorMatrix
from mdescriptor_studio_backend.datasets import native as native_mod
from mdescriptor_studio_backend.datasets import statistics as st
from mdescriptor_studio_backend.datasets.covalent_radii import _ARRAY as RADII
from mdescriptor_studio_backend.datasets.ghosts import periodic_boundary_ghosts
from mdescriptor_studio_backend.errors import ANALYSIS_INPUT_INVALID, AppError
from mdescriptor_studio_backend.lattice import MAX_IMAGES_PER_AXIS, image_shift_limits

# Two Si 1.2422 Å apart, but only through the lattice translation (-2, 1, 0):
# a skewed cell, so a row-norm bound under-reads axis 0 and misses the contact.
SKEW_CELL = np.array([[10.0, 0.0, 0.0], [14.0, 1.0, 0.0], [0.0, 0.0, 6.0]])
SKEW_POSITIONS = np.array([[0.0, 0.0, 0.0], [0.5263, 0.0, 0.0]]) @ SKEW_CELL
SKEW_NUMBERS = np.array([14, 14])
PBC3 = np.array([True, True, True])


def _cubic(side: float) -> np.ndarray:
    return np.eye(3) * side


def test_min_distance_does_not_depend_on_the_query_block_size(monkeypatch: pytest.MonkeyPatch) -> None:
    """The image-shift hunt chunks its query to bound a (shifts x atoms x 3)
    temporary, which is gigabytes on a million-atom frame if issued in one shot.
    Chunking may only change when the minimum is found, never what it is."""
    rng = np.random.default_rng(3)
    positions = rng.random((80, 3)) @ SKEW_CELL
    cell, pbc = SKEW_CELL, PBC3

    whole = st._frame_min_distance(positions, cell, pbc)
    assert np.isfinite(whole) and whole > 0.0

    # One shift per query: the widest possible disagreement with the fused call.
    monkeypatch.setattr(st, "_MIN_DISTANCE_QUERY_ROWS", 1)
    assert st._frame_min_distance(positions, cell, pbc) == pytest.approx(whole, rel=1e-12)


def test_limits_widen_the_axis_the_rows_would_miss():
    limits = image_shift_limits(SKEW_CELL, 1.554)
    assert limits == [4, 3, 2]
    inverse = np.linalg.inv(SKEW_CELL)
    rows = [int(1.554 * np.linalg.norm(inverse[axis, :])) + 1 for axis in range(3)]
    assert limits[0] > rows[0]  # the short contact lives on axis 0


def test_limits_refuse_degenerate_and_unusable_cells():
    # |det| = 2.5e-5 clears any reasonable singularity gate, yet the exact
    # bound reaches 3e6 images per axis.
    near_degenerate = np.array([[10.0, 0, 0], [9.999999, 1e-6, 0], [0, 0, 5.0]])
    assert image_shift_limits(near_degenerate, 3.0) is None
    assert image_shift_limits(np.zeros((3, 3)), 3.0) is None
    assert image_shift_limits(_cubic(10.0), 0.0) is None
    assert image_shift_limits(_cubic(10.0), float("nan")) is None
    assert image_shift_limits(np.full((3, 3), np.inf), 3.0) is None
    assert image_shift_limits(np.ones((2, 2)), 3.0) is None


def test_limits_keep_realistic_cells_periodic():
    # 6 Å of neighbourhood in a 2.5 Å cell genuinely spans several images on
    # every axis and must still be enumerated, not refused.
    limits = image_shift_limits(_cubic(2.5), 6.0)
    assert limits is not None and all(2 <= value <= MAX_IMAGES_PER_AXIS for value in limits)
    assert image_shift_limits(_cubic(10.0), 6.0) == [2, 2, 2]
    # A non-periodic axis is never asked for images.
    assert image_shift_limits(_cubic(1.0), 3.0, axes=(2,)) == [4]


def _atom_samples(cells: np.ndarray, pbc: np.ndarray) -> AtomDescriptorMatrix:
    positions = SKEW_POSITIONS.copy()
    return AtomDescriptorMatrix(
        values=np.random.default_rng(0).normal(size=(2, 3)),
        frame=np.zeros(2, dtype=np.int64),
        row=np.arange(2, dtype=np.int64),
        sample_ids=["atom:0", "atom:1"],
        elements=SKEW_NUMBERS.copy(),
        positions=positions,
        cells=np.repeat(cells[None, :, :], 2, axis=0),
        pbc=np.repeat(pbc[None, :], 2, axis=0),
    )


def test_neighbor_graph_refuses_an_unbounded_stencil():
    samples = _atom_samples(np.diag([10.0, 10.0, 1e-6]), PBC3)
    with pytest.raises(AppError) as raised:
        _local_neighbor_graph(samples, 3.0, 128)
    assert raised.value.code == ANALYSIS_INPUT_INVALID
    assert "too narrow" in raised.value.message


def test_neighbor_graph_allows_a_skewed_but_valid_cell():
    # This cell is legitimate: its shortest lattice translation is 6 Å, and the
    # conservative bound asks for 13 x 9 x 5 images. A per-axis-only rule as
    # tight as the ghost renderer's would have refused it (axis 0 needs 6).
    limits = image_shift_limits(
        SKEW_CELL, 3.0, max_per_axis=8, max_total_images=1024
    )
    assert limits == [6, 4, 2]
    assert image_shift_limits(SKEW_CELL, 3.0) is None
    coordination, offsets, indices, distances, warnings = _local_neighbor_graph(
        _atom_samples(SKEW_CELL, PBC3), 3.0, 128
    )
    assert warnings == []
    assert int(coordination.sum()) > 0
    assert int(offsets[-1]) == len(indices) == len(distances)


def test_short_contact_finds_the_image_contact_the_row_bound_missed():
    min_distance = st._frame_min_distance(SKEW_POSITIONS, SKEW_CELL, PBC3)
    assert min_distance == pytest.approx(1.2422, abs=1e-3)
    assert st._frame_short_contact(
        SKEW_POSITIONS, SKEW_NUMBERS, SKEW_CELL, PBC3, min_distance
    )


def test_native_kernel_agrees_on_the_skewed_frame():
    if not native_mod.native_available():
        pytest.skip("native kernel not built")
    distance, short = native_mod.frame_geometry(
        SKEW_POSITIONS,
        SKEW_NUMBERS,
        SKEW_CELL,
        PBC3,
        RADII,
        st.SHORT_CONTACT_COEFFICIENT,
        st._CELL_DET_TOL,
        st._MIN_DISTANCE_IMAGE_LIMIT,
    )
    assert short is True
    assert distance == pytest.approx(1.2422, abs=1e-3)


def test_ghost_block_budget_covers_the_image_count():
    # A cutoff spanning more than one cell multiplies the block by every image
    # per axis; the old budget divided by the atom count alone and let one
    # block build a (candidates x shifts) x atoms matrix in the gigabytes.
    rng = np.random.default_rng(3)
    positions = rng.uniform(0, 10, size=(64, 3))
    ghosts = periodic_boundary_ghosts(["Si"] * 64, positions, _cubic(10.0), cutoff=9.0)
    assert ghosts
    for element, shifted, parent in ghosts:
        assert element == "Si"
        assert 0 <= parent < 64
        assert np.isfinite(shifted).all()
