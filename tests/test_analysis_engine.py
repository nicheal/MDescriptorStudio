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
from mdescriptor_studio_backend.analysis.algorithms._common import _local_neighbor_graph, _preprocess, _rank_correlation, _safe_correlation, _trajectory_threshold
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


def test_drift_states_how_many_rows_its_kernel_numbers_describe() -> None:
    """The strip above the drift result mixes two populations: the distance
    statistics cover every query row while MMD, centroid and covariance shifts are
    computed on a bounded sample. Nothing on screen said so (pass 5, 5-C6)."""
    rng = np.random.default_rng(9)
    left = StructureDescriptorMatrix(rng.normal(0.0, 1.0, (60, 4)), np.arange(60))
    right = StructureDescriptorMatrix(rng.normal(0.6, 1.0, (60, 4)), np.arange(60))
    preview = drift(left, right, {"distribution_samples": 25, "seed": 3})["preview"]
    assert preview["mmd_reference_rows"] == 25 and preview["mmd_query_rows"] == 25
    # The distance side still describes all 60 query rows.
    assert preview["covered"] + preview["marginal"] + preview["out_of_coverage"] == 60
    uncapped = drift(left, right, {"seed": 3})["preview"]
    assert uncapped["mmd_query_rows"] == 60
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
        ("stratified", lambda s: sampling(s, {"n_samples": 8, "stratification_source": "composition"}, "stratified", group_labels=s.frame % 4)),
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
    stratified = sampling(
        structure_samples,
        {"n_samples": 10, "seed": 7, "stratification_source": "composition"},
        "stratified",
        group_labels=frames,
    )
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
        trajectory(StructureDescriptorMatrix(np.zeros((1, 2)), np.array([0])), {})
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

    singleton = feature_variance(
        StructureDescriptorMatrix(
            np.array([[np.nan], [np.nan], [2.31], [np.nan]], dtype=np.float64),
            np.arange(4),
        ),
        {},
    )
    assert singleton["preview"]["features"][0]["status"] == "insufficient"
    assert singleton["preview"]["summary"]["insufficient_count"] == 1

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
    assert preview["requested_reliability_k"] == 3
    assert preview["effective_reliability_k_min"] == 3
    assert preview["effective_reliability_k_max"] == 3
    assert preview["sparse_threshold"] < preview["ood_threshold"]
    assert -1.0 <= preview["distance_error_spearman"] <= 1.0
    assert preview["baseline_rmse"] > preview["rmse"]


def test_property_correlation_rejects_an_empty_informative_descriptor_space() -> None:
    values = np.ones((8, 3), dtype=np.float64)
    target = np.arange(8, dtype=np.float64)
    with pytest.raises(AppError) as exc:
        property_correlation(
            StructureDescriptorMatrix(values, np.arange(8), properties={"energy_per_atom": target}),
            {"property": "energy_per_atom", "folds": 3},
        )
    assert exc.value.code == ANALYSIS_INPUT_INVALID


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


def test_uncancellable_tsne_refuses_oversized_input() -> None:
    values = np.zeros((10_001, 2), dtype=np.float64)
    with pytest.raises(AppError, match="cannot be cancelled") as exc:
        tsne(StructureDescriptorMatrix(values, np.arange(values.shape[0])), {})
    assert exc.value.code == ANALYSIS_INPUT_INVALID


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


def test_mahalanobis_scores_say_when_the_covariance_cannot_be_estimated() -> None:
    # A few hundred structures over a couple of hundred SOAP features is the
    # Studio's normal shape, and there n - 1 < d: pinv inverts a covariance whose
    # rank is at most n - 1, so the score lives in a span-dimensional subspace of
    # the feature space and every direction outside it is simply unmeasured. The
    # panel reported an outlier_count as though the estimate were full rank.
    # The review's stronger claim - that a departure along an unoccupied direction
    # ranks *last* - did not reproduce: such a point adds that direction to the
    # covariance itself, and is still the one flagged, which is asserted below.
    rows, columns = 50, 60
    rng = np.random.default_rng(3)
    values = rng.normal(size=(rows, 8)) @ rng.normal(size=(8, columns))
    unoccupied = np.linalg.svd(values - values.mean(axis=0), full_matrices=True)[2][-1]
    values[0] += 500.0 * unoccupied

    result = outlier(StructureDescriptorMatrix(values, np.arange(rows, dtype=np.int64)), {}, "mahalanobis")
    healthy = outlier(
        StructureDescriptorMatrix(rng.normal(size=(400, 20)), np.arange(400, dtype=np.int64)), {}, "mahalanobis"
    )

    assert any("Mahalanobis scores span 9 of 60 directions with 50 samples" in warning for warning in result["warnings"]), result["warnings"]
    assert result["arrays"]["labels"][0] == 1, "the lone departure is still the point the panel flags"
    assert not any("Mahalanobis" in warning for warning in healthy["warnings"]), healthy["warnings"]


