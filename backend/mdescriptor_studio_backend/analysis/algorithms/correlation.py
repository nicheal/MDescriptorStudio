"""Analysis algorithms: correlation."""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, ANALYSIS_INSUFFICIENT_SAMPLES, AppError
from ..models import DescriptorMatrix
from ._common import _as_float64, _bounded_indices, _check_samples, _clustered_feature_order, _connected_component_count, _float_param, _int_param, _preprocess, _rank_correlation, _safe_correlation, _safe_import, _seed

def feature_correlation(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    x = _as_float64(samples.values)
    _check_samples(x, 2)
    method = str(params.get("method") or "pearson").lower()
    if method not in ("pearson", "spearman"):
        raise AppError(ANALYSIS_INPUT_INVALID, "feature correlation method must be pearson or spearman")
    variance = x.var(axis=0)
    variance_threshold = _float_param(params, "variance_threshold", 1e-12, 0.0)
    valid = variance > variance_threshold
    warnings = [f"ignored {int((~valid).sum())} zero-variance feature(s)"] if not bool(valid.all()) else []
    xv = x[:, valid]
    if xv.shape[1] < 2:
        raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "at least two non-constant features are required")
    top_k = _int_param(params, "top_k", 50, 1)
    correlation_input = xv
    if method == "spearman":
        rankdata = _safe_import("scipy.stats", "scipy").rankdata
        correlation_input = np.asarray(rankdata(xv, axis=0, method="average"), dtype=np.float64)
    standardized = (correlation_input - correlation_input.mean(axis=0)) / correlation_input.std(axis=0)
    # Only keep a bounded heatmap artifact.  Ranking pairs is the default
    # contract and avoids shipping an accidental D x D matrix over IPC.
    max_heatmap = min(_int_param(params, "heatmap_features", 256, 2), 512)
    if xv.shape[1] > max_heatmap:
        # For very wide descriptors, do not materialise the full D x D
        # correlation matrix just to discover the heatmap subset.  The
        # highest-variance features are a deterministic, useful subset.
        candidate = np.argsort(-variance[valid], kind="stable")[:max_heatmap]
        corr_input = standardized[:, candidate]
        bounded_matrix = (corr_input.T @ corr_input) / max(xv.shape[0], 1)
        feature_indices = np.flatnonzero(valid)[candidate]
        warnings.append(f"correlation heatmap limited to {max_heatmap} highest-variance features")
    else:
        bounded_matrix = (standardized.T @ standardized) / max(xv.shape[0], 1)
        feature_indices = np.flatnonzero(valid)
    tri = np.triu_indices(bounded_matrix.shape[0], 1)
    order = np.argsort(-np.abs(bounded_matrix[tri]), kind="stable")[:top_k]
    pairs = np.column_stack([feature_indices[tri[0][order]], feature_indices[tri[1][order]]]).astype(np.int64)
    values = bounded_matrix[tri[0][order], tri[1][order]].astype(np.float64)
    arrays: dict[str, np.ndarray] = {"pairs": pairs, "correlations": values}
    arrays["correlation_matrix"] = bounded_matrix.astype(np.float64)
    arrays["correlation_feature_indices"] = feature_indices.astype(np.int64)
    threshold_name = "correlation_threshold" if "correlation_threshold" in params else "redundancy_threshold"
    correlation_threshold = _float_param(params, threshold_name, 0.95, 0.0, 1.0)
    high_pairs = np.argwhere(np.triu(np.abs(bounded_matrix) >= correlation_threshold, 1))
    involved_features = np.unique(feature_indices[high_pairs]) if high_pairs.size else np.empty(0, dtype=np.int64)
    clustered_feature_order = _clustered_feature_order(bounded_matrix, feature_indices)
    component_count = _connected_component_count(high_pairs, bounded_matrix.shape[0])
    feature_count = int(x.shape[1])
    if progress:
        progress(1.0, "feature correlation complete")
    return {
        "arrays": arrays,
        "preview": {
            "kind": "feature_correlation",
            "schema_version": 3,
            "correlation_metric": method,
            "correlation_threshold": correlation_threshold,
            "feature_count": feature_count,
            "valid_feature_count": int(xv.shape[1]),
            "zero_variance_count": int((~valid).sum()),
            "highly_correlated_pairs": int(high_pairs.shape[0]),
            "high_correlation_cluster_count": component_count,
            "involved_feature_count": int(involved_features.size),
            "involved_feature_ratio": float(involved_features.size / max(feature_count, 1)),
            "clustered_feature_order": clustered_feature_order.astype(np.int64).tolist(),
            "heatmap_feature_count": int(feature_indices.size),
            "heatmap_limited": bool(xv.shape[1] > max_heatmap),
            "pairs": [
                {
                    "feature_a": int(a),
                    "feature_b": int(b),
                    "correlation": float(v),
                    "absolute_correlation": float(abs(v)),
                }
                for (a, b), v in zip(pairs.tolist(), values.tolist())
            ],
        },
        "warnings": warnings,
    }


