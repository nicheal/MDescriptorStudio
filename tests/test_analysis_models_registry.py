"""Shared model and service-layer regression tests for the analysis package."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis import (
    AnalysisEngine,
    AtomDescriptorMatrix,
    AnalysisRegistry,
    AlgorithmSpec,
    PropertyMatrix,
    SampleMatrix,
    StructureDescriptorMatrix,
    TrajectoryDescriptorMatrix,
    validate_matrix_consistency,
)
from mdescriptor_studio_backend.errors import ANALYSIS_INPUT_INVALID, AppError


def test_typed_matrices_reject_inconsistent_aligned_arrays() -> None:
    values = np.arange(12, dtype=np.float64).reshape(6, 2)
    with pytest.raises(AppError) as exc:
        StructureDescriptorMatrix(values, np.arange(5, dtype=np.int64))
    assert exc.value.code == ANALYSIS_INPUT_INVALID

    matrix = StructureDescriptorMatrix(values, np.arange(6, dtype=np.int64))
    assert matrix.n_samples == 6
    assert matrix.granularity == "structure"

    atom = AtomDescriptorMatrix(values, np.arange(6, dtype=np.int64), row=np.arange(6), elements=np.arange(6))
    assert atom.granularity == "atom"

    trajectory = TrajectoryDescriptorMatrix(values, np.arange(6, dtype=np.int64))
    assert trajectory.granularity == "structure"

    target = PropertyMatrix(values, np.arange(6, dtype=np.int64), properties={"energy": np.arange(6.0)})
    assert target.properties["energy"].shape == (6,)


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


def test_sample_matrix_remains_a_compatible_shim() -> None:
    values = np.arange(6, dtype=np.float64).reshape(3, 2)
    legacy = SampleMatrix(values, np.arange(3, dtype=np.int64), mode="atom")
    assert legacy.granularity == "atom"
    assert legacy.subset([0, 2]).n_samples == 2


def test_registry_runs_an_independently_registered_plugin() -> None:
    class DemoPlugin:
        name = "demo_metric"
        category = "engine"

        def validate(self, params: dict) -> None:
            return None

        def run(self, data, params: dict, progress=None) -> dict:
            return {"arrays": {"scale": np.asarray(data.values) * float(params.get("scale", 1))}}

    registry = AnalysisRegistry()
    registry.register(DemoPlugin())
    samples = StructureDescriptorMatrix(np.arange(8, dtype=np.float64).reshape(4, 2), np.arange(4))
    result = registry.run("demo_metric", [samples], {"scale": 2.0})
    np.testing.assert_array_equal(result["arrays"]["scale"], samples.values * 2.0)
    assert "demo_metric" in registry.names()

    registry.register(AlgorithmSpec("demo_alias", "engine", lambda data, params, progress=None: {"arrays": {"ok": np.array([1])}}, aliases=("demo_legacy",)))
    assert registry.get("demo_legacy").name == "demo_alias"
    assert registry.run("demo_legacy", [samples], {})["arrays"]["ok"].tolist() == [1]


def test_engine_facade_matches_plugin_functions() -> None:
    from mdescriptor_studio_backend.analysis.algorithms.pca import pca as plugin_pca

    values = np.arange(24, dtype=np.float64).reshape(8, 3)
    samples = StructureDescriptorMatrix(values, np.arange(8))
    direct = plugin_pca(samples, {"preprocess": "center"})
    facade = AnalysisEngine.pca(samples, {"preprocess": "center"})
    np.testing.assert_allclose(direct["arrays"]["coords"], facade["arrays"]["coords"])
