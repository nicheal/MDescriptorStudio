"""Numerical contract tests for the generic Analysis engine.

These tests use a small deterministic matrix and never require a descriptor
calculation.  They protect the public algorithm vocabulary, float64 outputs,
bounded sampling, and structured validation errors.
"""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis import AnalysisEngine, SampleMatrix
from mdescriptor_studio_backend.errors import (
    ANALYSIS_INPUT_INVALID,
    ANALYSIS_INSUFFICIENT_SAMPLES,
    AppError,
)


@pytest.fixture()
def samples() -> SampleMatrix:
    rng = np.random.default_rng(42)
    values = rng.normal(size=(48, 12)).astype(np.float32)
    return SampleMatrix(
        values=values,
        frame=np.arange(values.shape[0], dtype=np.int64),
        sample_ids=[f"frame:{i}" for i in range(values.shape[0])],
    )


@pytest.mark.parametrize(
    ("name", "runner"),
    [
        ("pca", lambda s: AnalysisEngine.pca(s, {})),
        ("umap", lambda s: AnalysisEngine.umap(s, {"n_neighbors": 8})),
        ("tsne", lambda s: AnalysisEngine.tsne(s, {"perplexity": 8, "max_iter": 250})),
        ("neighbors", lambda s: AnalysisEngine.neighbors(s, {"k": 5})),
        ("similarity", lambda s: AnalysisEngine.similarity(s, {"k": 5})),
        ("pairwise", lambda s: AnalysisEngine.pairwise(s, {"max_samples": 24})),
        ("kmeans", lambda s: AnalysisEngine.cluster(s, {"n_clusters": 4}, "kmeans")),
        ("dbscan", lambda s: AnalysisEngine.cluster(s, {"min_samples": 3}, "dbscan")),
        ("hdbscan", lambda s: AnalysisEngine.cluster(s, {"min_cluster_size": 3}, "hdbscan")),
        ("agglomerative", lambda s: AnalysisEngine.cluster(s, {"n_clusters": 4}, "agglomerative")),
        ("outlier-knn", lambda s: AnalysisEngine.outlier(s, {"k": 5}, "knn")),
        ("outlier-lof", lambda s: AnalysisEngine.outlier(s, {"k": 5}, "lof")),
        ("outlier-iforest", lambda s: AnalysisEngine.outlier(s, {}, "isolation_forest")),
        ("outlier-mahalanobis", lambda s: AnalysisEngine.outlier(s, {}, "mahalanobis")),
        ("fps", lambda s: AnalysisEngine.sampling(s, {"n_samples": 8}, "fps")),
        ("random", lambda s: AnalysisEngine.sampling(s, {"n_samples": 8}, "random")),
        ("stratified", lambda s: AnalysisEngine.sampling(s, {"n_samples": 8}, "stratified")),
        ("cluster-representative", lambda s: AnalysisEngine.sampling(s, {"n_samples": 8}, "cluster_representative")),
        ("per-element", lambda s: AnalysisEngine.sampling(SampleMatrix(s.values, s.frame, elements=np.where(s.frame % 2, 31, 33)), {"n_samples": 8}, "per_element")),
        ("coverage", lambda s: AnalysisEngine.coverage(s, s, {})),
        ("overlap", lambda s: AnalysisEngine.overlap(s, s, {})),
        ("acquisition", lambda s: AnalysisEngine.acquisition(s, s, {"n_samples": 8})),
        ("compare", lambda s: AnalysisEngine.compare(s, s, {})),
        ("feature-variance", lambda s: AnalysisEngine.feature_variance(s, {})),
        ("feature-correlation", lambda s: AnalysisEngine.feature_correlation(s, {})),
        ("effective-dimension", lambda s: AnalysisEngine.effective_dimension(s, {})),
        (
            "property-correlation",
            lambda s: AnalysisEngine.property_correlation(
                SampleMatrix(s.values, s.frame, properties={"energy_per_atom": s.values[:, 0] * 0.5 + s.values[:, 1]}),
                {"property": "energy_per_atom", "folds": 3},
            ),
        ),
        (
            "local-diversity",
            lambda s: AnalysisEngine.local_diversity(
                SampleMatrix(
                    s.values,
                    s.frame,
                    row=np.arange(s.n_samples),
                    elements=np.where(s.frame % 2, 31, 33),
                    mode="atom",
                ),
                {"n_clusters": 3},
            ),
        ),
        ("kernel", lambda s: AnalysisEngine.kernel(s, {"kernel": "rbf", "max_samples": 24})),
        ("trajectory", lambda s: AnalysisEngine.trajectory(s, {"frame_start": 0, "frame_end": 47})),
        ("drift", lambda s: AnalysisEngine.drift(s, s, {})),
        (
            "sensitivity",
            lambda s: AnalysisEngine.sensitivity(
                [({"id": "r1", "parameters_json": "{}"}, s), ({"id": "r2", "parameters_json": "{}"}, s)],
                {},
            ),
        ),
    ],
)
def test_analysis_algorithms_return_artifacts(name: str, runner, samples: SampleMatrix) -> None:
    result = runner(samples)
    assert result["arrays"], name
    for array in result["arrays"].values():
        assert np.asarray(array).dtype in (np.float64, np.int64), name
    assert isinstance(result["preview"], dict)


