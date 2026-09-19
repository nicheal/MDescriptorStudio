"""Numerical contract tests for the generic Analysis engine.

These tests use a small deterministic matrix and never require a descriptor
calculation.  They protect the public algorithm vocabulary, float64 outputs,
bounded sampling, and structured validation errors.
"""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis import AtomDescriptorMatrix, StructureDescriptorMatrix
from mdescriptor_studio_backend.analysis.algorithms.correlation import feature_correlation, property_correlation
from mdescriptor_studio_backend.analysis.algorithms.kernel import kernel
from mdescriptor_studio_backend.analysis.algorithms.pairs import acquisition, compare, coverage, drift, mantel, overlap
from mdescriptor_studio_backend.analysis.algorithms.pca import pca
from mdescriptor_studio_backend.analysis.algorithms.sensitivity import perturbation_sensitivity, sensitivity
from mdescriptor_studio_backend.analysis.algorithms.tsne import MAX_ITERATIONS, tsne
from mdescriptor_studio_backend.analysis.algorithms.umap import umap
from mdescriptor_studio_backend.analysis.clustering import cluster, outlier
from mdescriptor_studio_backend.analysis.metrics import effective_dimension, feature_variance, local_diversity, neighbors, pairwise, similarity, trajectory
from mdescriptor_studio_backend.analysis.sampling.engine import sampling
from mdescriptor_studio_backend.errors import (
    ANALYSIS_INPUT_INVALID,
    ANALYSIS_INSUFFICIENT_SAMPLES,
    AppError,
)


@pytest.fixture()
def samples() -> StructureDescriptorMatrix:
    rng = np.random.default_rng(42)
    values = rng.normal(size=(48, 12)).astype(np.float32)
    return StructureDescriptorMatrix(
        values=values,
        frame=np.arange(values.shape[0], dtype=np.int64),
        sample_ids=[f"frame:{i}" for i in range(values.shape[0])],
    )


@pytest.mark.parametrize(
    ("name", "runner"),
    [
        ("pca", lambda s: pca(s, {})),
        ("umap", lambda s: umap(s, {"n_neighbors": 8})),
        ("tsne", lambda s: tsne(s, {"perplexity": 8, "max_iter": 250})),
        ("neighbors", lambda s: neighbors(s, {"k": 5})),
        ("similarity", lambda s: similarity(s, {"k": 5})),
        ("pairwise", lambda s: pairwise(s, {"max_samples": 24})),
        ("kmeans", lambda s: cluster(s, {"n_clusters": 4}, "kmeans")),
        ("dbscan", lambda s: cluster(s, {"min_samples": 3}, "dbscan")),
        ("hdbscan", lambda s: cluster(s, {"min_cluster_size": 3}, "hdbscan")),
        ("agglomerative", lambda s: cluster(s, {"n_clusters": 4}, "agglomerative")),
        ("outlier-knn", lambda s: outlier(s, {"k": 5}, "knn")),
        ("outlier-lof", lambda s: outlier(s, {"k": 5}, "lof")),
        ("outlier-iforest", lambda s: outlier(s, {}, "isolation_forest")),
        ("outlier-mahalanobis", lambda s: outlier(s, {}, "mahalanobis")),
        ("fps", lambda s: sampling(s, {"n_samples": 8}, "fps")),
        ("random", lambda s: sampling(s, {"n_samples": 8}, "random")),
        ("stratified", lambda s: sampling(s, {"n_samples": 8}, "stratified")),
        ("cluster-representative", lambda s: sampling(s, {"n_samples": 8}, "cluster_representative")),
        ("per-element", lambda s: sampling(StructureDescriptorMatrix(s.values, s.frame, elements=np.where(s.frame % 2, 31, 33)), {"n_samples": 8}, "per_element")),
        ("coverage", lambda s: coverage(s, s, {})),
        ("overlap", lambda s: overlap(s, s, {})),
        ("acquisition", lambda s: acquisition(s, s, {"n_samples": 8})),
        ("compare", lambda s: compare(s, s, {})),
        ("feature-variance", lambda s: feature_variance(s, {})),
        ("feature-correlation", lambda s: feature_correlation(s, {})),
        ("effective-dimension", lambda s: effective_dimension(s, {})),
        (
            "property-correlation",
            lambda s: property_correlation(
                StructureDescriptorMatrix(s.values, s.frame, properties={"energy_per_atom": s.values[:, 0] * 0.5 + s.values[:, 1]}),
                {"property": "energy_per_atom", "folds": 3},
            ),
        ),
        (
            "local-diversity",
            lambda s: local_diversity(
                AtomDescriptorMatrix(
                    s.values,
                    s.frame,
                    row=np.arange(s.n_samples),
                    elements=np.where(s.frame % 2, 31, 33),
                ),
                {"n_clusters": 3},
            ),
        ),
        ("kernel", lambda s: kernel(s, {"kernel": "rbf", "max_samples": 24})),
        ("trajectory", lambda s: trajectory(s, {"frame_start": 0, "frame_end": 47})),
        ("drift", lambda s: drift(s, s, {})),
        (
            "sensitivity",
            lambda s: sensitivity(
                [({"id": "r1", "parameters_json": "{}"}, s), ({"id": "r2", "parameters_json": "{}"}, s)],
                {},
            ),
        ),
    ],
)
def test_analysis_algorithms_return_artifacts(name: str, runner, samples: StructureDescriptorMatrix) -> None:
    result = runner(samples)
    assert result["arrays"], name
    for array in result["arrays"].values():
        assert np.asarray(array).dtype in (np.float64, np.int64), name
    assert isinstance(result["preview"], dict)


