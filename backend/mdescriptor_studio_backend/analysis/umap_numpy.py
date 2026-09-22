"""Pure numpy/scipy UMAP embedding for the Studio analysis engine.

Replaces umap-learn (and its numba/llvmlite/pynndescent chain) with a
self-contained implementation of the same algorithm:

1. deterministic k-nearest-neighbor search — chunked brute force for small
   inputs and a projected cKDTree candidate search for large descriptor sets,
2. the fuzzy simplicial set — smooth-kNN sigma calibration plus sparse
   symmetrization (the standard probabilistic-sum fuzzy union),
3. SGD layout optimization with negative sampling, vectorized per epoch with
   scatter aggregation instead of numba's per-edge compiled loop. Every edge
   contributes once per epoch (umap-learn visits edges on weight-dependent
   schedules); the weight stays in the force term, which preserves embedding
   quality while keeping the whole step branch-free.

The embedding is a projection aid, not a bit-for-bit clone of umap-learn:
coordinates match it in quality (trustworthiness) but not point for point.
What the contract does fix is reproducibility — PCA axes are sign-canonical
and every random choice (negative samples) derives from ``seed`` — so the
same inputs and parameters yield bitwise-identical coordinates on every run.

Compute runs in float32; coordinates are returned as float64 like every other
analysis array. The scipy imports stay inside ``fit_umap``: module import must
stay cheap for backend startup, and the first scipy import must happen on a
request thread after the analysis warmup gate so it cannot race the background
sklearn import for the Windows DLL loader.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

Progress = Callable[[float, str], None]

_NEGATIVE_SAMPLES_PER_EDGE = 5
_NEGATIVE_SAMPLE_CAP = 250_000  # per-epoch repulsive-sample budget on large graphs
_GAMMA = 1.0
_SPREAD = 1.0
_APPROX_KNN_THRESHOLD = 2_048
_APPROX_PROJECTION_DIM = 8
_APPROX_CANDIDATE_FACTOR = 8


def _tree_knn(
    x: np.ndarray,
    k: int,
    metric: str,
    progress: Progress | None,
) -> tuple[np.ndarray, np.ndarray]:
    """Find exact or candidate neighbours through a small cKDTree projection."""
    from scipy.spatial import cKDTree

    n, dimensions = x.shape
    if metric == "cosine":
        base = x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
    elif metric == "euclidean":
        base = x - x.mean(axis=0)
    else:
        base = x

    exact_tree = dimensions <= _APPROX_PROJECTION_DIM
    if exact_tree:
        projected = base
        tree_metric = 1 if metric == "manhattan" else 2
    else:
        # A fixed sign projection is cheap, reproducible, and keeps the tree's
        # dimensionality low. Original-space distances are recomputed for the
        # candidate set, so the projection only decides which rows to inspect.
        rng = np.random.default_rng(0x4D44434B + dimensions)
        signs = rng.choice(
            np.array((-1.0, 1.0), dtype=np.float32),
            size=(dimensions, _APPROX_PROJECTION_DIM),
        )
        projected = base @ (signs / np.sqrt(_APPROX_PROJECTION_DIM))
        tree_metric = 2

    candidate_count = min(n, max(k + 1, _APPROX_CANDIDATE_FACTOR * k + 1))
    tree = cKDTree(projected)
    candidates = np.empty((n, candidate_count), dtype=np.int64)
    batch = max(1, min(1024, n))
    total_batches = -(-n // batch)
    for batch_index, start in enumerate(range(0, n, batch)):
        stop = min(start + batch, n)
        _, near = tree.query(projected[start:stop], k=candidate_count, p=tree_metric)
        candidates[start:stop] = np.asarray(near, dtype=np.int64).reshape(stop - start, candidate_count)
        if progress is not None:
            progress(0.05 + 0.04 * (batch_index + 1) / total_batches, "fitting UMAP")

    indices = np.empty((n, k + 1), dtype=np.int64)
    distances = np.empty((n, k + 1), dtype=np.float32)
    for row in range(n):
        near = candidates[row][candidates[row] != row]
        if near.size < k:
            # This is only possible for a malformed/degenerate tree result;
            # keep the contract exact for that row rather than duplicating an
            # index in the graph.
            near = np.asarray([index for index in range(n) if index != row], dtype=np.int64)
        points = base[near]
        if metric == "manhattan":
            row_distances = np.abs(points - base[row]).sum(axis=1)
        else:
            row_distances = np.sqrt(np.maximum(((points - base[row]) ** 2).sum(axis=1), 0.0))
        order = np.argsort(row_distances, kind="stable")[:k]
        indices[row, 0] = row
        distances[row, 0] = 0.0
        indices[row, 1:] = near[order]
        distances[row, 1:] = row_distances[order]
        if progress is not None and (row + 1 == n or row % max(1, n // 20) == 0):
            progress(0.09 + 0.05 * (row + 1) / n, "fitting UMAP")
    return indices, distances


def _knn_indices_and_distances(
    x: np.ndarray, k: int, metric: str, progress: Progress | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """kNN with self at column 0 and bounded temporary memory.

    Small runs retain the exact chunked path. Larger runs use a deterministic
    cKDTree over an eight-dimensional candidate projection and recompute the
    requested metric in the original feature space for those candidates.
    """
    n = x.shape[0]
    if n > _APPROX_KNN_THRESHOLD:
        return _tree_knn(x, k, metric, progress)
    if metric == "manhattan":
        from scipy.spatial.distance import cdist
    elif metric == "cosine":
        x = x / np.maximum(np.linalg.norm(x, axis=1, keepdims=True), 1e-12)
    elif metric == "euclidean":
        # The expanded form of the distance below keeps only as many bits as
        # the features have left after their mean value, and UMAP embeds
        # untransformed descriptors by default. Centring costs one copy and
        # changes euclidean distances not at all. cdist subtracts coordinates
        # directly and is immune for the same reason; see _common.py.
        x = x - x.mean(axis=0)
    sq = (x * x).sum(1)
    idx = np.empty((n, k + 1), dtype=np.int64)
    dist = np.empty((n, k + 1), dtype=np.float32)
    # Keep the (block x n) float32 distance block near 32 MB. The previous
    # 256-row floor defeated that above n=32768, where 256 x n is ~1 GB.
    block = min(2048, max(1, (1 << 23) // max(n, 1)))
    total_blocks = -(-n // block)
    # At most ~100 checkpoints however the blocking turns out: each one is an
    # IPC event, and the callback is also where cancellation is checked.
    every = max(1, total_blocks // 100)
    for index, start in enumerate(range(0, n, block)):
        stop = min(start + block, n)
        if metric == "manhattan":
            d = cdist(x[start:stop], x, metric="cityblock")
        elif metric == "cosine":
            d = np.clip(1.0 - x[start:stop] @ x.T, 0.0, 2.0)
        else:
            d2 = sq[start:stop, None] + sq[None, :] - 2.0 * (x[start:stop] @ x.T)
            d = np.sqrt(np.maximum(d2, 0.0))
        # Remove the query identity before selecting neighbours.  Sorting the
        # k+1 closest rows and dropping column 0 is wrong for duplicate rows:
        # a duplicate can tie with self and push the real self index away from
        # that column, so the slice drops a valid neighbour and keeps self.
        local_rows = np.arange(stop - start)
        global_rows = np.arange(start, stop)
        d[local_rows, global_rows] = np.inf
        part = np.argpartition(d, k - 1, axis=1)[:, :k]
        near = np.take_along_axis(d, part, 1)
        order = np.argsort(near, axis=1)
        idx[start:stop, 0] = global_rows
        dist[start:stop, 0] = 0.0
        idx[start:stop, 1:] = np.take_along_axis(part, order, 1)
        dist[start:stop, 1:] = np.take_along_axis(near, order, 1)
        # This pass is O(n²·D) and the first thing a large selection set waits
        # through. Without a checkpoint inside it the job cannot be cancelled
        # until the search is already over.
        if progress is not None and (index % every == every - 1 or index == total_blocks - 1):
            progress(0.05 + 0.09 * (index + 1) / total_blocks, "fitting UMAP")
    return idx, dist


def _smooth_knn_weights(dist: np.ndarray, k: int) -> np.ndarray:
    """Per-point sigma so sum(exp(-(d - rho) / sigma)) hits log2(k) over the kNN."""
    rho = dist[:, 1]
    target = np.log2(k)

    def membership(sigma: np.ndarray) -> np.ndarray:
        return np.exp(-np.maximum(dist[:, 1:] - rho[:, None], 0.0) / sigma[:, None]).sum(1)

    lo = np.zeros(dist.shape[0], dtype=np.float32)
    hi = np.maximum(dist[:, 1:].mean(1) * 2.0, 1e-8)
    for _ in range(8):  # stretch bounds that cannot reach the target even at hi
        hi = np.where(membership(hi) < target, hi * 2.0, hi)
    for _ in range(64):
        mid = (lo + hi) / 2.0
        low = membership(mid) < target
        lo = np.where(low, mid, lo)
        hi = np.where(low, hi, mid)
    sigma = np.maximum((lo + hi) / 2.0, 1e-12)
    return np.exp(-np.maximum(dist[:, 1:] - rho[:, None], 0.0) / sigma[:, None])


def _fuzzy_simplicial_edges(
    idx: np.ndarray, weights: np.ndarray, n: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Symmetrized kNN membership graph using UMAP's fuzzy union."""
    from scipy.sparse import coo_matrix

    rows = np.repeat(np.arange(n, dtype=np.int64), idx.shape[1] - 1)
    graph = coo_matrix((weights.ravel(), (rows, idx[:, 1:].ravel())), shape=(n, n)).tocsr()
    transpose = graph.T.tocsr()
    graph = graph + transpose - graph.multiply(transpose)
    graph.setdiag(0.0)
    graph.eliminate_zeros()
    edges = graph.tocoo()
    return (
        edges.row.astype(np.int64),
        edges.col.astype(np.int64),
        edges.data.astype(np.float32),
    )


