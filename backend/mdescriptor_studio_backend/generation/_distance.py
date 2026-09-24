"""Shared blocked Euclidean-distance kernels.

Lifted from ``analysis/sampling/fps.py`` so farthest-point sampling, archive
novelty queries (and later generation optimizers) all run the same
precision-critical distance code. The module deliberately knows nothing
about ASE, the GUI, or any descriptor implementation: it only ever sees
plain 2-D float64 matrices.

Precision: distances are computed as explicit coordinate differences,
never via the expanded identity |x|² − 2x·p + |p|². The expanded form
spends the float64 mantissa on the common magnitude and gives away
exactly the low-order bits that separate near-duplicate structures —
which is what decides the next FPS pick and duplicate detection.
Measured on 400 x 96 features offset by 1e6 with 1e-3 spread: median
relative error 1.0, wrong argmax, no agreement with the true top-20
ranking. ``tests/test_fps_sampling.py`` pins this as an invariant.
"""

from __future__ import annotations

import numpy as np

from ..analysis.algorithms._common import _safe_import

# Query/reference block side: each Gram block costs block² floats (~33 MB in
# float64), keeping the N×M cross-distance pass memory-bounded without
# pinning a SciPy dependency on callers.
_BLOCK = 2048


def sqdist_to_point(x: np.ndarray, point: np.ndarray) -> np.ndarray:
    """Squared distance from every row of ``x`` to one point, in blocks."""
    out = np.empty(x.shape[0], dtype=np.float64)
    for start in range(0, x.shape[0], _BLOCK):
        stop = min(start + _BLOCK, x.shape[0])
        delta = x[start:stop] - point
        out[start:stop] = np.einsum("ij,ij->i", delta, delta)
    return out


def min_sqdist_to_set(x: np.ndarray, existing: np.ndarray, *, block_size: int = _BLOCK) -> np.ndarray:
    """Row-wise minimum squared distance from each row of ``x`` to the set ``existing``.

    Runs in blocks of ``block_size`` rows/columns; a full N×M distance
    matrix is never materialised. scipy subtracts coordinates directly,
    so an identical row scores exactly zero.
    """
    cdist = _safe_import("scipy.spatial.distance", "scipy").cdist
    d2 = np.full(x.shape[0], np.inf, dtype=np.float64)
    for ref_start in range(0, existing.shape[0], block_size):
        ref = existing[ref_start : ref_start + block_size]
        for cand_start in range(0, x.shape[0], block_size):
            cand_slice = slice(cand_start, cand_start + block_size)
            block = cdist(x[cand_slice], ref, metric="sqeuclidean")
            np.minimum(d2[cand_slice], block.min(axis=1), out=d2[cand_slice])
    return d2


def min_distance_to_reference(
    query: np.ndarray,
    reference: np.ndarray,
    *,
    block_size: int = _BLOCK,
) -> np.ndarray:
    """Nearest true Euclidean distance of every query row to the reference set.

    The public entry point for archive novelty scoring: novelty of a
    candidate is its distance to the closest archived point, and coverage
    radius is the max of these over a candidate pool.
    """
    query = np.asarray(query, dtype=np.float64)
    reference = np.asarray(reference, dtype=np.float64)
    if query.ndim != 2 or reference.ndim != 2 or query.shape[1] != reference.shape[1]:
        raise ValueError("query and reference must be 2D matrices with matching feature width")
    if reference.shape[0] == 0:
        return np.full(query.shape[0], np.inf, dtype=np.float64)
    return np.sqrt(np.clip(min_sqdist_to_set(query, reference, block_size=block_size), 0.0, None))
