"""Numerical contract tests for the in-repo numpy UMAP embedding.

The embedding replaced umap-learn: these tests pin the properties the Studio
relies on — bitwise determinism per seed, float64 output, all three metrics,
informative progress checkpoints — and embedding quality on separable blobs.
"""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis.umap_numpy import fit_umap


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


def test_separable_blobs_preserve_local_structure() -> None:
    from sklearn.manifold import trustworthiness

    x, _ = _blobs()
    coords = _fit(x)
    assert trustworthiness(x, coords, n_neighbors=10) > 0.9