def test_sampling_is_deterministic_and_bounded(samples: StructureDescriptorMatrix) -> None:
    a = sampling(samples, {"n_samples": 10, "seed": 42}, "fps")
    b = sampling(samples, {"n_samples": 10, "seed": 42}, "fps")
    assert np.array_equal(a["arrays"]["selected_indices"], b["arrays"]["selected_indices"])
    assert len(a["arrays"]["selected_indices"]) == 10


def test_grouped_sampling_returns_exact_target_count() -> None:
    values = np.arange(40, dtype=np.float64).reshape(20, 2)
    frames = np.repeat(np.arange(4, dtype=np.int64), 5)
    structure_samples = StructureDescriptorMatrix(values, frames)
    stratified = sampling(structure_samples, {"n_samples": 10, "seed": 7}, "stratified")
    assert stratified["arrays"]["selected_indices"].size == 10
    assert np.unique(frames[stratified["arrays"]["selected_indices"]]).size == 4

    elements = np.repeat(np.array([14, 32], dtype=np.int64), 10)
    atom_samples = AtomDescriptorMatrix(values, frames, elements=elements)
    per_element = sampling(atom_samples, {"n_samples": 5, "seed": 7}, "per_element")
    selected = per_element["arrays"]["selected_indices"]
    assert selected.size == 5
    assert np.unique(elements[selected]).size == 2


def test_cross_dataset_distances_keep_constant_reference_features() -> None:
    reference = StructureDescriptorMatrix(np.array([[0.0, 0.0], [0.0, 1.0]]), np.arange(2))
    query = StructureDescriptorMatrix(np.array([[100.0, 0.5]]), np.array([0]))

    euclidean = coverage(reference, query, {"preprocess": "raw", "metric": "euclidean"})
    manhattan = coverage(reference, query, {"preprocess": "raw", "metric": "manhattan"})
    assert euclidean["arrays"]["distances"][0] > 99.0
    assert np.isclose(manhattan["arrays"]["distances"][0], 100.5)


