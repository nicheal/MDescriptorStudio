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


def test_grouped_sampling_returns_exact_target_count() -> None:
    values = np.arange(40, dtype=np.float64).reshape(20, 2)
    frames = np.repeat(np.arange(4, dtype=np.int64), 5)
    structure_samples = SampleMatrix(values, frames)
    stratified = AnalysisEngine.sampling(structure_samples, {"n_samples": 10, "seed": 7}, "stratified")
    assert stratified["arrays"]["selected_indices"].size == 10
    assert np.unique(frames[stratified["arrays"]["selected_indices"]]).size == 4

    elements = np.repeat(np.array([14, 32], dtype=np.int64), 10)
    atom_samples = SampleMatrix(values, frames, elements=elements, mode="atom")
    per_element = AnalysisEngine.sampling(atom_samples, {"n_samples": 5, "seed": 7}, "per_element")
    selected = per_element["arrays"]["selected_indices"]
    assert selected.size == 5
    assert np.unique(elements[selected]).size == 2


def test_cross_dataset_distances_keep_constant_reference_features() -> None:
    reference = SampleMatrix(np.array([[0.0, 0.0], [0.0, 1.0]]), np.arange(2))
    query = SampleMatrix(np.array([[100.0, 0.5]]), np.array([0]))

    euclidean = AnalysisEngine.coverage(reference, query, {"preprocess": "raw", "metric": "euclidean"})
    manhattan = AnalysisEngine.coverage(reference, query, {"preprocess": "raw", "metric": "manhattan"})
    assert euclidean["arrays"]["distances"][0] > 99.0
    assert np.isclose(manhattan["arrays"]["distances"][0], 100.5)


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


def test_uncertainty_acquisition_exposes_knn_uncertainty_and_diversity(samples: SampleMatrix) -> None:
    query = SampleMatrix(
        samples.values + np.linspace(0.0, 0.25, samples.n_samples)[:, None],
        samples.frame,
        sample_ids=samples.sample_ids,
    )
    result = AnalysisEngine.acquisition(
        samples,
        query,
        {"n_samples": 8, "uncertainty_k": 4, "acquisition_method": "uncertainty_diversity", "seed": 42},
    )

    assert result["preview"]["algorithm"] == "uncertainty_diversity"
    assert result["preview"]["uncertainty_method"] == "knn_extrapolation"
    assert result["arrays"]["uncertainty"].shape == (samples.n_samples,)
    assert result["arrays"]["diversity"].shape == (samples.n_samples,)
    assert len(result["arrays"]["selected_indices"]) == 8
    assert np.isfinite(result["preview"]["mean_selected_uncertainty"])
    assert any("not model prediction variance" in warning for warning in result["warnings"])


def test_mantel_reports_observed_statistic_and_permutation_p_value(samples: SampleMatrix) -> None:
    right = SampleMatrix(
        samples.values.copy(),
        samples.frame,
        sample_ids=samples.sample_ids,
    )
    result = AnalysisEngine.mantel(
        samples,
        right,
        {"method": "spearman", "permutations": 31, "max_samples": 24, "seed": 42},
    )

    assert result["preview"]["kind"] == "mantel"
    assert result["preview"]["statistic"] > 0.99
    assert 0.0 <= result["preview"]["p_value"] <= 1.0
    assert result["arrays"]["null_distribution"].shape == (31,)
    assert result["arrays"]["left_pair_distances"].shape == result["arrays"]["right_pair_distances"].shape


def test_structural_perturbation_sensitivity_returns_sorted_response_curves(samples: SampleMatrix) -> None:
    direction = np.linspace(0.1, 1.2, samples.n_samples * samples.n_features).reshape(samples.values.shape)
    perturbations = [
        (0.2, SampleMatrix(samples.values + 0.2 * direction, samples.frame, sample_ids=samples.sample_ids)),
        (0.0, SampleMatrix(samples.values.copy(), samples.frame, sample_ids=samples.sample_ids)),
        (0.1, SampleMatrix(samples.values + 0.1 * direction, samples.frame, sample_ids=samples.sample_ids)),
    ]
    result = AnalysisEngine.perturbation_sensitivity(samples, perturbations, {"metric": "euclidean"})

    assert np.array_equal(result["arrays"]["amplitudes"], np.array([0.0, 0.1, 0.2]))
    assert result["preview"]["baseline_included"] is True
    assert result["arrays"]["response_matrix"].shape == (3, samples.n_samples)
    assert np.allclose(result["arrays"]["mean_response"][0], 0.0)
    assert np.all(np.diff(result["arrays"]["mean_response"]) >= -1e-12)


def test_perturbation_response_keeps_constant_baseline_features() -> None:
    baseline = SampleMatrix(
        np.array([[0.0, 1.0], [0.0, 2.0]]),
        np.arange(2),
        sample_ids=["a", "b"],
    )
    perturbed = SampleMatrix(
        np.array([[1.0, 1.0], [1.0, 2.0]]),
        np.arange(2),
        sample_ids=["a", "b"],
    )
    result = AnalysisEngine.perturbation_sensitivity(
        baseline,
        [(0.1, perturbed)],
        {"preprocess": "raw", "metric": "euclidean"},
    )
    assert np.allclose(result["arrays"]["response_matrix"], 1.0)