def test_mahalanobis_refuses_one_sample_instead_of_failing_inside_pinv() -> None:
    # np.cov of a single sample is all-NaN, and pinv of that raises LinAlgError
    # out of the job; knn and lof already answer with a structured error here.
    single = StructureDescriptorMatrix(np.array([[1.0, 2.0]]), np.array([0], dtype=np.int64))
    with pytest.raises(AppError) as exc:
        outlier(single, {}, "mahalanobis")
    assert exc.value.code == ANALYSIS_INSUFFICIENT_SAMPLES


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


def test_trajectory_display_filter_does_not_change_full_trajectory_statistics() -> None:
    values = np.column_stack([np.linspace(0.0, 1.0, 40), np.zeros(40)]).astype(np.float64)
    values[20:] += 40.0
    samples = StructureDescriptorMatrix(values, np.arange(40, dtype=np.int64))

    full = trajectory(samples, {})
    filtered = trajectory(samples, {"frame_start": 8, "frame_end": 20, "frame_step": 3})

    assert filtered["preview"]["sample_count"] == 40
    assert filtered["preview"]["display_sample_count"] == 5
    assert filtered["arrays"]["step_distance"].shape == (40,)
    assert filtered["preview"]["event_threshold"] == pytest.approx(full["preview"]["event_threshold"])


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


def test_acquisition_reports_the_score_that_drove_each_pick(samples: StructureDescriptorMatrix) -> None:
    """`pick_scores` records the objective each pick actually saw, and that trace
    is now non-increasing: both terms are normalised on a ruler fixed before the
    loop, min-diversity can only shrink, and every step maximises a subset of what
    the previous step could reach.  `scores` is the same formula evaluated once at
    the end, so it answers a different question - but it agrees with the last pick
    exactly, and the per-sample estimates must not be pool-restricted
    (deep review P1-17; pass-4 B-6)."""
    # A query that shares no row with the reference: distances (and therefore
    # novelty/uncertainty) are strictly positive, so a zero in either array can
    # only come from a value that was dropped on the way out.
    query = StructureDescriptorMatrix(
        samples.values + np.linspace(0.4, 0.9, samples.n_samples)[:, None],
        samples.frame,
        sample_ids=samples.sample_ids,
    )
    result = acquisition(samples, query, {"n_samples": 6, "pool_factor": 2.0})
    arrays = result["arrays"]
    picks = np.asarray(arrays["pick_scores"], dtype=np.float64)
    selected = np.asarray(arrays["selected_indices"], dtype=np.int64)

    assert len(picks) == len(selected) == 6
    assert np.isfinite(picks).all()
    # The seed pick maximises the min-max normalised base score, whose maximum
    # is exactly 1.0 -- proof the trace records the objective, not a post-hoc
    # recomputation.
    assert picks[0] == pytest.approx(1.0)
    assert bool((np.diff(picks) <= 1e-12).all()), f"the objective each pick saw must not rise: {picks}"
    assert float(picks[-1]) == pytest.approx(float(arrays["scores"][selected[-1]]), abs=1e-15)
    assert bool((arrays["diversity"] <= 1.0).all())

    # Uncertainty and novelty are per-sample estimates: every query row has
    # one, whether or not it entered the candidate pool.
    assert (arrays["uncertainty"] > 0.0).all()
    assert (arrays["novelty"] > 0.0).all()
    # `scores` stays pool-restricted and now has to be read that way: the pool
    # here is 6 * 2 = 12 of 48 samples, so the rest are placeholders.
    assert int((arrays["scores"] > 0.0).sum()) <= 12


