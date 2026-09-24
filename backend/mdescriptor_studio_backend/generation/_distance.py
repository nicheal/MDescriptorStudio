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

from concurrent.futures import ThreadPoolExecutor

import numpy as np

from ..analysis.algorithms._common import _safe_import

# Query/reference block side: each Gram block costs block² floats (~33 MB in
# float64), keeping the N×M cross-distance pass memory-bounded without
# pinning a SciPy dependency on callers.
_BLOCK = 2048
_MAX_PARALLEL_SCRATCH_BYTES = 256 * 1024 * 1024


def sqdist_to_point(x: np.ndarray, point: np.ndarray) -> np.ndarray:
    """Squared distance from every row of ``x`` to one point, in blocks."""
    out = np.empty(x.shape[0], dtype=np.float64)
    for start in range(0, x.shape[0], _BLOCK):
        stop = min(start + _BLOCK, x.shape[0])
        delta = x[start:stop] - point
        out[start:stop] = np.einsum("ij,ij->i", delta, delta)
    return out


def min_sqdist_to_set(
    x: np.ndarray,
    existing: np.ndarray,
    *,
    block_size: int = _BLOCK,
    workers: int = 1,
) -> np.ndarray:
    """Row-wise minimum squared distance from each row of ``x`` to the set ``existing``.

    Runs in blocks of ``block_size`` rows/columns; a full N×M distance
    matrix is never materialised. scipy subtracts coordinates directly,
    so an identical row scores exactly zero.
    """
    cdist = _safe_import("scipy.spatial.distance", "scipy").cdist

    if x.shape[0] == 0:
        return np.empty(0, dtype=np.float64)

    requested_workers = max(1, int(workers))
    # Split small query batches across workers without letting concurrent
    # cdist blocks exceed the shared scratch-memory budget.
    query_block_size = min(
        block_size,
        max(1, (x.shape[0] + requested_workers - 1) // requested_workers),
        max(1, _MAX_PARALLEL_SCRATCH_BYTES // (requested_workers * block_size * 8)),
    )

    def _query_chunk(cand_start: int) -> tuple[int, np.ndarray]:
        cand = x[cand_start : cand_start + query_block_size]
        nearest = np.full(cand.shape[0], np.inf, dtype=np.float64)
        for ref_start in range(0, existing.shape[0], block_size):
            ref = existing[ref_start : ref_start + block_size]
            block = cdist(cand, ref, metric="sqeuclidean")
            np.minimum(nearest, block.min(axis=1), out=nearest)
        return cand_start, nearest

    starts = range(0, x.shape[0], query_block_size)
    chunk_count = (x.shape[0] + query_block_size - 1) // query_block_size
    worker_count = min(requested_workers, chunk_count)
    d2 = np.full(x.shape[0], np.inf, dtype=np.float64)
    if worker_count == 1:
        results = map(_query_chunk, starts)
        for start, nearest in results:
            d2[start : start + nearest.size] = nearest
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            for start, nearest in pool.map(_query_chunk, starts):
                d2[start : start + nearest.size] = nearest
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