def property_correlation(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    if progress:
        progress(0.0, "preparing property correlations")
    property_name = str(params.get("property") or "energy_per_atom")
    target = samples.properties.get(property_name)
    if target is None:
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            f"property {property_name!r} is unavailable for this descriptor run",
            {"available_properties": sorted(samples.properties)},
        )
    target = np.asarray(target, dtype=np.float64).reshape(-1)
    if target.size != samples.n_samples:
        raise AppError(ANALYSIS_INPUT_INVALID, "property values are not aligned with descriptor samples")
    valid = np.isfinite(target)
    if int(valid.sum()) < 3:
        raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "property correlation requires at least three finite targets")
    y = target[valid]
    if float(y.std()) <= np.finfo(np.float64).eps:
        raise AppError(ANALYSIS_INPUT_INVALID, f"property {property_name!r} is constant")
    raw_x = _as_float64(samples.values[valid])
    x, warnings, keep = _preprocess(raw_x, {"preprocess": "standardized"}, "standardized")
    model_x = raw_x[:, keep]
    standardized_target = (y - y.mean()) / y.std()
    correlations = (x.T @ standardized_target) / max(x.shape[0], 1)
    stats = _safe_import("scipy.stats", "scipy")
    ranked_x = np.asarray(stats.rankdata(model_x, axis=0, method="average"), dtype=np.float64)
    ranked_y = np.asarray(stats.rankdata(y, method="average"), dtype=np.float64)
    ranked_x = (ranked_x - ranked_x.mean(axis=0)) / ranked_x.std(axis=0)
    ranked_y = (ranked_y - ranked_y.mean()) / ranked_y.std()
    spearman_correlations = (ranked_x.T @ ranked_y) / max(ranked_x.shape[0], 1)

    if progress:
        progress(0.1, "computing feature-property mutual information")
    sklearn_feature_selection = _safe_import("sklearn.feature_selection", "scikit-learn")
    mutual_information = np.asarray(
        sklearn_feature_selection.mutual_info_regression(
            model_x,
            y,
            n_neighbors=min(3, max(y.size - 1, 1)),
            random_state=_seed(params),
        ),
        dtype=np.float64,
    )
    order = np.argsort(-np.abs(correlations), kind="stable")
    top_k = min(_int_param(params, "top_k", 20, 1), correlations.size)

    sklearn_linear = _safe_import("sklearn.linear_model", "scikit-learn")
    sklearn_model_selection = _safe_import("sklearn.model_selection", "scikit-learn")
    sklearn_pipeline = _safe_import("sklearn.pipeline", "scikit-learn")
    sklearn_preprocessing = _safe_import("sklearn.preprocessing", "scikit-learn")
    sklearn_metrics = _safe_import("sklearn.metrics", "scikit-learn")
    folds = min(_int_param(params, "folds", 5, 2), x.shape[0])
    splitter = sklearn_model_selection.KFold(n_splits=folds, shuffle=True, random_state=_seed(params))
    model = sklearn_pipeline.make_pipeline(
        sklearn_preprocessing.StandardScaler(),
        sklearn_linear.Ridge(alpha=_float_param(params, "alpha", 1.0, 0.0)),
    )
    if progress:
        progress(0.25, "cross-validating property regression")
    sklearn_base = _safe_import("sklearn.base", "scikit-learn")
    sklearn_neighbors = _safe_import("sklearn.neighbors", "scikit-learn")
    distance_metric = str(params.get("distance_metric") or "euclidean").lower()
    if distance_metric not in ("euclidean", "cosine"):
        raise AppError(ANALYSIS_INPUT_INVALID, "property reliability distance_metric must be euclidean or cosine")
    reliability_k = _int_param(params, "reliability_k", 5, 1)
    predictions = np.empty(y.shape[0], dtype=np.float64)
    baseline_predictions = np.empty(y.shape[0], dtype=np.float64)
    oof_distances = np.empty(y.shape[0], dtype=np.float64)
    for fold_index, (train_indices, test_indices) in enumerate(splitter.split(model_x)):
        if progress:
            progress(0.25 + 0.65 * fold_index / folds, f"cross-validating property regression: fold {fold_index + 1}/{folds}")
        fold_model = sklearn_base.clone(model)
        fold_model.fit(model_x[train_indices], y[train_indices])
        predictions[test_indices] = fold_model.predict(model_x[test_indices])
        baseline_predictions[test_indices] = float(y[train_indices].mean())

        scaler = sklearn_preprocessing.StandardScaler()
        train_scaled = scaler.fit_transform(model_x[train_indices])
        test_scaled = scaler.transform(model_x[test_indices])
        fold_k = min(reliability_k, train_indices.size)
        neighbor_model = sklearn_neighbors.NearestNeighbors(n_neighbors=fold_k, metric=distance_metric, n_jobs=1)
        neighbor_model.fit(train_scaled)
        distances, _neighbor_indices = neighbor_model.kneighbors(test_scaled)
        oof_distances[test_indices] = distances.mean(axis=1)

    if progress:
        progress(0.9, "summarizing property reliability")
    r2 = float(sklearn_metrics.r2_score(y, predictions))
    rmse = float(np.sqrt(sklearn_metrics.mean_squared_error(y, predictions)))
    mae = float(sklearn_metrics.mean_absolute_error(y, predictions))
    baseline_r2 = float(sklearn_metrics.r2_score(y, baseline_predictions))
    baseline_rmse = float(np.sqrt(sklearn_metrics.mean_squared_error(y, baseline_predictions)))
    baseline_mae = float(sklearn_metrics.mean_absolute_error(y, baseline_predictions))
    residuals = predictions - y
    absolute_errors = np.abs(residuals)
    distance_error_pearson = _safe_correlation(oof_distances, absolute_errors)
    distance_error_spearman = _rank_correlation(oof_distances, absolute_errors)

    sparse_quantile = _float_param(params, "sparse_quantile", 0.90, 0.5, 0.99)
    ood_quantile = _float_param(params, "ood_quantile", 0.99, sparse_quantile, 0.999)
    sparse_threshold = float(np.quantile(oof_distances, sparse_quantile))
    ood_threshold = float(np.quantile(oof_distances, ood_quantile))
    high_error_threshold = float(np.quantile(absolute_errors, 0.90))
    high_error = absolute_errors > high_error_threshold
    sparse_or_ood = oof_distances > sparse_threshold

    bin_count = min(12, max(4, int(np.sqrt(y.size))))
    quantile_edges = np.unique(np.quantile(oof_distances, np.linspace(0.0, 1.0, bin_count + 1)))
    bin_centers: list[float] = []
    bin_median: list[float] = []
    bin_p90: list[float] = []
    bin_p95: list[float] = []
    for left, right in zip(quantile_edges[:-1], quantile_edges[1:]):
        members = (oof_distances >= left) & (oof_distances <= right if right == quantile_edges[-1] else oof_distances < right)
        if not bool(members.any()):
            continue
        bin_centers.append(float(np.median(oof_distances[members])))
        bin_median.append(float(np.median(absolute_errors[members])))
        bin_p90.append(float(np.quantile(absolute_errors[members], 0.90)))
        bin_p95.append(float(np.quantile(absolute_errors[members], 0.95)))

    pair_limit = min(_int_param(params, "pair_samples", 20_000, 100), 100_000)
    if x.shape[0] <= 250:
        pair_i, pair_j = np.triu_indices(x.shape[0], 1)
    else:
        rng = np.random.default_rng(_seed(params))
        pair_i = rng.integers(0, x.shape[0], size=pair_limit)
        pair_j = rng.integers(0, x.shape[0], size=pair_limit)
        different = pair_i != pair_j
        pair_i, pair_j = pair_i[different], pair_j[different]
    if pair_i.size > pair_limit:
        display = _bounded_indices(pair_i.size, pair_limit)
        pair_i, pair_j = pair_i[display], pair_j[display]
    pair_distance = np.linalg.norm(x[pair_i] - x[pair_j], axis=1)
    pair_property_delta = np.abs(y[pair_i] - y[pair_j])
    distance_property_correlation = _safe_correlation(pair_distance, pair_property_delta)
    property_units = {
        "energy_per_atom": "eV/atom",
        "energy": "eV",
        "force_max": "eV/Å",
        "force_magnitude": "eV/Å",
        "volume": "Å³",
    }
    max_pearson = float(np.max(np.abs(correlations)))
    max_spearman = float(np.max(np.abs(spearman_correlations)))
    max_mi = float(np.max(mutual_information))
    encoding_strength = "strong" if r2 >= 0.8 else "moderate" if r2 >= 0.5 else "weak"
    information_pattern = (
        "distributed" if r2 >= 0.8 and max_pearson < 0.6
        else "dominant_features" if r2 >= 0.8 and max_pearson >= 0.6
        else "insufficient" if r2 < 0.5 and max_pearson < 0.4
        else "mixed"
    )
    if progress:
        progress(1.0, "property correlation complete")
    return {
        "arrays": {
            "sample_indices": np.flatnonzero(valid).astype(np.int64),
            "sample_frames": np.asarray(samples.frame)[valid].astype(np.int64),
            "sample_rows": (
                np.asarray(samples.row)[valid].astype(np.int64)
                if samples.row is not None
                else np.full(y.size, -1, dtype=np.int64)
            ),
            "targets": y,
            "predictions": predictions,
            "residuals": residuals.astype(np.float64),
            "absolute_errors": absolute_errors.astype(np.float64),
            "feature_correlations": correlations.astype(np.float64),
            "pearson_correlations": correlations.astype(np.float64),
            "spearman_correlations": spearman_correlations.astype(np.float64),
            "mutual_information": mutual_information.astype(np.float64),
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
            "top_indices": np.flatnonzero(keep)[order[:top_k]].astype(np.int64),
            "oof_distances": oof_distances.astype(np.float64),
            "reliability_bin_center": np.asarray(bin_centers, dtype=np.float64),
            "reliability_bin_median": np.asarray(bin_median, dtype=np.float64),
            "reliability_bin_p90": np.asarray(bin_p90, dtype=np.float64),
            "reliability_bin_p95": np.asarray(bin_p95, dtype=np.float64),
            "pair_distance": pair_distance.astype(np.float64),
            "pair_property_delta": pair_property_delta.astype(np.float64),
        },
        "preview": {
            "kind": "property_correlation",
            "property": property_name,
            "property_unit": property_units.get(property_name, ""),
            "sample_count": int(y.size),
            "feature_count": int(samples.n_features),
            "valid_feature_count": int(model_x.shape[1]),
            "missing_count": int((~valid).sum()),
            "model": "Ridge",
            "model_alpha": _float_param(params, "alpha", 1.0, 0.0),
            "cv_folds": folds,
            "cv_shuffle": True,
            "cv_seed": _seed(params),
            "r2": r2,
            "rmse": rmse,
            "mae": mae,
            "baseline": "training-fold mean",
            "baseline_r2": baseline_r2,
            "baseline_rmse": baseline_rmse,
            "baseline_mae": baseline_mae,
            "residual_mean": float(residuals.mean()),
            "residual_median": float(np.median(residuals)),
            "residual_std": float(residuals.std()),
            "p95_absolute_error": float(np.quantile(absolute_errors, 0.95)),
            "max_abs_pearson": max_pearson,
            "max_abs_spearman": max_spearman,
            "max_mutual_information": max_mi,
            "encoding_strength": encoding_strength,
            "information_pattern": information_pattern,
            "distance_definition": "mean OOF training-fold kNN distance",
            "distance_metric": distance_metric,
            "distance_standardized": True,
            "reliability_k": reliability_k,
            "distance_error_pearson": distance_error_pearson,
            "distance_error_spearman": distance_error_spearman,
            "sparse_quantile": sparse_quantile,
            "ood_quantile": ood_quantile,
            "sparse_threshold": sparse_threshold,
            "ood_threshold": ood_threshold,
            "high_error_threshold": high_error_threshold,
            "high_error_high_distance_count": int(np.sum(high_error & sparse_or_ood)),
            "high_error_low_distance_count": int(np.sum(high_error & ~sparse_or_ood)),
            "distance_property_correlation": distance_property_correlation,
            "top_features": [
                {
                    "feature": int(np.flatnonzero(keep)[index]),
                    "correlation": float(correlations[index]),
                    "pearson": float(correlations[index]),
                    "spearman": float(spearman_correlations[index]),
                    "mutual_information": float(mutual_information[index]),
                }
                for index in order[:top_k]
            ],
        },
        "warnings": warnings,
        "feature_indices": np.flatnonzero(keep).astype(np.int64),
    }



class FeatureCorrelation:
    name = "feature_correlation"
    category = "engine"

    def validate(self, params: dict) -> None:
        return None

    def run(self, data, params: dict, progress=None) -> dict:
        return feature_correlation(data, params, progress)


class PropertyCorrelation:
    name = "property_correlation"
    category = "engine"

    def validate(self, params: dict) -> None:
        return None

    def run(self, data, params: dict, progress=None) -> dict:
        return property_correlation(data, params, progress)