def test_cross_dataset_algorithms_share_one_scale_and_say_so(samples: StructureDescriptorMatrix) -> None:
    """Deep review P1-14: coverage measured distances on the raw matrix while
    overlap and acquisition standardised the same pair, so one panel could give
    opposite verdicts for one input -- and no stored result recorded which
    scale it had used."""
    query_values = samples.values + 0.05
    query = StructureDescriptorMatrix(query_values, samples.frame, sample_ids=samples.sample_ids)

    scales = {
        "coverage": coverage(samples, query, {})["preview"]["preprocess"],
        "overlap": overlap(samples, query, {})["preview"]["preprocess"],
        "acquisition": acquisition(samples, query, {"n_samples": 4})["preview"]["preprocess"],
    }
    assert scales == {"coverage": "standardized", "overlap": "standardized", "acquisition": "standardized"}

    # The scale is not just a label: one column carrying different units (here
    # a factor of 1000 applied to both sides) must not move a standardised
    # comparison, while a raw one stretches with it.
    factor = np.ones(samples.n_features)
    factor[0] = 1000.0
    both = coverage(
        StructureDescriptorMatrix(samples.values * factor, samples.frame, sample_ids=samples.sample_ids),
        StructureDescriptorMatrix(query_values * factor, samples.frame, sample_ids=samples.sample_ids),
        {},
    )["preview"]
    assert both["mean_distance"] == pytest.approx(coverage(samples, query, {})["preview"]["mean_distance"], rel=1e-9)

    raw = coverage(samples, query, {"preprocess": "raw"})["preview"]
    raw_scaled = coverage(
        StructureDescriptorMatrix(samples.values * factor, samples.frame, sample_ids=samples.sample_ids),
        StructureDescriptorMatrix(query_values * factor, samples.frame, sample_ids=samples.sample_ids),
        {"preprocess": "raw"},
    )["preview"]
    assert raw["preprocess"] == "raw"
    assert raw_scaled["mean_distance"] > raw["mean_distance"] * 10


def test_ulp_jitter_never_becomes_a_feature_axis() -> None:
    """Deep review P1-15: a column that only jitters by a few float64 ulps
    around 1000.0 passed the old eps test, was divided by its own scale, and
    then carried as much weight in every distance as a real descriptor."""
    rows = 40
    signal = np.random.default_rng(5).normal(size=(rows, 3))
    jitter = 1000.0 + np.arange(rows, dtype=np.float64) * np.spacing(1000.0)
    assert float(jitter.std()) > np.finfo(np.float64).eps  # exactly what the old test let through
    matrix = StructureDescriptorMatrix(
        np.hstack([signal, jitter[:, None]]),
        np.arange(rows, dtype=np.int64),
        sample_ids=[f"frame:{index}" for index in range(rows)],
    )

    result = pca(matrix, {"preprocess": "standardized"})

    assert result["feature_indices"].tolist() == [0, 1, 2]
    assert any("zero-variance feature" in warning for warning in result["warnings"])


def test_a_column_too_narrow_to_bin_is_reported_not_fatal() -> None:
    # The same 口径2 example column, but through feature_variance: np.histogram
    # raises "Too many bins for data range" once a spread is a few ulps of its
    # magnitude, and one such column used to fail the whole job with a numpy
    # message naming neither the feature nor the cause - in the one panel whose
    # contract is to report per-column faults.
    rows = 500
    signal = np.random.default_rng(4).normal(size=(rows, 2))
    jitter = 1000.0 + np.arange(rows, dtype=np.float64) * np.spacing(1000.0)
    matrix = StructureDescriptorMatrix(
        np.hstack([signal, jitter[:, None], np.full((rows, 1), 7.0)]),
        np.arange(rows, dtype=np.int64),
        sample_ids=[f"frame:{index}" for index in range(rows)],
    )

    result = feature_variance(matrix, {})

    edges = np.asarray(result["arrays"]["histogram_edges"], dtype=np.float64)
    counts = np.asarray(result["arrays"]["histogram_counts"], dtype=np.int64)
    assert edges.shape == (4, 33) and counts.shape == (4, 32)
    assert bool(np.isfinite(edges).all()), "every edge must stay encodable"
    assert bool(np.all(np.diff(edges, axis=1) > 0)), "no repeated edge"
    assert counts.sum(axis=1).tolist() == [rows] * 4, "every sample lands in a bucket"
    statuses = [record["status"] for record in result["preview"]["features"]]
    assert statuses[0] == statuses[1] == "active", statuses
    # Column 2 is the ulp jitter and column 3 is exactly 7.0. Neither has room
    # for 32 finite bin edges, so both are reported instead of killing the run.
    # Column 2 used to come back "near_zero": its range (5.7e-11) is above the
    # absolute constant_tolerance, while _preprocess drops the same column for
    # being rounding noise at its own magnitude. One owner now decides, and the
    # two panels agree - see the B-3 note in the pass-4 report.
    assert statuses[2] == "constant", statuses
    assert statuses[3] == "constant", statuses