def _ab_params(min_dist: float) -> tuple[float, float]:
    """UMAP's a/b kernel parameters, fitted to the min_dist target curve."""
    from scipy.optimize import curve_fit

    def curve(r: np.ndarray, a: float, b: float) -> np.ndarray:
        return 1.0 / (1.0 + a * r ** (2 * b))

    r = np.linspace(0.0, 3.0 * _SPREAD, 300)
    target = np.where(r <= min_dist, 1.0, np.exp(-(r - min_dist) / _SPREAD))
    (a, b), _ = curve_fit(curve, r, target)
    return float(a), float(b)


def _pca_init(x: np.ndarray) -> np.ndarray:
    """Sign-canonical two-axis PCA scaled to umap's [0, 10] init magnitude."""
    centered = x - x.mean(axis=0)
    _, _, axes = np.linalg.svd(centered, full_matrices=False)
    axes = axes[:2]
    if axes.shape[0] < 2:
        # A single-descriptor run still needs two init axes; the spare stays
        # zero and the optimiser spreads it, instead of the run crashing.
        axes = np.pad(axes, ((0, 2 - axes.shape[0]), (0, 0)))
    # LAPACK sign conventions are platform-dependent; a flipped axis would
    # mirror the whole embedding, so pin each axis to a positive lead entry.
    flip = axes[np.arange(2), np.argmax(np.abs(axes), axis=1)] < 0
    axes = np.where(flip[:, None], -axes, axes)
    emb = (centered @ axes.T).astype(np.float32)
    span = np.maximum(np.ptp(emb, axis=0), 1e-9)
    return ((10.0 * (emb - emb.min(axis=0))) / span).astype(np.float32)