def test_sampling_is_deterministic_and_bounded(samples: SampleMatrix) -> None:
    a = AnalysisEngine.sampling(samples, {"n_samples": 10, "seed": 42}, "fps")
    b = AnalysisEngine.sampling(samples, {"n_samples": 10, "seed": 42}, "fps")
    assert np.array_equal(a["arrays"]["selected_indices"], b["arrays"]["selected_indices"])
    assert len(a["arrays"]["selected_indices"]) == 10


def test_invalid_numeric_inputs_are_structured(samples: SampleMatrix) -> None:
    with pytest.raises(AppError) as exc:
        AnalysisEngine.pca(SampleMatrix(np.array([[np.nan, 1.0], [2.0, 3.0]]), np.array([0, 1])), {})
    assert exc.value.code == ANALYSIS_INPUT_INVALID

    with pytest.raises(AppError) as exc:
        AnalysisEngine.tsne(samples, {"perplexity": 100})
    assert exc.value.code == ANALYSIS_INPUT_INVALID

    with pytest.raises(AppError) as exc:
        AnalysisEngine.trajectory(samples, {"frame_start": 0, "frame_end": 0})
    assert exc.value.code == ANALYSIS_INSUFFICIENT_SAMPLES


def test_wide_correlation_keeps_heatmap_bounded(samples: SampleMatrix) -> None:
    rng = np.random.default_rng(7)
    wide = SampleMatrix(rng.normal(size=(48, 1_000)), samples.frame)
    result = AnalysisEngine.feature_correlation(wide, {"top_k": 12, "heatmap_features": 32})
    matrix = result["arrays"]["correlation_matrix"]
    assert matrix.shape == (32, 32)
    assert result["arrays"]["pairs"].shape == (12, 2)
    assert any("limited" in warning for warning in result["warnings"])


def test_feature_correlation_is_bounded_and_keeps_unit_diagonal() -> None:
    values = np.array([[0.0, 0.0, 1.0], [1.0, 1.0, 1.0], [2.0, 2.0, 1.0]])
    result = AnalysisEngine.feature_correlation(SampleMatrix(values, np.arange(3)), {})
    matrix = result["arrays"]["correlation_matrix"]
    assert np.allclose(np.diag(matrix), 1.0)
    assert np.all(np.abs(matrix) <= 1.0 + 1e-12)
    assert np.isclose(result["arrays"]["correlations"][0], 1.0)