def test_the_constant_verdict_has_one_owner_across_the_three_sites() -> None:
    # Three places decided "this feature carries nothing" three different ways:
    # feature_correlation cut on variance, which is in squared units;
    # feature_variance cut on the absolute range; and _preprocess - the pass every
    # distance, PCA and coverage threshold runs through - used the relative rule.
    # So a real feature with a small amplitude and a column that is pure rounding
    # noise at 1e6 got opposite verdicts in different panels.
    rows = 400
    rng = np.random.default_rng(4)
    values = np.column_stack(
        [
            rng.normal(size=rows),
            rng.normal(size=rows) * 1e-8,
            1e6 + np.arange(rows, dtype=np.float64) * np.spacing(1e6),
            np.full(rows, 7.0),
        ]
    )
    matrix = StructureDescriptorMatrix(
        values,
        np.arange(rows, dtype=np.int64),
        sample_ids=[f"frame:{index}" for index in range(rows)],
    )

    kept = np.asarray(feature_correlation(matrix, {})["arrays"]["correlation_feature_indices"], dtype=np.int64).tolist()
    preprocessed = _preprocess(values, {}, "standardized")[2].tolist()
    statuses = [row["status"] for row in feature_variance(matrix, {})["preview"]["features"]]

    assert kept == [0, 1], "column 1 is small but real; column 2 is noise at its own magnitude"
    assert preprocessed == [True, True, False, False], preprocessed
    assert statuses[0] == "active"
    assert statuses[2:] == ["constant", "constant"], statuses


def test_pairwise_minimum_ignores_the_zero_diagonal() -> None:
    # `distances.min()` over the whole square matrix is the diagonal: the panel's
    # "Minimum" read 0.0 for every dataset and every metric, and "minimum pairwise
    # distance = 0" is exactly what this panel is read for - two identical
    # structures.
    values = np.array([[0.0, 0.0], [5.0, 0.0], [0.0, 12.0], [7.0, 7.0]])
    matrix = StructureDescriptorMatrix(
        values, np.arange(4, dtype=np.int64), sample_ids=[f"frame:{index}" for index in range(4)]
    )

    result = pairwise(matrix, {"max_samples": 4, "metric": "euclidean"})

    distances = np.asarray(result["arrays"]["distance_matrix"], dtype=np.float64)
    truth = float(distances[np.triu_indices(4, 1)].min())
    assert result["preview"]["distance_min"] == truth
    assert result["preview"]["distance_min"] > 0.0
    with pytest.raises(AppError) as exc:
        pairwise(
            StructureDescriptorMatrix(values[:1], np.arange(1, dtype=np.int64), sample_ids=["frame:0"]),
            {"max_samples": 4},
        )
    assert exc.value.code == ANALYSIS_INPUT_INVALID


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


