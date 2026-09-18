"""Hypothesis property tests for random descriptor inputs.

Hypothesis is a test-only dependency. When it is unavailable (the runtime
sidecar intentionally carries no test packages) these tests skip; the
deterministic fuzz suite in test_analysis_fuzz.py still exercises the same
invariants in every environment.
"""

from __future__ import annotations

import numpy as np
import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings
from hypothesis import strategies as st

from mdescriptor_studio_backend.analysis import StructureDescriptorMatrix
from mdescriptor_studio_backend.analysis.algorithms.pca import pca
from mdescriptor_studio_backend.errors import AppError


@settings(max_examples=40, deadline=None)
@given(
    n_samples=st.integers(min_value=2, max_value=30),
    n_features=st.integers(min_value=1, max_value=80),
    seed=st.integers(min_value=0, max_value=2**32 - 1),
)
def test_random_descriptor_matrices_never_crash(n_samples: int, n_features: int, seed: int) -> None:
    rng = np.random.default_rng(seed)
    values = rng.normal(size=(n_samples, n_features)) * rng.uniform(0.01, 1000.0)
    samples = StructureDescriptorMatrix(values, np.arange(n_samples, dtype=np.int64))
    try:
        result = pca(samples, {"preprocess": "standardized"})
    except AppError as exc:
        assert exc.code
        return
    assert result["arrays"]["coords"].shape == (n_samples, 2)
    assert np.isfinite(result["arrays"]["coords"]).all()
