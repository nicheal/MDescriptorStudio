"""Deterministic fuzz tests for descriptor-matrix edge cases.

These complement the Hypothesis suite: they run in every environment (the CI
image does not need a test-only dependency) and assert the invariant that the
analysis layer either returns finite artifacts or raises a structured AppError
instead of crashing with an implementation exception.
"""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis import StructureDescriptorMatrix
from mdescriptor_studio_backend.analysis.algorithms.kernel import kernel
from mdescriptor_studio_backend.analysis.algorithms.pca import pca
from mdescriptor_studio_backend.analysis.metrics import feature_variance, neighbors, similarity
from mdescriptor_studio_backend.errors import AppError


def _is_structured_error(exc: Exception) -> bool:
    return isinstance(exc, AppError)


def test_random_matrices_never_crash_unexpectedly() -> None:
    rng = np.random.default_rng(20250301)
    for _ in range(25):
        n_samples = int(rng.integers(2, 40))
        n_features = int(rng.integers(1, 60))
        values = rng.normal(size=(n_samples, n_features)) * rng.uniform(0.01, 100.0)
        samples = StructureDescriptorMatrix(values, np.arange(n_samples, dtype=np.int64))
        for runner in (
            lambda s: pca(s, {"preprocess": "standardized"}),
            lambda s: neighbors(s, {"k": 3}),
            lambda s: feature_variance(s, {}),
            lambda s: similarity(s, {"k": 3}),
            lambda s: kernel(s, {"max_samples": min(n_samples, 12)}),
        ):
            try:
                result = runner(samples)
            except AppError as exc:
                assert exc.code  # structured errors are valid outcomes
                continue
            for array in result["arrays"].values():
                array = np.asarray(array)
                if np.issubdtype(array.dtype, np.floating):
                    assert np.isfinite(array).all()


def test_empty_and_one_sample_inputs_fail_structurally() -> None:
    with pytest.raises(AppError):
        StructureDescriptorMatrix(np.empty((0, 4)), np.empty((0,), dtype=np.int64))
    single = StructureDescriptorMatrix(np.ones((1, 4)), np.zeros(1, dtype=np.int64))
    with pytest.raises(AppError):
        pca(single, {})


def test_high_dimensional_small_sample_input_does_not_crash() -> None:
    values = np.linspace(-1.0, 1.0, 3 * 5_000, dtype=np.float64).reshape(3, 5_000)
    samples = StructureDescriptorMatrix(values, np.arange(3, dtype=np.int64))
    result = pca(samples, {"preprocess": "standardized"})
    assert result["arrays"]["coords"].shape == (3, 2)
    assert np.isfinite(result["arrays"]["coords"]).all()


def test_nan_handling_is_opt_in_per_algorithm() -> None:
    values = np.array([[0.0, 1.0], [np.nan, 2.0], [3.0, 4.0], [5.0, 6.0]])
    with pytest.raises(AppError):
        pca(StructureDescriptorMatrix(values, np.arange(4, dtype=np.int64)), {})
    # feature-variance is the diagnostic that deliberately tolerates non-finite
    # values and reports per-feature invalid counts.
    variance = feature_variance(
        StructureDescriptorMatrix(values, np.arange(4, dtype=np.int64)), {}
    )
    assert variance["arrays"]["invalid_count"].sum() == 1