def _optimize(
    emb: np.ndarray,
    heads: np.ndarray,
    tails: np.ndarray,
    weights: np.ndarray,
    a: float,
    b: float,
    seed: int,
    n_epochs: int,
    progress: Progress | None,
) -> np.ndarray:
    """Batched cross-entropy SGD: per-epoch attractive + negative-sample forces."""
    rng = np.random.default_rng(seed)
    n, dim = emb.shape
    n_edges = heads.shape[0]
    for epoch in range(n_epochs):
        alpha = 1.0 * (1.0 - epoch / n_epochs)

        diff = emb[heads] - emb[tails]
        r2 = np.maximum((diff * diff).sum(1), 1e-8)
        coeff = (-2.0 * a * b * r2 ** (b - 1.0)) / (a * r2 ** b + 1.0)
        force = weights[:, None] * coeff[:, None] * diff
        np.clip(force, -4.0, 4.0, out=force)

        # With-replacement edge draws keep the repulsive budget O(cap) and
        # deterministic; edges missed in one epoch simply contribute later.
        pick = np.arange(n_edges)
        n_samples = n_edges * _NEGATIVE_SAMPLES_PER_EDGE
        if n_samples > _NEGATIVE_SAMPLE_CAP:
            pick = rng.integers(0, n_edges, size=_NEGATIVE_SAMPLE_CAP // _NEGATIVE_SAMPLES_PER_EDGE)
            n_samples = pick.size * _NEGATIVE_SAMPLES_PER_EDGE
        negatives = rng.integers(0, n, size=n_samples)
        heads_rep = np.repeat(heads[pick], _NEGATIVE_SAMPLES_PER_EDGE)
        diff_rep = emb[heads_rep] - emb[negatives]
        r2_rep = (diff_rep * diff_rep).sum(1)
        coeff_rep = np.where(
            r2_rep > 0,
            (2.0 * _GAMMA * b) / ((0.001 + r2_rep) * (a * r2_rep**b + 1.0)),
            0.0,
        )
        force_rep = coeff_rep[:, None] * diff_rep
        np.clip(force_rep, -4.0, 4.0, out=force_rep)

        update = np.empty_like(emb)
        for axis in range(dim):
            update[:, axis] = (
                np.bincount(heads, force[:, axis], n)
                - np.bincount(tails, force[:, axis], n)
                + np.bincount(heads_rep, force_rep[:, axis], n)
            )
        emb += (alpha * update).astype(np.float32)
        if progress is not None:
            progress(0.2 + 0.78 * (epoch + 1) / n_epochs, "fitting UMAP")
    return emb


def fit_umap(
    x: np.ndarray,
    *,
    n_neighbors: int,
    min_dist: float,
    metric: str,
    seed: int,
    progress: Progress | None = None,
) -> np.ndarray:
    """Embed x (n_samples, n_features) into 2-D UMAP coordinates (float64).

    ``progress`` receives (fraction, "fitting UMAP") checkpoints from 0.05 to
    0.98; the caller emits the terminal callback. n_neighbors must already be
    clamped to [2, n_samples - 1] and metric to the three supported values —
    the engine owns that validation.
    """
    # See module docstring: lazy scipy imports, after the warmup gate.
    x = np.ascontiguousarray(x, dtype=np.float32)
    n = x.shape[0]
    if progress is not None:
        progress(0.05, "fitting UMAP")
    idx, dist = _knn_indices_and_distances(x, n_neighbors, metric, progress)
    if progress is not None:
        progress(0.15, "fitting UMAP")
    weights = _smooth_knn_weights(dist, n_neighbors)
    heads, tails, edge_weights = _fuzzy_simplicial_edges(idx, weights, n)
    a, b = _ab_params(min_dist)
    emb = _pca_init(x)
    n_epochs = 500 if n <= 10_000 else 200
    emb = _optimize(emb, heads, tails, edge_weights, a, b, seed, n_epochs, progress)
    return emb.astype(np.float64)