def test_invalid_numeric_inputs_are_structured(samples: StructureDescriptorMatrix) -> None:
    with pytest.raises(AppError) as exc:
        pca(StructureDescriptorMatrix(np.array([[np.nan, 1.0], [2.0, 3.0]]), np.array([0, 1])), {})
    assert exc.value.code == ANALYSIS_INPUT_INVALID

    with pytest.raises(AppError) as exc:
        tsne(samples, {"perplexity": 100})
    assert exc.value.code == ANALYSIS_INPUT_INVALID

    with pytest.raises(AppError) as exc:
        trajectory(samples, {"frame_start": 0, "frame_end": 0})
    assert exc.value.code == ANALYSIS_INSUFFICIENT_SAMPLES


def test_feature_variance_reports_full_stats_robustness_and_invalid_counts() -> None:
    rng = np.random.default_rng(7)
    normal = rng.normal(size=8)
    values = np.column_stack(
        [
            np.ones(8),
            np.linspace(-1e-8, 1e-8, 8),
            normal,
            100.0 * normal,
            np.array([0.0, 1.0, -1.0, 0.0, 0.5, -0.5, 1000.0, 0.0]),
            np.array([0.0, np.nan, 1.0, np.inf, 2.0, 3.0, 4.0, 5.0]),
        ]
    )

    result = feature_variance(StructureDescriptorMatrix(values, np.arange(values.shape[0])), {})
    preview = result["preview"]
    features = {row["index"]: row for row in preview["features"]}

    assert preview["schema_version"] == 2
    assert preview["ddof"] == 0
    assert len(preview["features"]) == values.shape[1]
    assert features[0]["status"] == "constant"
    assert features[1]["status"] == "near_zero"
    assert features[4]["std_robust_ratio"] > 1.0
    assert features[4]["outlier_count"] > 0
    assert features[4]["whisker_max"] < features[4]["max"]
    assert int(result["arrays"]["histogram_counts"][4].sum()) == features[4]["finite_count"]
    sampled_values = result["arrays"]["distribution_samples"][4][: features[4]["distribution_sample_count"]]
    assert 1000.0 in sampled_values
    assert features[5]["finite_count"] == 6
    assert features[5]["invalid_count"] == 2
    assert result["arrays"]["histogram_edges"].shape == (6, 33)
    assert result["arrays"]["histogram_counts"].shape == (6, 32)
    assert not any(np.isnan(np.asarray(array, dtype=np.float64)).any() for array in result["arrays"].values())
    assert any("non-finite" in warning for warning in result["warnings"])

    zero = feature_variance(StructureDescriptorMatrix(np.zeros((4, 2)), np.arange(4)), {})
    assert zero["preview"]["summary"]["max_variance"] == 0.0
    assert all(row["status"] == "constant" for row in zero["preview"]["features"])


def test_wide_correlation_keeps_heatmap_bounded(samples: StructureDescriptorMatrix) -> None:
    rng = np.random.default_rng(7)
    wide = StructureDescriptorMatrix(rng.normal(size=(48, 1_000)), samples.frame)
    result = feature_correlation(wide, {"top_k": 12, "heatmap_features": 32})
    matrix = result["arrays"]["correlation_matrix"]
    assert matrix.shape == (32, 32)
    assert result["arrays"]["pairs"].shape == (12, 2)
    assert any("limited" in warning for warning in result["warnings"])


def test_feature_correlation_is_bounded_and_keeps_unit_diagonal() -> None:
    values = np.array([[0.0, 0.0, 1.0], [1.0, 1.0, 1.0], [2.0, 2.0, 1.0]])
    result = feature_correlation(StructureDescriptorMatrix(values, np.arange(3)), {})
    matrix = result["arrays"]["correlation_matrix"]
    assert np.allclose(np.diag(matrix), 1.0)
    assert np.all(np.abs(matrix) <= 1.0 + 1e-12)
    assert np.isclose(result["arrays"]["correlations"][0], 1.0)
    assert result["preview"]["correlation_metric"] == "pearson"
    assert result["preview"]["correlation_threshold"] == 0.95
    assert result["preview"]["clustered_feature_order"]