def test_effective_dimension_reports_zero_thresholds_for_constant_input() -> None:
    values = np.ones((4, 2), dtype=np.float64)
    result = AnalysisEngine.effective_dimension(SampleMatrix(values, np.arange(4)), {})
    assert result["preview"]["participation_ratio"] == 0.0
    assert result["preview"]["components_for_threshold"] == {"0.9": 0, "0.95": 0, "0.99": 0}


def test_tsne_adapts_default_perplexity_for_small_inputs() -> None:
    values = np.arange(8, dtype=np.float64).reshape(4, 2)
    result = AnalysisEngine.tsne(SampleMatrix(values, np.arange(4)), {"max_iter": 250})
    assert result["preview"]["parameters"]["perplexity"] == 3.0


def test_compare_reports_geometry_and_neighbor_consistency(samples: SampleMatrix) -> None:
    perturbed = SampleMatrix(samples.values * 1.5 + 0.01, samples.frame, sample_ids=samples.sample_ids)
    result = AnalysisEngine.compare(samples, perturbed, {"max_samples": 32, "k": 5})
    preview = result["preview"]
    assert preview["pairwise_distance_pearson"] > 0.99
    assert preview["pairwise_distance_spearman"] > 0.99
    assert preview["neighbor_overlap"] > 0.99
    assert "left_coords" in result["arrays"] and "right_coords" in result["arrays"]


def test_sensitivity_accepts_different_feature_dimensions(samples: SampleMatrix) -> None:
    narrow = SampleMatrix(samples.values[:, :3], samples.frame, sample_ids=samples.sample_ids)
    result = AnalysisEngine.sensitivity(
        [
            ({"id": "wide", "descriptor_name": "SOAP", "parameters_json": "{}"}, samples),
            ({"id": "narrow", "descriptor_name": "SOAP", "parameters_json": "{}"}, narrow),
        ],
        {"max_samples": 32, "k": 5, "n_clusters": 3},
    )

    rows = result["preview"]["runs"]
    assert rows[0]["feature_count"] == samples.n_features
    assert rows[1]["feature_count"] == 3
    assert rows[1]["mean_delta_norm"] is None
    assert np.isfinite(rows[1]["pairwise_distance_pearson"])
    assert np.isfinite(rows[1]["pairwise_distance_spearman"])


def test_sensitivity_rejects_different_descriptors(samples: SampleMatrix) -> None:
    with pytest.raises(AppError, match="same descriptor") as exc:
        AnalysisEngine.sensitivity(
            [
                ({"id": "soap", "descriptor_name": "SOAP", "parameters_json": "{}"}, samples),
                ({"id": "acsf", "descriptor_name": "ACSF", "parameters_json": "{}"}, samples),
            ],
            {},
        )
    assert exc.value.code == ANALYSIS_INPUT_INVALID


def test_overlap_identity_is_detected_as_duplicate(samples: SampleMatrix) -> None:
    result = AnalysisEngine.overlap(samples, samples, {"duplicate_threshold": 1e-10})
    assert result["preview"]["near_duplicates"] == samples.n_samples
    assert np.all(result["arrays"]["nearest_indices"] == np.arange(samples.n_samples))


def test_feature_redundancy_summary_counts_correlated_columns() -> None:
    values = np.column_stack([np.arange(20), np.arange(20), np.arange(20) ** 2]).astype(np.float64)
    result = AnalysisEngine.feature_correlation(SampleMatrix(values, np.arange(20)), {"redundancy_threshold": 0.99})
    assert result["preview"]["highly_correlated_pairs"] >= 1
    assert result["preview"]["redundant_feature_count"] >= 1


def test_trajectory_includes_reference_distance_and_projection(samples: SampleMatrix) -> None:
    result = AnalysisEngine.trajectory(samples, {"frame_start": 0, "frame_end": 47})
    assert result["arrays"]["reference_distance"][0] == 0
    assert result["arrays"]["coords"].shape == (48, 2)
    assert result["arrays"]["cumulative_distance"][-1] >= result["arrays"]["step_distance"][-1]