def test_local_diversity_reports_periodic_coordination_and_neighbor_shell() -> None:
    values = np.array(
        [[0.0, 1.0, 2.0], [1.0, 0.0, 2.0], [2.0, 1.0, 0.0], [1.5, 2.0, 0.5]],
        dtype=np.float64,
    )
    positions = np.array(
        [[0.05, 0.05, 0.05], [0.95, 0.05, 0.05], [0.50, 0.50, 0.50], [0.50, 0.50, 0.60]],
        dtype=np.float64,
    )
    result = AnalysisEngine.local_diversity(
        SampleMatrix(
            values,
            np.zeros(4, dtype=np.int64),
            row=np.arange(4, dtype=np.int64),
            sample_ids=[f"frame:0:row:{i}" for i in range(4)],
            elements=np.full(4, 14, dtype=np.int64),
            mode="atom",
            positions=positions,
            cells=np.repeat(np.eye(3, dtype=np.float64)[None, :, :], 4, axis=0),
            pbc=np.ones((4, 3), dtype=bool),
        ),
        {"cutoff": 0.2, "max_neighbors": 8, "n_clusters": 2, "k": 1},
    )

    assert result["preview"]["neighbor_graph_available"] is True
    assert result["preview"]["neighbor_count"] == 4
    assert np.all(result["arrays"]["coordination"] == 1)
    assert result["arrays"]["neighbor_offsets"].tolist() == [0, 1, 2, 3, 4]
    assert np.allclose(result["arrays"]["neighbor_distances"], 0.1)


def test_local_neighbor_graph_keeps_periodic_image_contacts() -> None:
    values = np.array([[0.0, 1.0], [1.0, 0.0], [0.5, 0.25]], dtype=np.float64)
    result = AnalysisEngine.local_diversity(
        SampleMatrix(
            values,
            np.arange(3, dtype=np.int64),
            row=np.zeros(3, dtype=np.int64),
            sample_ids=[f"frame:{i}:row:0" for i in range(3)],
            elements=np.full(3, 14, dtype=np.int64),
            mode="atom",
            positions=np.zeros((3, 3), dtype=np.float64),
            cells=np.repeat(np.eye(3, dtype=np.float64)[None, :, :], 3, axis=0),
            pbc=np.ones((3, 3), dtype=bool),
        ),
        {"cutoff": 1.1, "max_neighbors": 8, "n_clusters": 2, "k": 1},
    )
    assert result["arrays"]["coordination"].tolist() == [6, 6, 6]
    assert result["arrays"]["neighbor_offsets"].tolist() == [0, 6, 12, 18]
    assert result["arrays"]["neighbor_indices"].tolist() == [0] * 6 + [1] * 6 + [2] * 6
    assert np.allclose(result["arrays"]["neighbor_distances"], 1.0)


def test_local_neighbor_graph_csr_rows_follow_global_order() -> None:
    values = np.array(
        [[0.0, 1.0], [1.0, 0.0], [0.2, 0.8], [0.8, 0.2]],
        dtype=np.float64,
    )
    result = AnalysisEngine.local_diversity(
        SampleMatrix(
            values,
            np.array([0, 1, 0, 1], dtype=np.int64),
            row=np.arange(4, dtype=np.int64),
            sample_ids=[f"sample:{i}" for i in range(4)],
            elements=np.full(4, 14, dtype=np.int64),
            mode="atom",
            positions=np.array(
                [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.1, 0.0, 0.0]],
                dtype=np.float64,
            ),
        ),
        {"cutoff": 0.2, "max_neighbors": 8, "n_clusters": 2, "k": 1},
    )
    assert result["arrays"]["neighbor_offsets"].tolist() == [0, 1, 2, 3, 4]
    assert result["arrays"]["neighbor_indices"].tolist() == [2, 3, 0, 1]


def test_drift_marks_covariance_shift_unavailable_for_singletons() -> None:
    single = SampleMatrix(np.array([[2.0, 3.0]]), np.array([0]), sample_ids=["one"])
    result = AnalysisEngine.drift(single, single, {})
    assert result["preview"]["covariance_shift"] is None
    assert np.isfinite(result["preview"]["mmd"])
    assert any("covariance shift requires" in warning for warning in result["warnings"])


def test_sensitivity_reports_peak_memory_and_delta(samples: SampleMatrix) -> None:
    result = AnalysisEngine.sensitivity(
        [
            ({"id": "r1", "descriptor_name": "ACE", "parameters_json": "{}", "memory_peak_bytes": 100}, samples),
            ({"id": "r2", "descriptor_name": "ACE", "parameters_json": "{}", "memory_peak_bytes": 160}, samples),
        ],
        {},
    )

    rows = result["preview"]["runs"]
    assert rows[1]["memory_delta_bytes"] == 60
    assert np.array_equal(result["arrays"]["memory_peak_bytes"], np.array([100.0, 160.0]))
    assert result["preview"]["memory_available"] is True


def test_sensitivity_uses_baseline_scaling_for_common_shifts() -> None:
    baseline = SampleMatrix(
        np.array([[0.0, 1.0], [1.0, 2.0], [2.0, 4.0], [3.0, 8.0]]),
        np.arange(4),
        sample_ids=[f"s{i}" for i in range(4)],
    )
    shifted = SampleMatrix(
        baseline.values + 5.0,
        baseline.frame,
        sample_ids=baseline.sample_ids,
    )
    result = AnalysisEngine.sensitivity(
        [
            ({"id": "base", "descriptor_name": "SOAP", "parameters_json": "{}"}, baseline),
            ({"id": "shifted", "descriptor_name": "SOAP", "parameters_json": "{}"}, shifted),
        ],
        {"max_samples": 4, "n_clusters": 2, "k": 1},
    )
    assert result["preview"]["runs"][1]["mean_delta_norm"] > 1.0