def test_feature_correlation_supports_spearman_rank_correlation() -> None:
    x = np.linspace(-2.0, 2.0, 25)
    values = np.column_stack([x, x**3, np.cos(x)])
    pearson = feature_correlation(StructureDescriptorMatrix(values, np.arange(x.size)), {"method": "pearson"})
    spearman = feature_correlation(StructureDescriptorMatrix(values, np.arange(x.size)), {"method": "spearman"})

    assert pearson["preview"]["correlation_metric"] == "pearson"
    assert spearman["preview"]["correlation_metric"] == "spearman"
    assert spearman["arrays"]["correlation_matrix"][0, 1] > 0.999999
    assert spearman["arrays"]["correlation_matrix"][0, 1] > pearson["arrays"]["correlation_matrix"][0, 1]

    with pytest.raises(AppError, match="pearson or spearman") as exc:
        feature_correlation(StructureDescriptorMatrix(values, np.arange(x.size)), {"method": "kendall"})
    assert exc.value.code == ANALYSIS_INPUT_INVALID


def test_property_correlation_reports_oof_encoding_association_and_reliability() -> None:
    rng = np.random.default_rng(42)
    values = rng.normal(size=(36, 6))
    target = values[:, 0] * 1.8 - values[:, 2] * 0.7 + rng.normal(scale=0.08, size=36)
    result = property_correlation(
        StructureDescriptorMatrix(values, np.arange(values.shape[0]), properties={"energy_per_atom": target}),
        {
            "property": "energy_per_atom",
            "folds": 4,
            "reliability_k": 3,
            "distance_metric": "cosine",
            "sparse_quantile": 0.85,
            "ood_quantile": 0.95,
        },
    )

    arrays = result["arrays"]
    preview = result["preview"]
    assert np.allclose(arrays["residuals"], arrays["predictions"] - arrays["targets"])
    assert arrays["oof_distances"].shape == target.shape
    assert np.array_equal(arrays["sample_frames"], np.arange(values.shape[0]))
    assert np.all(arrays["sample_rows"] == -1)
    assert arrays["absolute_errors"].shape == target.shape
    assert arrays["pearson_correlations"].shape == (values.shape[1],)
    assert arrays["spearman_correlations"].shape == (values.shape[1],)
    assert arrays["mutual_information"].shape == (values.shape[1],)
    assert preview["model"] == "Ridge"
    assert preview["cv_folds"] == 4
    assert preview["property_unit"] == "eV/atom"
    assert preview["distance_metric"] == "cosine"
    assert preview["reliability_k"] == 3
    assert preview["sparse_threshold"] < preview["ood_threshold"]
    assert -1.0 <= preview["distance_error_spearman"] <= 1.0
    assert preview["baseline_rmse"] > preview["rmse"]


def test_effective_dimension_reports_zero_thresholds_for_constant_input() -> None:
    values = np.ones((4, 2), dtype=np.float64)
    result = effective_dimension(StructureDescriptorMatrix(values, np.arange(4)), {})
    assert result["preview"]["participation_ratio"] == 0.0
    assert result["preview"]["components_for_threshold"] == {"0.9": 0, "0.95": 0, "0.99": 0}


def test_effective_dimension_reports_pca_basis_and_feature_counts() -> None:
    values = np.array([
        [0.0, 100.0, 0.0],
        [1.0, 110.0, 1.0],
        [2.0, 80.0, 4.0],
        [3.0, 130.0, 9.0],
    ])
    result = effective_dimension(StructureDescriptorMatrix(values, np.arange(4)), {"preprocess": "standardized"})
    preview = result["preview"]
    assert preview["preprocess"] == "standardized"
    assert preview["pca_basis"] == "correlation"
    assert preview["feature_count"] == 3
    assert preview["pca_feature_count"] == 3
    assert preview["component_count"] == 3

    centered = effective_dimension(StructureDescriptorMatrix(values, np.arange(4)), {"preprocess": "center"})
    assert centered["preview"]["pca_basis"] == "covariance"
    assert not np.allclose(result["arrays"]["explained_variance"], centered["arrays"]["explained_variance"])


