"""Shared model and service-layer regression tests for the analysis package."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis import (
    AnalysisRegistry,
    AlgorithmSpec,
    AtomDescriptorMatrix,
    StructureDescriptorMatrix,
    validate_matrix_consistency,
)
from mdescriptor_studio_backend.errors import ANALYSIS_INPUT_INVALID, AppError
from mdescriptor_studio_backend.services.analysis_helpers import canonical_analysis_request


def test_typed_matrices_reject_inconsistent_aligned_arrays() -> None:
    values = np.arange(12, dtype=np.float64).reshape(6, 2)
    with pytest.raises(AppError) as exc:
        StructureDescriptorMatrix(values, np.arange(5, dtype=np.int64))
    assert exc.value.code == ANALYSIS_INPUT_INVALID

    matrix = StructureDescriptorMatrix(values, np.arange(6, dtype=np.int64))
    assert matrix.n_samples == 6

    AtomDescriptorMatrix(values, np.arange(6, dtype=np.int64), row=np.arange(6), elements=np.arange(6))


def test_matrix_consistency_validates_field_shapes() -> None:
    values = np.arange(12, dtype=np.float64).reshape(6, 2)
    with pytest.raises(AppError):
        validate_matrix_consistency(values, np.arange(6), properties={"energy": np.arange(5.0)})
    with pytest.raises(AppError):
        validate_matrix_consistency(values, np.arange(6), positions=np.zeros((6, 2)))
    with pytest.raises(AppError):
        validate_matrix_consistency(values, np.arange(6), sample_ids=["only-one"])
    with pytest.raises(AppError):
        validate_matrix_consistency(np.array([[1.0], [2.0]]), np.arange(2), cells=np.zeros((2, 2)))


def test_registry_runs_an_independently_registered_plugin() -> None:
    class DemoPlugin:
        name = "demo_metric"
        category = "engine"

        def run(self, data, params: dict, progress=None) -> dict:
            return {"arrays": {"scale": np.asarray(data.values) * float(params.get("scale", 1))}}

    registry = AnalysisRegistry()
    registry.register(DemoPlugin())
    samples = StructureDescriptorMatrix(np.arange(8, dtype=np.float64).reshape(4, 2), np.arange(4))
    result = registry.run("demo_metric", [samples], {"scale": 2.0})
    np.testing.assert_array_equal(result["arrays"]["scale"], samples.values * 2.0)
    assert "demo_metric" in registry.names()

    registry.register(AlgorithmSpec("demo_pair", "pair", lambda left, right, params, progress=None: {"arrays": {}}))
    with pytest.raises(ValueError, match="two input"):
        registry.run("demo_pair", [samples], {})


def test_registry_rejects_a_missing_input_matrix() -> None:
    registry = AnalysisRegistry()
    registry.register(AlgorithmSpec("demo_engine", "engine", lambda data, params, progress=None: {"arrays": {}}))
    with pytest.raises(ValueError, match="requires an input"):
        registry.run("demo_engine", [], {})


def test_analysis_aliases_share_one_canonical_request_identity() -> None:
    assert canonical_analysis_request("hierarchical", {}) == ("agglomerative", {"algorithm": "agglomerative"})
    assert canonical_analysis_request("outlier", {"method": "iforest"}) == (
        "isolation_forest",
        {"algorithm": "isolation_forest"},
    )
    assert canonical_analysis_request("sampling", {"algorithm": "element"}) == (
        "per_element",
        {"algorithm": "per_element", "mode": "atom"},
    )
