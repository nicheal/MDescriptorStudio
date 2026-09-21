"""Numerical contract tests for the in-repo numpy UMAP embedding.

The embedding replaced umap-learn: these tests pin the properties the Studio
relies on — bitwise determinism per seed, float64 output, all three metrics,
informative progress checkpoints — and embedding quality on separable blobs.
"""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis.umap_numpy import _knn_indices_and_distances, fit_umap


def _blobs(n: int = 300, d: int = 10, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    centers = rng.normal(0.0, 4.0, size=(4, d))
    labels = rng.integers(0, 4, n)
    return centers[labels] + rng.normal(0.0, 1.0, size=(n, d)), labels


def _fit(x, **overrides):
    params = {"n_neighbors": 10, "min_dist": 0.1, "metric": "euclidean", "seed": 42} | overrides
    return fit_umap(x, **params)


def test_same_seed_is_bitwise_deterministic() -> None:
    x, _ = _blobs()
    first = _fit(x)
    second = _fit(x)
    assert first.dtype == np.float64
    assert np.array_equal(first, second)


def test_different_seed_moves_coordinates() -> None:
    x, _ = _blobs()
    assert not np.array_equal(_fit(x, seed=1), _fit(x, seed=2))


@pytest.mark.parametrize("metric", ["euclidean", "cosine", "manhattan"])
def test_supported_metrics_return_finite_coordinates(metric: str) -> None:
    x, _ = _blobs()
    coords = _fit(x, metric=metric)
    assert coords.shape == (300, 2)
    assert np.isfinite(coords).all()


def test_progress_checkpoints_are_monotonic_and_terminal() -> None:
    x, _ = _blobs(n=120, d=6)
    seen: list[tuple[float, str]] = []
    _fit(x, progress=lambda fraction, message: seen.append((fraction, message)))
    fractions = [fraction for fraction, _ in seen]
    assert fractions[0] == pytest.approx(0.05)
    assert fractions[-1] == pytest.approx(0.98)
    assert all(left <= right for left, right in zip(fractions, fractions[1:]))
    assert {message for _, message in seen} == {"fitting UMAP"}


def _median_nearest_neighbour_distance(coords) -> float:
    """How far apart the tightest pairs in an embedding are, in embedding units."""
    squared = ((coords[:, None, :] - coords[None, :, :]) ** 2).sum(axis=-1)
    np.fill_diagonal(squared, np.inf)
    return float(np.median(np.sqrt(squared.min(axis=1))))


def test_separable_blobs_preserve_local_structure() -> None:
    from sklearn.manifold import trustworthiness

    x, _ = _blobs()
    coords = _fit(x)
    # Measured on the variants this file cannot otherwise tell apart: dropping
    # every repulsive force gives 0.9287 and returning the PCA initialisation
    # gives 0.9326, so the 0.9 that used to be the whole quality gate accepted
    # both (pass 5, 5-N2). The seeds sit at 0.9592-0.9595 and a 1200-point run
    # at 0.9578, so 0.95 still has room.
    assert trustworthiness(x, coords, n_neighbors=10) > 0.95


def test_min_dist_actually_spreads_the_embedding() -> None:
    """`min_dist` is a slider in the projection panel, and nothing here read it.

    The spread it controls is the contract: a bigger `min_dist` must push the
    tightest pairs apart. An implementation that ignored the parameter - the
    ``(a, b) = (1, 1)`` shortcut - scored 0.958 on the gate above, so quality
    alone cannot catch it; this one does.
    """
    x, _ = _blobs()
    spreads = [_median_nearest_neighbour_distance(_fit(x, min_dist=md)) for md in (0.05, 0.1, 0.5, 0.99)]
    assert all(narrower < wider for narrower, wider in zip(spreads, spreads[1:])), spreads


def test_knn_search_checkpoints_so_a_long_one_stays_cancellable() -> None:
    """Cancellation is checked inside the progress callback. A pass that never
    calls it cannot be stopped, and this one is O(n^2*D) over the whole
    selection set."""
    x = np.random.default_rng(3).normal(size=(8000, 2)).astype(np.float32)
    seen: list[float] = []
    _knn_indices_and_distances(x, 10, "euclidean", progress=lambda fraction, _m: seen.append(fraction))
    assert len(seen) > 4
    assert seen[0] > 0.05 and seen[-1] == pytest.approx(0.14)
    assert all(left <= right for left, right in zip(seen, seen[1:]))


def test_shared_feature_offset_does_not_reorder_neighbours() -> None:
    """A common shift changes no euclidean distance, yet rebuilding distances
    as |a|^2 + |b|^2 - 2ab gives float32 away exactly in the low-order bits
    that separate near-duplicate rows -- which is what descriptor matrices of
    similar configurations look like. The neighbourhoods UMAP builds its graph
    from must not depend on where the feature origin sits."""
    from scipy.spatial.distance import cdist

    rng = np.random.default_rng(11)
    x = (rng.normal(size=(600, 96)) + 2000.0).astype(np.float32)
    exact = np.argsort(cdist(x.astype(np.float64), x.astype(np.float64)), axis=1)[:, :11]
    indices, _ = _knn_indices_and_distances(x, 10, "euclidean")
    overlap = np.mean([len(set(row.tolist()) & set(known.tolist())) for row, known in zip(indices, exact)])
    assert overlap > 0.98
    assert (indices[:, 0] == np.arange(x.shape[0])).all(), "self must stay in column 0"