def test_tsne_adapts_default_perplexity_for_small_inputs() -> None:
    values = np.arange(8, dtype=np.float64).reshape(4, 2)
    result = tsne(StructureDescriptorMatrix(values, np.arange(4)), {"max_iter": 250})
    assert result["preview"]["parameters"]["perplexity"] == 3.0


def test_tsne_caps_iterations_it_cannot_report_progress_for() -> None:
    """sklearn optimises without a callback, so iteration count is the only
    bound on how long the job runs before it can be cancelled."""
    values = np.arange(12, dtype=np.float64).reshape(6, 2)
    result = tsne(StructureDescriptorMatrix(values, np.arange(6)), {"max_iter": 100_000})
    assert result["preview"]["parameters"]["max_iter"] == MAX_ITERATIONS
    assert any("max_iter reduced" in warning for warning in result["warnings"])


def test_single_feature_matrix_runs_do_not_crash_unstructured() -> None:
    """One descriptor is a thin but legitimate input: the projection either
    returns finite coordinates or says so with a structured analysis error."""
    values = np.arange(5, dtype=np.float64).reshape(5, 1)
    samples = StructureDescriptorMatrix(values, np.arange(5))
    coords = umap(samples, {"n_neighbors": 3})["arrays"]["coords"]
    assert coords.shape == (5, 2)
    assert np.isfinite(coords).all()
    with pytest.raises(AppError) as exc:
        tsne(samples, {"perplexity": 2.0})
    assert exc.value.code == ANALYSIS_INPUT_INVALID


def test_compare_reports_geometry_and_neighbor_consistency(samples: StructureDescriptorMatrix) -> None:
    perturbed = StructureDescriptorMatrix(samples.values * 1.5 + 0.01, samples.frame, sample_ids=samples.sample_ids)
    result = compare(samples, perturbed, {"max_samples": 32, "k": 5})
    preview = result["preview"]
    assert preview["pairwise_distance_pearson"] > 0.99
    assert preview["pairwise_distance_spearman"] > 0.99
    assert preview["neighbor_overlap"] > 0.99
    assert "left_coords" in result["arrays"] and "right_coords" in result["arrays"]


