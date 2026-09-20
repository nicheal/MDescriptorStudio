"""Dimension, neighbour, variance, and diversity metrics."""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, ANALYSIS_INSUFFICIENT_SAMPLES, AppError
from ..models import DescriptorMatrix
from ..algorithms._common import _as_float64, _bounded_indices, _check_samples, _effective_dimension_metrics, _float_param, _int_param, _local_neighbor_graph, _nearest_distances, _pairwise_matrix, _preprocess, _safe_import, _seed, _trajectory_threshold, _visual_pca, _visual_pca_components

def _feature_histogram(values: np.ndarray, bins: int) -> tuple[np.ndarray, np.ndarray]:
    """Bin one feature, or one centred bucket when it has no bin-able spread.

    np.histogram raises "Too many bins for data range" once a column's spread is
    a handful of ulps of its own magnitude: there are no `bins` distinct finite
    edges inside it. Such a column is constant for every purpose the histogram
    serves, and letting numpy fail there dropped the whole feature-variance job
    over one column - with a message naming neither the feature nor the cause -
    in the one panel whose contract is to report per-column faults instead.
    """
    try:
        return np.histogram(values, bins=bins)
    except ValueError:
        low, high = float(values.min()), float(values.max())
        pad = max(abs(low), abs(high)) * 1e-9 or 0.5
        edges = np.linspace(low - pad, high + pad, bins + 1)
        counts = np.zeros(bins, dtype=np.int64)
        counts[bins // 2] = values.size
        return counts, edges


def neighbors(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    x, warnings, keep = _preprocess(samples.values, params, "raw")
    k = _int_param(params, "k", 10, 1)
    metric = params.get("metric", "euclidean")
    if metric not in ("euclidean", "cosine", "manhattan"):
        raise AppError(ANALYSIS_INPUT_INVALID, "metric must be euclidean, cosine, or manhattan")
    indices, distances = _nearest_distances(x, k, metric)
    if progress:
        progress(1.0, "nearest neighbors complete")
    return {"arrays": {"indices": indices.astype(np.int64), "distances": distances.astype(np.float64)}, "preview": {"kind": "neighbors", "k": int(indices.shape[1]), "metric": metric}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}


def similarity(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    x, warnings, keep = _preprocess(samples.values, params, "raw")
    metric = params.get("metric", "cosine")
    if metric not in ("euclidean", "cosine"):
        raise AppError(ANALYSIS_INPUT_INVALID, "similarity metric must be euclidean or cosine")
    query = params.get("query_index")
    if query is None:
        query = 0
    query = int(query)
    if query < 0 or query >= x.shape[0]:
        raise AppError(ANALYSIS_INPUT_INVALID, "query_index is out of range")
    k = _int_param(params, "k", 10, 1)
    model = _safe_import("sklearn.neighbors", "scikit-learn").NearestNeighbors(n_neighbors=min(k + 1, x.shape[0]), metric=metric, n_jobs=1)
    distances, indices = model.fit(x).kneighbors(x[query : query + 1])
    mask = indices[0] != query
    indices = indices[0][mask][:k]
    distances = distances[0][mask][:k]
    similarity = 1.0 / (1.0 + distances) if metric == "euclidean" else 1.0 - distances
    if progress:
        progress(1.0, "similarity complete")
    return {"arrays": {"indices": indices.astype(np.int64), "distances": distances.astype(np.float64), "similarity": similarity.astype(np.float64)}, "preview": {"kind": "similarity", "query_index": query, "metric": metric}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}


def pairwise(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    x, warnings, keep = _preprocess(samples.values, params, "raw")
    metric = str(params.get("metric") or "cosine")
    limit = min(_int_param(params, "max_samples", 400, 2), 2_000)
    indices = _bounded_indices(x.shape[0], limit)
    if indices.size < x.shape[0]:
        warnings.append(f"pairwise matrix limited to {indices.size} deterministic samples")
    if progress:
        progress(0.2, "building pairwise matrix")
    distances = _pairwise_matrix(x[indices], metric)
    similarity = 1.0 - distances if metric == "cosine" else 1.0 / (1.0 + distances)
    # The matrix is square and its diagonal is zero by construction, so min over
    # the whole thing is 0.0 for every dataset and every metric - and "minimum
    # pairwise distance = 0" is exactly what this panel is read for: two
    # structures that turn out to be identical. Take the pairs, the way
    # _aligned_space_metrics already does.
    pairs = distances[np.triu_indices(indices.size, 1)] if indices.size > 1 else np.empty(0)
    if progress:
        progress(1.0, "pairwise matrix complete")
    return {
        "arrays": {
            "sample_indices": indices,
            "distance_matrix": distances,
            "similarity_matrix": similarity.astype(np.float64),
        },
        "preview": {
            "kind": "pairwise_similarity",
            "metric": metric,
            "sample_count": int(indices.size),
            "total_samples": int(x.shape[0]),
            # The matrix is square and its diagonal is zero by construction, so
            # `distances.min()` reported 0.0 for every dataset and every metric -
            # and "minimum pairwise distance = 0" is precisely what this panel is
            # read for: two structures that are identical. Off-diagonal only, the
            # way _aligned_space_metrics already takes them.
            "distance_min": float(pairs.min()) if pairs.size else None,
            "distance_max": float(distances.max()),
        },
        "warnings": warnings,
        "feature_indices": np.flatnonzero(keep).astype(np.int64),
    }


def feature_variance(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    x = _as_float64(samples.values, allow_nonfinite=True)
    feature_count = int(x.shape[1])
    sample_count = int(x.shape[0])
    near_zero_threshold = _float_param(params, "near_zero_relative_threshold", 1e-4, 0.0)
    low_variation_threshold = _float_param(params, "low_variance_relative_threshold", 1e-2, 0.0)
    if low_variation_threshold < near_zero_threshold:
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "low_variance_relative_threshold must be >= near_zero_relative_threshold",
        )
    constant_tolerance = _float_param(params, "constant_tolerance", 1e-12, 0.0)
    histogram_bins = _int_param(params, "histogram_bins", 32, 8)
    if histogram_bins > 128:
        raise AppError(ANALYSIS_INPUT_INVALID, "histogram_bins must be <= 128")
    distribution_sample_size = _int_param(params, "distribution_sample_size", 20_000, 1)
    distribution_sample_size = min(distribution_sample_size, 20_000)
    # Bound the total sample artifact as descriptor width grows. The
    # sample is only for KDE/box visualisation; all scalar statistics below
    # use every finite value.
    distribution_capacity = min(
        sample_count,
        distribution_sample_size,
        max(1, 2_000_000 // max(feature_count, 1)),
    )

    finite_counts = np.isfinite(x).sum(axis=0).astype(np.int64)
    invalid_counts = (sample_count - finite_counts).astype(np.int64)
    variance = np.zeros(feature_count, dtype=np.float64)
    std = np.zeros(feature_count, dtype=np.float64)
    means = np.zeros(feature_count, dtype=np.float64)
    minima = np.zeros(feature_count, dtype=np.float64)
    maxima = np.zeros(feature_count, dtype=np.float64)
    percentiles = np.zeros((feature_count, 5), dtype=np.float64)
    iqr = np.zeros(feature_count, dtype=np.float64)
    mad = np.zeros(feature_count, dtype=np.float64)
    robust_sigma = np.zeros(feature_count, dtype=np.float64)
    # Persist finite arrays only. A missing ratio is represented as None
    # in the feature record and as zero in this companion array.
    std_robust_ratio = np.zeros(feature_count, dtype=np.float64)
    histogram_edges = np.zeros((feature_count, histogram_bins + 1), dtype=np.float64)
    histogram_counts = np.zeros((feature_count, histogram_bins), dtype=np.int64)
    distribution_samples = np.zeros((feature_count, distribution_capacity), dtype=np.float64)
    distribution_sample_counts = np.zeros(feature_count, dtype=np.int64)
    feature_records: list[dict[str, Any]] = []

    for index in range(feature_count):
        values = x[:, index]
        finite_values = values[np.isfinite(values)]
        count = int(finite_values.size)
        record: dict[str, Any] = {
            "index": index,
            "mean": None,
            "variance": None,
            "relative_variance": None,
            "std": None,
            "min": None,
            "max": None,
            "p05": None,
            "p25": None,
            "median": None,
            "p75": None,
            "p95": None,
            "iqr": None,
            "mad": None,
            "robust_sigma": None,
            "std_robust_ratio": None,
            "whisker_min": None,
            "whisker_max": None,
            "outlier_count": 0,
            "finite_count": count,
            "invalid_count": int(invalid_counts[index]),
            # Keep the name used by the original development plan as a
            # compatibility alias; invalid_count is the canonical field.
            "missing_count": int(invalid_counts[index]),
            "distribution_sample_count": 0,
            "status": "invalid",
        }
        if count:
            mean = float(np.mean(finite_values))
            centered = finite_values - mean
            feature_variance = float(np.mean(centered * centered))
            feature_std = float(np.sqrt(feature_variance))
            quantile_values = np.percentile(finite_values, [5, 25, 50, 75, 95])
            median = float(quantile_values[2])
            feature_iqr = float(quantile_values[3] - quantile_values[1])
            feature_mad = float(np.median(np.abs(finite_values - median)))
            feature_robust_sigma = float(1.4826 * feature_mad)
            ratio = (
                float(feature_std / feature_robust_sigma)
                if feature_robust_sigma > np.finfo(np.float64).eps
                else None
            )
            lower_fence = float(quantile_values[1] - 1.5 * feature_iqr)
            upper_fence = float(quantile_values[3] + 1.5 * feature_iqr)
            outlier_mask = (finite_values < lower_fence) | (finite_values > upper_fence)
            outlier_indices = np.flatnonzero(outlier_mask)
            inlier_values = finite_values[~outlier_mask]
            whisker_min = float(np.min(inlier_values)) if inlier_values.size else float(quantile_values[1])
            whisker_max = float(np.max(inlier_values)) if inlier_values.size else float(quantile_values[3])
            outlier_count = int(outlier_indices.size)
            means[index] = mean
            variance[index] = feature_variance
            std[index] = feature_std
            minima[index] = float(np.min(finite_values))
            maxima[index] = float(np.max(finite_values))
            percentiles[index] = quantile_values
            iqr[index] = feature_iqr
            mad[index] = feature_mad
            robust_sigma[index] = feature_robust_sigma
            if ratio is not None:
                std_robust_ratio[index] = ratio

            edges: np.ndarray
            counts: np.ndarray
            counts, edges = _feature_histogram(finite_values, histogram_bins)
            histogram_counts[index] = counts.astype(np.int64, copy=False)
            histogram_edges[index] = edges.astype(np.float64, copy=False)
            sample_count_for_feature = min(count, distribution_capacity)
            if sample_count_for_feature:
                if outlier_count >= sample_count_for_feature:
                    outlier_positions = np.linspace(0, outlier_count - 1, sample_count_for_feature, dtype=np.int64)
                    sample_indices = outlier_indices[outlier_positions]
                elif outlier_count:
                    regular_count = sample_count_for_feature - outlier_count
                    regular_indices = np.linspace(0, count - 1, regular_count, dtype=np.int64)
                    # Preserve every detected outlier when the bounded
                    # sample has room; this keeps a long tail visible in
                    # the detail panel even for large descriptor runs.
                    sample_indices = np.unique(np.concatenate((outlier_indices, regular_indices)))
                else:
                    sample_indices = np.linspace(0, count - 1, sample_count_for_feature, dtype=np.int64)
                sample_count_for_feature = int(sample_indices.size)
                distribution_samples[index, :sample_count_for_feature] = finite_values[sample_indices]
                distribution_sample_counts[index] = sample_count_for_feature
            is_constant = float(np.ptp(finite_values)) <= constant_tolerance
            record.update(
                {
                    "mean": mean,
                    "variance": feature_variance,
                    "std": feature_std,
                    "min": float(minima[index]),
                    "max": float(maxima[index]),
                    "p05": float(quantile_values[0]),
                    "p25": float(quantile_values[1]),
                    "median": median,
                    "p75": float(quantile_values[3]),
                    "p95": float(quantile_values[4]),
                    "iqr": feature_iqr,
                    "mad": feature_mad,
                    "robust_sigma": feature_robust_sigma,
                    "std_robust_ratio": ratio,
                    "whisker_min": whisker_min,
                    "whisker_max": whisker_max,
                    "outlier_count": outlier_count,
                    "distribution_sample_count": int(sample_count_for_feature),
                    "status": "constant" if is_constant else "pending",
                }
            )
        feature_records.append(record)

    finite_variances = variance[finite_counts > 0]
    max_variance = float(np.max(finite_variances)) if finite_variances.size else 0.0
    relative_variance = variance / max_variance if max_variance > 0 else np.zeros(feature_count, dtype=np.float64)
    for index, record in enumerate(feature_records):
        if record["status"] == "invalid":
            continue
        if record["status"] == "constant":
            continue
        relative = float(relative_variance[index])
        record["status"] = (
            "near_zero"
            if relative < near_zero_threshold
            else "low_variation"
            if relative < low_variation_threshold
            else "active"
        )
        record["relative_variance"] = relative
    for index, record in enumerate(feature_records):
        if record["status"] == "constant":
            record["relative_variance"] = float(relative_variance[index])

    order = np.argsort(-variance, kind="stable")
    top_k = min(_int_param(params, "top_k", 20, 1), feature_count)
    status_values = np.asarray([record["status"] for record in feature_records], dtype=object)
    near_zero = status_values == "near_zero"
    constant = status_values == "constant"
    low_variation = status_values == "low_variation"
    active = status_values == "active"
    invalid = status_values == "invalid"
    summary_variances = finite_variances if finite_variances.size else np.asarray([0.0])
    warnings: list[str] = []
    invalid_total = int(invalid_counts.sum())
    if invalid_total:
        warnings.append(
            f"ignored {invalid_total} non-finite feature value(s) across {int(np.count_nonzero(invalid_counts))} feature(s)"
        )
    if progress:
        progress(0.8, "feature variance statistics complete")
        progress(1.0, "feature variance complete")
    return {
        "arrays": {
            "variance": variance,
            "relative_variance": relative_variance,
            "std": std,
            "iqr": iqr,
            "mad": mad,
            "std_robust_ratio": std_robust_ratio,
            "near_zero_mask": near_zero.astype(np.int64),
            "constant_mask": constant.astype(np.int64),
            "finite_count": finite_counts,
            "invalid_count": invalid_counts,
            "histogram_edges": histogram_edges,
            "histogram_counts": histogram_counts,
            "distribution_samples": distribution_samples,
            "distribution_sample_counts": distribution_sample_counts,
        },
        "preview": {
            "kind": "feature_variance",
            "schema_version": 2,
            "sample_count": sample_count,
            "feature_count": feature_count,
            "ddof": 0,
            "settings": {
                "near_zero_relative_threshold": near_zero_threshold,
                "low_variance_relative_threshold": low_variation_threshold,
                "constant_tolerance": constant_tolerance,
                "histogram_bins": histogram_bins,
                "distribution_sample_limit": distribution_sample_size,
            },
            "summary": {
                "max_variance": max_variance,
                "median_variance": float(np.median(summary_variances)),
                "min_variance": float(np.min(summary_variances)),
                "near_zero_count": int(near_zero.sum()),
                "constant_count": int(constant.sum()),
                "low_variation_count": int(low_variation.sum()),
                "active_count": int(active.sum()),
                "invalid_count": int(invalid.sum()),
                "invalid_value_count": invalid_total,
                "effective_nonzero_dimensions": int((active | low_variation).sum()),
            },
            "top_k": top_k,
            "top_indices": order[:top_k].astype(np.int64).tolist(),
            "top_values": variance[order[:top_k]].astype(np.float64).tolist(),
            "features": feature_records,
        },
        "warnings": warnings,
    }


def local_diversity(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    if samples.row is None:
        raise AppError(ANALYSIS_INPUT_INVALID, "local diversity requires atom/local-environment samples")
    x, warnings, keep = _preprocess(samples.values, params, "standardized")
    _check_samples(x, 3)
    elements = np.asarray(samples.elements if samples.elements is not None else np.zeros(x.shape[0]), dtype=np.int64)
    cutoff = _float_param(params, "cutoff", 3.0, 0.1)
    max_neighbors = _int_param(params, "max_neighbors", 128, 1)
    # The neighbour search is the slow phase of this analysis, and the only one
    # whose length the user cannot guess from the sample count (cutoff and cell
    # geometry decide it), so it gets its own part of the bar - and with it the
    # runner's cooperative cancellation, which only happens inside progress.
    graph_progress = (lambda fraction, message: progress(0.6 * fraction, message)) if progress else None
    coordination_all, neighbor_offsets_all, neighbor_indices_all, neighbor_distances_all, graph_warnings = _local_neighbor_graph(
        samples, cutoff, max_neighbors, graph_progress
    )
    warnings.extend(graph_warnings)
    selected_element = params.get("element")
    if selected_element is not None:
        selected_element = int(selected_element)
        mask = elements == selected_element
        if int(mask.sum()) < 3:
            raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, f"element {selected_element} has fewer than three local environments")
        x = x[mask]
        elements = elements[mask]
        sample_indices = np.flatnonzero(mask).astype(np.int64)
    else:
        sample_indices = np.arange(x.shape[0], dtype=np.int64)
    selected_coordination = coordination_all[sample_indices]
    # The coordination number counts every contact; only the stored neighbour
    # list is budgeted, so say plainly when the two differ (deep review P1-16).
    capped_atoms = int((selected_coordination > max_neighbors).sum())
    if capped_atoms:
        warnings.append(f"{capped_atoms} atom(s) have more than {max_neighbors} contacts; neighbour lists keep the nearest {max_neighbors}")
    selected_neighbor_indices: list[int] = []
    selected_neighbor_distances: list[float] = []
    selected_neighbor_offsets = np.zeros(sample_indices.size + 1, dtype=np.int64)
    for output_index, original_index in enumerate(sample_indices.tolist()):
        lo, hi = int(neighbor_offsets_all[original_index]), int(neighbor_offsets_all[original_index + 1])
        selected_neighbor_indices.extend(int(value) for value in neighbor_indices_all[lo:hi].tolist())
        selected_neighbor_distances.extend(float(value) for value in neighbor_distances_all[lo:hi].tolist())
        selected_neighbor_offsets[output_index + 1] = len(selected_neighbor_indices)
    coords = _visual_pca(x)
    categories = np.zeros(x.shape[0], dtype=np.int64)
    scores = np.zeros(x.shape[0], dtype=np.float64)
    cluster_labels = np.full(x.shape[0], -1, dtype=np.int64)
    summaries = []
    cluster_offset = 0
    for group_index, element in enumerate(np.unique(elements).tolist()):
        members = np.flatnonzero(elements == element)
        group = x[members]
        if group.shape[0] > 1:
            k = min(_int_param(params, "k", 10, 1), group.shape[0] - 1)
            group_scores = _nearest_distances(group, k, "euclidean")[1].mean(axis=1)
        else:
            group_scores = np.zeros(group.shape[0], dtype=np.float64)
        q90 = float(np.quantile(group_scores, 0.90))
        q99 = float(np.quantile(group_scores, 0.99))
        group_categories = np.where(group_scores <= q90, 0, np.where(group_scores <= q99, 1, 2)).astype(np.int64)
        categories[members] = group_categories
        scores[members] = group_scores
        cluster_count = min(_int_param(params, "n_clusters", 6, 2), group.shape[0])
        if group.shape[0] >= 2:
            kmeans = _safe_import("sklearn.cluster", "scikit-learn").KMeans(
                n_clusters=cluster_count,
                random_state=_seed(params),
                n_init=10,
            )
            group_clusters = np.asarray(kmeans.fit_predict(group), dtype=np.int64)
        else:
            group_clusters = np.zeros(group.shape[0], dtype=np.int64)
        cluster_labels[members] = group_clusters + cluster_offset
        cluster_offset += int(group_clusters.max()) + 1 if group_clusters.size else 0
        counts = np.bincount(group_clusters) if group_clusters.size else np.zeros(0, dtype=np.int64)
        effective_dimension, _thresholds = _effective_dimension_metrics(group)
        summaries.append({
            "element": int(element),
            "samples": int(group.shape[0]),
            "clusters": int(np.unique(group_clusters).size),
            "main_cluster_fraction": float(counts.max() / max(group.shape[0], 1)) if counts.size else 0.0,
            "distorted": int((group_categories == 1).sum()),
            "outliers": int((group_categories == 2).sum()),
            "median_neighbor_distance": float(np.median(group_scores)),
            "median_coordination": float(np.median(coordination_all[sample_indices[members]])) if members.size else 0.0,
            "max_coordination": int(coordination_all[sample_indices[members]].max()) if members.size else 0,
            "effective_dimension": effective_dimension,
        })
        if progress:
            progress(0.6 + 0.4 * (group_index + 1) / max(len(np.unique(elements)), 1), "summarizing local environments")
    return {
        "arrays": {
            "sample_indices": sample_indices,
            "coords": coords,
            "labels": categories,
            "scores": scores,
            "cluster_labels": cluster_labels,
            "elements": elements,
            "coordination": selected_coordination,
            "neighbor_offsets": selected_neighbor_offsets,
            "neighbor_indices": np.asarray(selected_neighbor_indices, dtype=np.int64),
            "neighbor_distances": np.asarray(selected_neighbor_distances, dtype=np.float64),
        },
        "preview": {
            "kind": "local_diversity",
            "categories": ["main", "distorted", "outlier"],
            "element_summary": summaries,
            "sample_count": int(x.shape[0]),
            "cutoff": cutoff,
            "max_neighbors": max_neighbors,
            "mean_coordination": float(selected_coordination.mean()) if selected_coordination.size else 0.0,
            "max_coordination": int(selected_coordination.max()) if selected_coordination.size else 0,
            "coordination_capped_atoms": capped_atoms,
            "neighbor_count": int(len(selected_neighbor_indices)),
            "neighbor_graph_available": bool(samples.positions is not None),
            "selected_element": selected_element,
        },
        "warnings": warnings,
        "feature_indices": np.flatnonzero(keep).astype(np.int64),
    }


def effective_dimension(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    effective_params = dict(params or {})
    preprocess = effective_params.get("preprocess")
    if preprocess is None or preprocess == "":
        preprocess = "standardized"
        effective_params["preprocess"] = preprocess
    x, warnings, keep = _preprocess(samples.values, effective_params, "standardized")
    _check_samples(x, 2)
    singular = np.linalg.svd(x, compute_uv=False, full_matrices=False)
    eigen = (singular * singular) / max(x.shape[0] - 1, 1)
    total = float(eigen.sum())
    normalized = eigen / total if total > 0 else np.zeros_like(eigen)
    participation = float(1.0 / np.sum(normalized * normalized)) if total > 0 else 0.0
    cumulative = np.cumsum(normalized)
    thresholds = (
        {str(t): int(np.searchsorted(cumulative, t) + 1) for t in (0.9, 0.95, 0.99)}
        if total > 0
        else {str(t): 0 for t in (0.9, 0.95, 0.99)}
    )
    pca_basis = {
        "standardized": "correlation",
        "center": "covariance",
        "raw": "uncentered_second_moment",
    }[preprocess]
    if progress:
        progress(1.0, "effective dimension complete")
    return {
        "arrays": {
            "eigenvalues": eigen.astype(np.float64),
            "explained_variance": normalized.astype(np.float64),
        },
        "preview": {
            "kind": "effective_dimension",
            "preprocess": preprocess,
            "pca_basis": pca_basis,
            "sample_count": int(samples.n_samples),
            "feature_count": int(samples.n_features),
            "pca_feature_count": int(x.shape[1]),
            "component_count": int(eigen.size),
            "participation_ratio": participation,
            "components_for_threshold": thresholds,
        },
        "warnings": warnings,
        "feature_indices": np.flatnonzero(keep).astype(np.int64),
    }


def trajectory(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    frames = np.asarray(samples.frame, dtype=np.int64)
    start = _int_param(params, "frame_start", int(frames.min()) if frames.size else 0, 0)
    end = _int_param(params, "frame_end", int(frames.max()) if frames.size else start, start)
    step = _int_param(params, "frame_step", 1, 1)
    selected = np.flatnonzero((frames >= start) & (frames <= end) & (((frames - start) % step) == 0))
    if selected.size < 2:
        raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "trajectory range contains fewer than two samples")
    x, warnings, _keep = _preprocess(samples.values[selected], params, "standardized")
    # Three distinct quantities: how much the descriptor moves between
    # neighbouring frames, how far it has drifted from the reference frame,
    # and how long the explored path is.  Only the first one detects events.
    deltas = np.linalg.norm(np.diff(x, axis=0), axis=1)
    step_distance = np.concatenate([[0.0], deltas])
    reference_distance = np.linalg.norm(x - x[0], axis=1)
    cumulative_distance = np.cumsum(step_distance)
    coords, pc_explained_variance = _visual_pca_components(x)
    if params.get("timestep") is not None:
        timestep = _float_param(params, "timestep", 1.0, 0.0)
        time_axis = frames[selected].astype(np.float64) * timestep
        time_unit = str(params.get("time_unit") or "arb. units")
    else:
        time_axis = frames[selected].astype(np.float64)
        time_unit = "frame"
    time_delta = np.diff(time_axis, prepend=time_axis[0])
    speed = np.divide(step_distance, time_delta, out=np.zeros_like(step_distance), where=time_delta > 0)
    event_method, event_sensitivity, event_threshold, step_stats, threshold_warnings = _trajectory_threshold(deltas, params)
    warnings.extend(threshold_warnings)
    event_indices = np.flatnonzero(step_distance > event_threshold)
    if progress:
        progress(1.0, "trajectory analysis complete")
    # Percentile of each step among all steps, so one event reads as
    # "larger than 99.6% of the trajectory" without recomputation.
    step_ranks = np.argsort(np.argsort(deltas, kind="stable"), kind="stable")
    event_details = [
        {
            "index": int(index),
            "frame": int(frames[selected][index]),
            "step_distance": float(step_distance[index]),
            "threshold_ratio": float(step_distance[index] / event_threshold) if event_threshold > 0 else None,
            "percentile": float((step_ranks[index - 1] + 1) / deltas.size),
            "reference_distance": float(reference_distance[index]),
            "pc1": float(coords[index, 0]),
            "pc2": float(coords[index, 1]),
            "pc_displacement": float(np.linalg.norm(coords[index] - coords[index - 1])),
        }
        for index in event_indices[:200].tolist()
    ]
    return {
        "arrays": {
            "sample_indices": selected.astype(np.int64),
            "indices": selected.astype(np.int64),
            "frames": frames[selected],
            "time": time_axis,
            "step_distance": step_distance,
            "reference_distance": reference_distance.astype(np.float64),
            "cumulative_distance": cumulative_distance.astype(np.float64),
            "speed": speed.astype(np.float64),
            "coords": coords,
            "pc_explained_variance": pc_explained_variance,
            "event_indices": event_indices.astype(np.int64),
        },
        "preview": {
            "kind": "trajectory",
            "frame_start": start,
            "frame_end": end,
            "frame_step": step,
            "time_unit": time_unit,
            "sample_count": int(selected.size),
            "total_distance": float(deltas.sum()),
            "max_step_distance": float(deltas.max()),
            "max_reference_distance": float(reference_distance.max()),
            "median_step_distance": step_stats["median"],
            "mean_step_distance": step_stats["mean"],
            "step_mad": step_stats["mad"],
            "step_robust_sigma": step_stats["robust_sigma"],
            "step_std": step_stats["std"],
            "event_method": event_method,
            "event_sensitivity": event_sensitivity,
            "event_threshold": event_threshold,
            "event_count": int(event_indices.size),
            "event_rate": float(event_indices.size / deltas.size),
            "event_space": "descriptor",
            "detection_note": "Event detection uses descriptor-space step distances; the PCA panel is a visualization only.",
            "pc1_explained_variance": float(pc_explained_variance[0]),
            "pc2_explained_variance": float(pc_explained_variance[1]),
            "pc_explained_variance_sum": float(pc_explained_variance.sum()),
            "events": event_details,
        },
        "warnings": warnings,
    }



class Neighbors:
    name = "neighbors"
    category = "engine"

    def run(self, data, params: dict, progress=None) -> dict:
        return neighbors(data, params, progress)


class Similarity:
    name = "similarity"
    category = "engine"

    def run(self, data, params: dict, progress=None) -> dict:
        return similarity(data, params, progress)


class Pairwise:
    name = "pairwise"
    category = "engine"

    def run(self, data, params: dict, progress=None) -> dict:
        return pairwise(data, params, progress)


class FeatureVariance:
    name = "feature_variance"
    category = "engine"

    def run(self, data, params: dict, progress=None) -> dict:
        return feature_variance(data, params, progress)


class LocalDiversity:
    name = "local_diversity"
    category = "engine"

    def run(self, data, params: dict, progress=None) -> dict:
        return local_diversity(data, params, progress)


class EffectiveDimension:
    name = "effective_dimension"
    category = "engine"

    def run(self, data, params: dict, progress=None) -> dict:
        return effective_dimension(data, params, progress)


class Trajectory:
    name = "trajectory"
    category = "engine"

    def run(self, data, params: dict, progress=None) -> dict:
        return trajectory(data, params, progress)