@pytest.mark.parametrize("method", ["pearson", "spearman"])
def test_mantel_permutations_match_the_textbook_definition(samples: StructureDescriptorMatrix, method: str) -> None:
    """Check the permutation loop against the definition, not against itself.

    Spearman ranks the pair vector once and gathers ranks, and both methods
    gather from a matrix instead of recomputing distances - the shortcut is the
    entire speedup, so it needs an oracle that re-ranks and re-correlates every
    permutation from scratch.  The grid here is rebuilt from the returned pair
    vectors, which also pins that a permutation only relabels pairs and leaves
    their multiset alone.
    """
    right = StructureDescriptorMatrix(
        samples.values + 0.25 * np.roll(samples.values, 1, axis=1),
        samples.frame,
        sample_ids=samples.sample_ids,
    )
    params = {"method": method, "permutations": 23, "max_samples": 26, "seed": 7}
    result = mantel(samples, right, params)
    size = result["preview"]["sample_count"]
    rows, columns = np.triu_indices(size, 1)
    left_pairs = result["arrays"]["left_pair_distances"]
    grid = np.zeros((size, size), dtype=np.float64)
    grid[rows, columns] = result["arrays"]["right_pair_distances"]
    grid[columns, rows] = grid[rows, columns]
    correlate = _rank_correlation if method == "spearman" else _safe_correlation
    rng = np.random.default_rng(params["seed"])
    reference = []
    for _ in range(params["permutations"]):
        permutation = rng.permutation(size)
        reference.append(correlate(left_pairs, grid[permutation[rows], permutation[columns]]))
    reference = np.asarray(reference, dtype=np.float64)

    observed = result["preview"]["statistic"]
    # abs=1e-12 rather than exact: sklearn's euclidean gram trick is only
    # symmetric to ~1e-15, and the Pearson branch gathers from that matrix.
    assert observed == pytest.approx(correlate(left_pairs, grid[rows, columns]), abs=1e-12)
    np.testing.assert_allclose(result["arrays"]["null_distribution"], reference, rtol=0.0, atol=1e-12)
    assert result["preview"]["p_value"] == pytest.approx(
        float((int((np.abs(reference) >= abs(observed)).sum()) + 1) / (params["permutations"] + 1)), abs=1e-12
    )


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


def test_coordination_counts_contacts_the_neighbour_list_drops() -> None:
    """Deep review P1-16: the coordination number used to be the length of the
    `max_neighbors`-budgeted neighbour list, so a dense environment reported the
    display budget as a physical property."""
    centre = [0.0, 0.0, 0.0]
    shell = [[0.1, 0, 0], [-0.1, 0, 0], [0, 0.1, 0], [0, -0.1, 0], [0, 0, 0.1], [0, 0, -0.1]]
    positions = np.array([centre, *shell], dtype=np.float64)
    result = local_diversity(
        AtomDescriptorMatrix(
            np.random.default_rng(7).normal(size=(7, 2)),
            np.zeros(7, dtype=np.int64),
            row=np.arange(7, dtype=np.int64),
            sample_ids=[f"frame:0:row:{i}" for i in range(7)],
            elements=np.full(7, 14, dtype=np.int64),
            positions=positions,
            cells=np.repeat(np.eye(3, dtype=np.float64)[None, :, :], 7, axis=0),
            pbc=np.zeros((7, 3), dtype=bool),
        ),
        # 0.12 keeps the six shell atoms out of each other's shells, so only the
        # centre has more contacts than the budget stores.
        {"cutoff": 0.12, "max_neighbors": 2, "n_clusters": 2, "k": 1},
    )

    coordination = result["arrays"]["coordination"]
    assert coordination.tolist() == [6, 1, 1, 1, 1, 1, 1]
    offsets = np.asarray(result["arrays"]["neighbor_offsets"], dtype=np.int64)
    assert np.diff(offsets).tolist() == [2, 1, 1, 1, 1, 1, 1]
    assert result["preview"]["coordination_capped_atoms"] == 1
    assert any("more than 2 contacts" in warning for warning in result["warnings"])


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