def test_sensitivity_accepts_different_feature_dimensions(samples: StructureDescriptorMatrix) -> None:
    narrow = StructureDescriptorMatrix(samples.values[:, :3], samples.frame, sample_ids=samples.sample_ids)
    result = sensitivity(
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


def test_sensitivity_rejects_different_descriptors(samples: StructureDescriptorMatrix) -> None:
    with pytest.raises(AppError, match="same descriptor") as exc:
        sensitivity(
            [
                ({"id": "soap", "descriptor_name": "SOAP", "parameters_json": "{}"}, samples),
                ({"id": "acsf", "descriptor_name": "ACSF", "parameters_json": "{}"}, samples),
            ],
            {},
        )
    assert exc.value.code == ANALYSIS_INPUT_INVALID


def test_overlap_identity_is_detected_as_duplicate(samples: StructureDescriptorMatrix) -> None:
    result = overlap(samples, samples, {"duplicate_threshold": 1e-10})
    assert result["preview"]["near_duplicates"] == samples.n_samples
    assert np.all(result["arrays"]["nearest_indices"] == np.arange(samples.n_samples))


def test_feature_redundancy_summary_counts_correlated_columns() -> None:
    values = np.column_stack([np.arange(20), -np.arange(20), np.arange(20) * 2 + 1, np.sin(np.arange(20))]).astype(np.float64)
    result = feature_correlation(StructureDescriptorMatrix(values, np.arange(20)), {"correlation_threshold": 0.99})
    assert result["preview"]["highly_correlated_pairs"] >= 1
    assert result["preview"]["high_correlation_cluster_count"] == 1
    assert result["preview"]["involved_feature_count"] == 3
    assert result["preview"]["involved_feature_ratio"] == 0.75
    assert any(row["correlation"] < 0 for row in result["preview"]["pairs"])


def test_trajectory_includes_reference_distance_and_projection(samples: StructureDescriptorMatrix) -> None:
    result = trajectory(samples, {"frame_start": 0, "frame_end": 47})
    assert result["arrays"]["reference_distance"][0] == 0
    assert result["arrays"]["coords"].shape == (48, 2)
    assert result["arrays"]["cumulative_distance"][-1] >= result["arrays"]["step_distance"][-1]
    assert result["arrays"]["pc_explained_variance"].shape == (2,)
    assert 0.0 <= float(result["arrays"]["pc_explained_variance"].sum()) <= 1.0


def test_trajectory_event_detection_is_explicit_about_threshold_and_space() -> None:
    values = np.column_stack([np.linspace(0.0, 1.0, 40), np.zeros(40)]).astype(np.float64)
    values[20:] += 40.0
    frames = np.arange(40, dtype=np.int64)
    samples = StructureDescriptorMatrix(values, frames)

    result = trajectory(samples, {"event_method": "mad", "event_sensitivity": 3.0})
    preview = result["preview"]
    steps = result["arrays"]["step_distance"][1:]
    median = float(np.median(steps))
    mad = float(np.median(np.abs(steps - median)))
    assert preview["event_method"] == "mad"
    assert preview["event_sensitivity"] == 3.0
    assert np.isclose(preview["event_threshold"], median + 3.0 * 1.4826 * mad)
    assert preview["event_space"] == "descriptor"
    assert preview["event_count"] == len(preview["events"]) == 1
    assert preview["event_rate"] == pytest.approx(1 / steps.size)
    assert preview["max_step_distance"] == pytest.approx(float(steps.max()))
    assert preview["total_distance"] == pytest.approx(float(steps.sum()))

    assert result["arrays"]["event_indices"].tolist() == [20]
    event = preview["events"][0]
    assert event["frame"] == 20
    assert event["percentile"] == 1.0
    assert event["threshold_ratio"] > 1.0
    assert event["pc_displacement"] > 0.0

    # A sigma-based threshold is inflated by the single jump: the robust MAD
    # default must therefore stay available and documented.
    sigma = trajectory(samples, {"event_method": "zscore", "event_sensitivity": 3.0})["preview"]
    assert sigma["event_threshold"] > preview["event_threshold"]

    top = trajectory(samples, {"event_method": "percentile", "event_sensitivity": 5.0})["preview"]
    assert np.isclose(top["event_threshold"], float(np.quantile(steps, 0.95)))

    with pytest.raises(AppError) as exc:
        trajectory(samples, {"event_method": "unknown"})
    assert exc.value.code == ANALYSIS_INPUT_INVALID


def test_uncertainty_acquisition_exposes_knn_uncertainty_and_diversity(samples: StructureDescriptorMatrix) -> None:
    query = StructureDescriptorMatrix(
        samples.values + np.linspace(0.0, 0.25, samples.n_samples)[:, None],
        samples.frame,
        sample_ids=samples.sample_ids,
    )
    result = acquisition(
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


def test_mantel_reports_observed_statistic_and_permutation_p_value(samples: StructureDescriptorMatrix) -> None:
    right = StructureDescriptorMatrix(
        samples.values.copy(),
        samples.frame,
        sample_ids=samples.sample_ids,
    )
    result = mantel(
        samples,
        right,
        {"method": "spearman", "permutations": 31, "max_samples": 24, "seed": 42},
    )

    assert result["preview"]["kind"] == "mantel"
    assert result["preview"]["statistic"] > 0.99
    assert 0.0 <= result["preview"]["p_value"] <= 1.0
    assert result["arrays"]["null_distribution"].shape == (31,)
    assert result["arrays"]["left_pair_distances"].shape == result["arrays"]["right_pair_distances"].shape


def test_structural_perturbation_sensitivity_returns_sorted_response_curves(samples: StructureDescriptorMatrix) -> None:
    direction = np.linspace(0.1, 1.2, samples.n_samples * samples.n_features).reshape(samples.values.shape)
    perturbations = [
        (0.2, StructureDescriptorMatrix(samples.values + 0.2 * direction, samples.frame, sample_ids=samples.sample_ids)),
        (0.0, StructureDescriptorMatrix(samples.values.copy(), samples.frame, sample_ids=samples.sample_ids)),
        (0.1, StructureDescriptorMatrix(samples.values + 0.1 * direction, samples.frame, sample_ids=samples.sample_ids)),
    ]
    result = perturbation_sensitivity(samples, perturbations, {"metric": "euclidean"})

    assert np.array_equal(result["arrays"]["amplitudes"], np.array([0.0, 0.1, 0.2]))
    assert result["preview"]["baseline_included"] is True
    assert result["arrays"]["response_matrix"].shape == (3, samples.n_samples)
    assert np.allclose(result["arrays"]["mean_response"][0], 0.0)
    assert np.all(np.diff(result["arrays"]["mean_response"]) >= -1e-12)


def test_perturbation_response_keeps_constant_baseline_features() -> None:
    baseline = StructureDescriptorMatrix(
        np.array([[0.0, 1.0], [0.0, 2.0]]),
        np.arange(2),
        sample_ids=["a", "b"],
    )
    perturbed = StructureDescriptorMatrix(
        np.array([[1.0, 1.0], [1.0, 2.0]]),
        np.arange(2),
        sample_ids=["a", "b"],
    )
    result = perturbation_sensitivity(
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
    result = local_diversity(
        AtomDescriptorMatrix(
            values,
            np.zeros(4, dtype=np.int64),
            row=np.arange(4, dtype=np.int64),
            sample_ids=[f"frame:0:row:{i}" for i in range(4)],
            elements=np.full(4, 14, dtype=np.int64),
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
    result = local_diversity(
        AtomDescriptorMatrix(
            values,
            np.arange(3, dtype=np.int64),
            row=np.zeros(3, dtype=np.int64),
            sample_ids=[f"frame:{i}:row:0" for i in range(3)],
            elements=np.full(3, 14, dtype=np.int64),
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
    result = local_diversity(
        AtomDescriptorMatrix(
            values,
            np.array([0, 1, 0, 1], dtype=np.int64),
            row=np.arange(4, dtype=np.int64),
            sample_ids=[f"sample:{i}" for i in range(4)],
            elements=np.full(4, 14, dtype=np.int64),
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
    single = StructureDescriptorMatrix(np.array([[2.0, 3.0]]), np.array([0]), sample_ids=["one"])
    result = drift(single, single, {})
    assert result["preview"]["covariance_shift"] is None
    assert np.isfinite(result["preview"]["mmd"])
    assert any("covariance shift requires" in warning for warning in result["warnings"])


def test_sensitivity_reports_peak_memory_and_delta(samples: StructureDescriptorMatrix) -> None:
    result = sensitivity(
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
    baseline = StructureDescriptorMatrix(
        np.array([[0.0, 1.0], [1.0, 2.0], [2.0, 4.0], [3.0, 8.0]]),
        np.arange(4),
        sample_ids=[f"s{i}" for i in range(4)],
    )
    shifted = StructureDescriptorMatrix(
        baseline.values + 5.0,
        baseline.frame,
        sample_ids=baseline.sample_ids,
    )
    result = sensitivity(
        [
            ({"id": "base", "descriptor_name": "SOAP", "parameters_json": "{}"}, baseline),
            ({"id": "shifted", "descriptor_name": "SOAP", "parameters_json": "{}"}, shifted),
        ],
        {"max_samples": 4, "n_clusters": 2, "k": 1},
    )
    assert result["preview"]["runs"][1]["mean_delta_norm"] > 1.0