def test_local_diversity_element_filter_gathers_the_selected_neighbour_rows() -> None:
    """Selecting one element must return the CSR rows of exactly those atoms, in
    order - the gather that used to rebuild these arrays with a Python loop per
    atom (58 ms of this analysis on 20 000 atoms; 2 ms vectorised) and that is
    skipped outright when no filter is active."""
    values = np.array(
        [[0.0, 1.0], [1.0, 0.0], [2.0, 1.0], [3.0, 0.0], [4.0, 1.0], [5.0, 0.5]],
        dtype=np.float64,
    )
    positions = np.array(
        [[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [5.0, 0.0, 0.0],
         [5.1, 0.0, 0.0], [9.0, 0.0, 0.0], [9.1, 0.0, 0.0]],
        dtype=np.float64,
    )
    # Three close pairs, alternating Si/Ga, so either element still clears the
    # three-environment floor _check_samples imposes. The cell has to hold those
    # coordinates: in a 1 A periodic box every position is a lattice translate of
    # every other, and minimum-image wrapping would put all six atoms inside each
    # other's cutoff.
    elements = np.array([14, 31, 14, 31, 14, 31], dtype=np.int64)

    def graph(sel_element: int | None):
        params: dict = {"cutoff": 0.2, "max_neighbors": 8, "n_clusters": 2, "k": 1}
        if sel_element is not None:
            params["element"] = sel_element
        return local_diversity(
            AtomDescriptorMatrix(
                values, np.zeros(6, dtype=np.int64), row=np.arange(6, dtype=np.int64),
                sample_ids=[f"frame:0:row:{i}" for i in range(6)],
                elements=elements, positions=positions,
                cells=np.repeat((np.eye(3, dtype=np.float64) * 10.0)[None, :, :], 6, axis=0),
                pbc=np.ones((6, 3), dtype=bool),
            ),
            params,
        )["arrays"]

    unfiltered = graph(None)
    # Rows are 0:[1], 1:[0], 2:[3], 3:[2], 4:[5], 5:[4] - pairs only, because the
    # 5 A between pairs is far outside the 0.2 A cutoff and the one-image stencil
    # of a 10 A cell.
    assert unfiltered["neighbor_offsets"].tolist() == [0, 1, 2, 3, 4, 5, 6]
    assert unfiltered["neighbor_indices"].tolist() == [1, 0, 3, 2, 5, 4]

    ga = graph(31)
    assert ga["sample_indices"].tolist() == [1, 3, 5]
    # The three Ga rows keep their own shells, concatenated in row order.
    assert ga["neighbor_offsets"].tolist() == [0, 1, 2, 3]
    assert ga["neighbor_indices"].tolist() == [0, 2, 4]
    assert np.allclose(ga["neighbor_distances"], 0.1)
    assert ga["neighbor_indices"].dtype == np.int64 and ga["neighbor_distances"].dtype == np.float64

    si = graph(14)
    assert si["sample_indices"].tolist() == [0, 2, 4]
    assert si["neighbor_offsets"].tolist() == [0, 1, 2, 3]
    assert si["neighbor_indices"].tolist() == [1, 3, 5]


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


def test_neighbour_search_excludes_self_by_identity_not_by_position() -> None:
    # Column 0 of knn(x, k+1) is only reliably the query itself while no other
    # row coincides with it. The old [:, 1:] slice assumed it always was, so for
    # duplicate rows it dropped a genuine neighbour and kept the row's own index
    # at distance 0 — which then averaged into every neighbour statistic.
    from mdescriptor_studio_backend.analysis.algorithms._common import _nearest_distances

    x = np.array([[1.0, 2.0], [1.0, 2.0], [1.0, 2.0], [5.0, 5.0]])
    indices, distances = _nearest_distances(x, 2)
    assert indices.shape == (4, 2)
    for row, others in enumerate(indices.tolist()):
        assert row not in others, f"row {row} lists itself as a neighbour"
        assert len(set(others)) == 2
    assert distances[3].tolist() == pytest.approx([distances[3][0]] * 2)
    assert distances[3][0] > 0.0

    # Distinct rows keep the previous shape exactly.
    plain = np.array([[0.0], [1.0], [4.0], [9.0]])
    plain_indices, plain_distances = _nearest_distances(plain, 1)
    assert plain_indices.tolist() == [[1], [0], [1], [2]]
    assert plain_distances[:, 0].tolist() == pytest.approx([1.0, 1.0, 3.0, 5.0])

    # More neighbours than there are other rows: never self, never padding.
    many_indices, many_distances = _nearest_distances(x, 10)
    assert many_indices.shape == (4, 3)
    for row, others in enumerate(many_indices.tolist()):
        assert row not in others
    assert np.isfinite(many_distances).all()


def test_event_threshold_falls_back_when_the_mad_degenerates() -> None:
    """Deep review P1-13: a trajectory that records every step twice (or a
    quantized descriptor) has median == MAD == 0, and the MAD rule then flagged
    about half of the steps as events -- with no threshold ratio attached,
    because a threshold of zero makes the ratio meaningless."""
    # Most steps identical, a handful of real jumps: the median lands on
    # the repeated value and every deviation from it is also repeated.
    steps = np.concatenate([np.zeros(30), np.full(10, 0.4)])

    _method, _sensitivity, threshold, stats, warnings = _trajectory_threshold(steps, {})

    assert stats["mad"] == 0.0
    assert threshold == pytest.approx(float(steps.mean() + 3.0 * steps.std()))
    assert threshold > steps.max()
    assert any("fell back to mean" in warning for warning in warnings)
    # Asking for zscore directly is not a fallback and says so.
    assert _trajectory_threshold(steps, {"event_method": "zscore"})[4] == []


def test_local_neighbor_graph_reports_progress_per_query_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """The neighbour search is the slowest part of local diversity and progress
    is the runner's only cancellation point, so it has to report as it goes and
    hand back a bar that ends where the next phase starts."""
    import mdescriptor_studio_backend.analysis.algorithms._common as common

    monkeypatch.setattr(common, "_GRAPH_QUERY_BLOCK", 4)
    rng = np.random.default_rng(4)
    samples = AtomDescriptorMatrix(
        rng.normal(size=(30, 3)),
        np.zeros(30, dtype=np.int64),
        row=np.arange(30, dtype=np.int64),
        sample_ids=[f"frame:0:row:{i}" for i in range(30)],
        elements=np.full(30, 14, dtype=np.int64),
        positions=rng.random((30, 3)),
    )
    fractions: list[float] = []

    _local_neighbor_graph(samples, 0.4, 128, lambda fraction, _message: fractions.append(fraction))

    assert len(fractions) >= 8  # one per block of four atoms, not one per frame
    assert fractions == sorted(fractions)
    assert fractions[-1] == pytest.approx(1.0)

    # Through the analysis, the graph keeps the first 60% of the job bar and the
    # element summaries the rest, so neither phase claims the whole run.
    reported: list[float] = []
    local_diversity(samples, {"cutoff": 0.4, "n_clusters": 2, "k": 2}, lambda fraction, _m: reported.append(fraction))
    assert reported == sorted(reported)
    assert min(reported) < 0.6
    assert max(reported) == pytest.approx(1.0)


def test_comparison_knn_overlap_excludes_self_by_identity() -> None:
    """`_nearest_distances` already refuses to drop a neighbour by position; the
    aligned-space overlap did not, and both sides of a comparison leaking their own
    index inflate the number together (deep review pass 5, 5-C5).

    Duplicate-rich input: for the second row of a duplicate pair the query itself
    sorts behind its twin, so `[:, 1:k+1]` keeps the row and loses a real
    neighbour, and the two spaces agree on that leaked index by construction.
    """
    from mdescriptor_studio_backend.analysis.algorithms._common import _aligned_space_metrics

    # Two unrelated 60-row descriptor spaces, each with 20 exact duplicate rows
    # copied from a *different* slice. A duplicated row then sorts behind its
    # twin, so the positional slice keeps the query itself and drops the twin -
    # and because both sides do it, the leaked self index matches across the two
    # spaces and is counted as agreement.
    a = np.random.default_rng(5).normal(0.0, 3.0, (60, 4))
    b = np.random.default_rng(6).normal(0.0, 3.0, (60, 4))
    left = np.vstack([a, a[:20]])
    right = np.vstack([b, b[40:60]])
    k = 10
    metrics, _arrays = _aligned_space_metrics(left, right, {"k": k, "max_samples": 600, "n_clusters": 2})

    def neighbours(matrix: np.ndarray, by_identity: bool) -> list[set[int]]:
        squared = ((matrix[:, None, :] - matrix[None, :, :]) ** 2).sum(axis=-1)
        rows = []
        for i in range(matrix.shape[0]):
            order = np.argsort(squared[i], kind="stable")
            picks = [j for j in order.tolist() if j != i][:k] if by_identity else order[1 : k + 1].tolist()
            rows.append(set(int(j) for j in picks))
        return rows

    def overlap(by_identity: bool) -> float:
        pairs = zip(neighbours(left, by_identity), neighbours(right, by_identity))
        return float(np.mean([len(a & b) / k for a, b in pairs]))

    assert metrics["neighbor_overlap"] == pytest.approx(overlap(True))
    # The input is chosen so the two rules actually differ: the old slice reports
    # +2.25 points of agreement that belongs to no neighbour pair.
    assert overlap(False) > overlap(True)
