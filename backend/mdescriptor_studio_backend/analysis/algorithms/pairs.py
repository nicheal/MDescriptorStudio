"""Analysis algorithms: pairs."""

from __future__ import annotations

from typing import Callable

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, AppError
from ..models import DescriptorMatrix
from ._common import _aligned_space_metrics, _bounded_indices, _check_samples, _correlation_of_ranks, _cross_k_nearest, _cross_nearest, _effective_dimension_metrics, _float_param, _int_param, _joint_projection, _nearest_distances, _pairwise_matrix, _preprocess, _preprocess_reference_query, _reference_query_preprocess, _safe_correlation, _safe_import, _seed, _visual_pca

def coverage(reference: DescriptorMatrix, query: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    ref, qry, warnings, keep = _preprocess_reference_query(reference.values, query.values, params)
    return _coverage(ref, qry, warnings, keep, params, progress)


def _coverage(
    ref: np.ndarray,
    qry: np.ndarray,
    warnings: list[str],
    keep: np.ndarray,
    params: dict,
    progress: Callable[[float, str], None] | None = None,
) -> dict:
    """Coverage over an already-preprocessed reference/query pair.

    ``drift`` shares this preprocessing: taking the whole pipeline again made a
    second copy of both matrices and a second centre/scale pass for statistics
    that have to be measured on the same coordinates.
    """
    metric = str(params.get("metric") or "euclidean")
    _check_samples(ref, 1)
    _check_samples(qry, 1)
    chunk = _int_param(params, "chunk_size", 2048, 1)
    reference_chunk = _int_param(params, "reference_chunk_size", 2048, 1)
    self_dist = _nearest_distances(ref, min(_int_param(params, "k", 1, 1), max(ref.shape[0] - 1, 1)), metric)[1][:, 0] if ref.shape[0] > 1 else np.zeros(1)
    q95 = _float_param(params, "q95", float(np.quantile(self_dist, 0.95)) if self_dist.size else 0.0, 0.0)
    q99 = _float_param(params, "q99", float(np.quantile(self_dist, 0.99)) if self_dist.size else q95, 0.0)
    if q99 < q95:
        raise AppError(ANALYSIS_INPUT_INVALID, "q99 must be >= q95")
    nearest, distances = _cross_nearest(ref, qry, metric, chunk, reference_chunk, progress)
    labels = np.where(distances <= q95, 0, np.where(distances <= q99, 1, 2)).astype(np.int64)
    arrays = {"nearest_indices": nearest, "distances": distances, "labels": labels}
    arrays.update(_joint_projection(ref, qry, min(_int_param(params, "projection_samples", 2_000, 50), 10_000)))
    return {"arrays": arrays, "preview": {"kind": "coverage", "preprocess": _reference_query_preprocess(params), "categories": ["covered", "marginal", "out_of_coverage"], "q95": q95, "q99": q99, "metric": metric, "covered": int((labels == 0).sum()), "marginal": int((labels == 1).sum()), "out_of_coverage": int((labels == 2).sum()), "mean_distance": float(distances.mean()), "median_distance": float(np.median(distances)), "max_distance": float(distances.max())}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}


def overlap(reference: DescriptorMatrix, query: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    ref, qry, warnings, keep = _preprocess_reference_query(reference.values, query.values, params)
    metric = str(params.get("metric") or "euclidean")
    chunk = _int_param(params, "chunk_size", 2048, 1)
    reference_chunk = _int_param(params, "reference_chunk_size", 2048, 1)
    nearest, distances = _cross_nearest(ref, qry, metric, chunk, reference_chunk, progress)
    if ref.shape[0] > 1:
        self_dist = _nearest_distances(ref, 1, metric)[1][:, 0]
        similar_default = float(np.quantile(self_dist, 0.05))
    else:
        similar_default = 0.0
    duplicate_threshold = _float_param(params, "duplicate_threshold", 1e-8, 0.0)
    similar_threshold = _float_param(params, "similar_threshold", max(similar_default, duplicate_threshold), duplicate_threshold)
    labels = np.where(distances <= duplicate_threshold, 0, np.where(distances <= similar_threshold, 1, 2)).astype(np.int64)
    arrays = {"nearest_indices": nearest, "distances": distances, "labels": labels}
    arrays.update(_joint_projection(ref, qry, min(_int_param(params, "projection_samples", 2_000, 50), 10_000)))
    total = max(int(labels.size), 1)
    return {
        "arrays": arrays,
        "preview": {
            "kind": "overlap",
            "preprocess": _reference_query_preprocess(params),
            "categories": ["near_duplicate", "highly_similar", "independent"],
            "metric": metric,
            "duplicate_threshold": duplicate_threshold,
            "similar_threshold": similar_threshold,
            "near_duplicates": int((labels == 0).sum()),
            "highly_similar": int((labels == 1).sum()),
            "independent": int((labels == 2).sum()),
            "overlap_fraction": float(((labels == 0) | (labels == 1)).sum() / total),
            "mean_distance": float(distances.mean()),
        },
        "warnings": warnings,
        "feature_indices": np.flatnonzero(keep).astype(np.int64),
    }


def acquisition(reference: DescriptorMatrix, query: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    """Select a novel/uncertain and diverse query batch.

    ``novelty_fps`` keeps the original nearest-reference acquisition.  The
    ``uncertainty_diversity`` variant estimates descriptor-space epistemic
    uncertainty from the distance to the k-th reference neighbor and its
    local spread.  It is deliberately model-free: the Studio remains a
    descriptor-analysis application and does not silently run a force or
    energy model during acquisition.
    """
    ref, qry, warnings, keep = _preprocess_reference_query(reference.values, query.values, params)
    metric = str(params.get("metric") or "euclidean")
    # Two phases share one bar: the kNN scan gets the first half, the greedy
    # acquisition the second. Handing the scan the raw callback would run the
    # progress to 100% and then start the loop over at 0%.
    scan_progress = (lambda fraction, message: progress(0.5 * fraction, message)) if progress else None
    acquisition_method = str(params.get("acquisition_method") or params.get("algorithm") or "novelty_fps").lower()
    if acquisition_method in ("uncertainty", "uncertainty_diversity", "knn_uncertainty"):
        uncertainty_k = min(_int_param(params, "uncertainty_k", 8, 2), ref.shape[0])
        neighbor_indices, neighbor_distances = _cross_k_nearest(
            ref,
            qry,
            metric,
            _int_param(params, "chunk_size", 2048, 1),
            _int_param(params, "reference_chunk_size", 2048, 1),
            uncertainty_k,
            scan_progress,
        )
        nearest = neighbor_indices[:, 0]
        novelty = neighbor_distances[:, 0]
        uncertainty = neighbor_distances[:, -1] + neighbor_distances.std(axis=1)
        acquisition_method = "uncertainty_diversity"
        warnings.append("uncertainty is a descriptor-space kNN extrapolation proxy, not model prediction variance")
    else:
        nearest, novelty = _cross_nearest(
            ref,
            qry,
            metric,
            _int_param(params, "chunk_size", 2048, 1),
            _int_param(params, "reference_chunk_size", 2048, 1),
            scan_progress,
        )
        uncertainty = novelty.copy()
        acquisition_method = "novelty_fps"
    target = min(_int_param(params, "n_samples", 100, 1), qry.shape[0])
    pool_factor = _float_param(params, "pool_factor", 5.0, 1.0)
    pool_size = min(qry.shape[0], max(target, int(round(target * pool_factor))))
    base_score = uncertainty if acquisition_method == "uncertainty_diversity" else novelty
    pool = np.argsort(-base_score, kind="stable")[:pool_size]
    base_scale = np.ptp(base_score[pool])
    normalized_base = (base_score[pool] - base_score[pool].min()) / max(float(base_scale), 1e-15)
    base_weight_name = "uncertainty_weight" if acquisition_method == "uncertainty_diversity" else "novelty_weight"
    base_weight = _float_param(params, base_weight_name, 0.65, 0.0, 1.0)
    selected_local = [int(np.argmax(normalized_base))]
    # The objective value that *caused* each pick, kept in pick order.  Both
    # terms now sit on a ruler fixed before the loop: re-dividing the diversity
    # term by its own running ptp at every step put step k and step k+1 on
    # different scales, so this list could rise while the panel plotted it as
    # "why each sample was taken" (deep review P1-17; 118 of 120 measured runs).
    pick_scores = [float(normalized_base[selected_local[0]])]
    min_diversity = np.full(pool_size, np.inf, dtype=np.float64)
    acquisition_score = np.zeros(pool_size, dtype=np.float64)
    diversity_scale = 0.0
    for step in range(1, target):
        last = qry[pool[selected_local[-1]]]
        delta = qry[pool] - last
        if metric == "euclidean":
            distances = np.linalg.norm(delta, axis=1)
        elif metric == "manhattan":
            distances = np.abs(delta).sum(axis=1)
        else:
            pool_norm = np.linalg.norm(qry[pool], axis=1)
            last_norm = float(np.linalg.norm(last))
            denominator = np.maximum(pool_norm * max(last_norm, 1e-15), 1e-15)
            distances = np.maximum(1.0 - (qry[pool] @ last) / denominator, 0.0)
        min_diversity = np.minimum(min_diversity, distances)
        if step == 1:
            # The pool's radius around the first pick. min_diversity can only
            # shrink from here, so this is the largest value the term will ever
            # take - which is what makes one ruler, and a monotone pick list,
            # possible.
            diversity_scale = float(np.max(min_diversity))
        normalized_diversity = min_diversity / max(diversity_scale, 1e-15)
        acquisition_score = base_weight * normalized_base + (1.0 - base_weight) * normalized_diversity
        acquisition_score[selected_local] = -1.0
        chosen = int(np.argmax(acquisition_score))
        pick_scores.append(float(acquisition_score[chosen]))
        selected_local.append(chosen)
        if progress and (step % 50 == 0 or step == target - 1):
            progress(0.5 + 0.5 * (step + 1) / max(target, 1), f"{acquisition_method} acquisition")
    selected = pool[np.asarray(selected_local, dtype=np.int64)]
    full_scores = np.zeros(qry.shape[0], dtype=np.float64)
    full_diversity = np.zeros(qry.shape[0], dtype=np.float64)
    # The same ruler as the loop, so the score the panel colours by is the last
    # value the loop maximised rather than a third normalisation of it.
    diversity_component = min_diversity / max(diversity_scale, 1e-15) if target > 1 else np.zeros(pool_size, dtype=np.float64)
    full_diversity[pool] = diversity_component
    full_scores[pool] = base_weight * normalized_base + (1.0 - base_weight) * diversity_component
    arrays = {
        "selected_indices": selected.astype(np.int64),
        "nearest_indices": nearest,
        "distances": novelty,
        "novelty": novelty,
        # Both uncertainty estimates exist for every query sample, so this is
        # the full array rather than the pool's copy: scattering zeros outside
        # the pool read as "these samples have no uncertainty" on the colour
        # bar (deep review P1-17, the same root cause as `scores`).
        "uncertainty": uncertainty,
        "diversity": full_diversity,
        "scores": full_scores,
        # Aligned with selected_indices: the score at the moment of the pick.
        "pick_scores": np.asarray(pick_scores, dtype=np.float64),
        "coords": _visual_pca(qry),
    }
    return {
        "arrays": arrays,
        "preview": {
            "kind": "acquisition",
            "preprocess": _reference_query_preprocess(params),
            "algorithm": acquisition_method,
            "uncertainty_method": "knn_extrapolation" if acquisition_method == "uncertainty_diversity" else None,
            "selected_count": int(selected.size),
            "candidate_pool": int(pool_size),
            "base_weight": base_weight,
            "novelty_weight": base_weight if acquisition_method == "novelty_fps" else None,
            "uncertainty_weight": base_weight if acquisition_method == "uncertainty_diversity" else None,
            "mean_selected_novelty": float(novelty[selected].mean()),
            "mean_selected_uncertainty": float(uncertainty[selected].mean()),
        },
        "warnings": warnings,
        "feature_indices": np.flatnonzero(keep).astype(np.int64),
    }


def mantel(left: DescriptorMatrix, right: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    """Run a two-sided Mantel permutation test on two aligned descriptor spaces."""
    a, left_warnings, _left_keep = _preprocess(left.values, params, "standardized")
    b, right_warnings, _right_keep = _preprocess(right.values, params, "standardized")
    _check_samples(a, 3)
    _check_samples(b, 3)
    if left.n_samples != right.n_samples or left.sample_ids != right.sample_ids:
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "Mantel comparison requires aligned sample IDs",
            {"left_samples": left.n_samples, "right_samples": right.n_samples},
        )
    metric = str(params.get("metric") or "euclidean")
    if metric not in ("euclidean", "cosine", "manhattan"):
        raise AppError(ANALYSIS_INPUT_INVALID, "Mantel metric must be euclidean, cosine, or manhattan")
    method = str(params.get("method") or "pearson").lower()
    if method not in ("pearson", "spearman"):
        raise AppError(ANALYSIS_INPUT_INVALID, "Mantel method must be pearson or spearman")
    alternative = str(params.get("alternative") or "two-sided").lower()
    if alternative not in ("two-sided", "greater", "less"):
        raise AppError(ANALYSIS_INPUT_INVALID, "Mantel alternative must be two-sided, greater, or less")
    limit = min(_int_param(params, "max_samples", 600, 3), 2_000)
    sample_indices = _bounded_indices(a.shape[0], limit)
    a = a[sample_indices]
    b = b[sample_indices]
    left_matrix = _pairwise_matrix(a, metric)
    right_matrix = _pairwise_matrix(b, metric)
    triangle = np.triu_indices(sample_indices.size, 1)
    left_pairs = left_matrix[triangle]
    right_pairs = right_matrix[triangle]
    size = int(sample_indices.size)
    rows, columns = triangle
    if method == "pearson":
        left_stat, right_grid, stat_fn = left_pairs, right_matrix, _safe_correlation
    else:
        # Spearman's rho is the Pearson correlation of average-method ranks, and
        # reordering the samples only relabels the pairs - the multiset of
        # off-diagonal distances is untouched. So rank the pairs once and put the
        # ranks back into a symmetric grid: the same single gather then yields
        # the ranks of the permuted pairs, which is what ranking each permutation
        # from scratch returned. Mirroring the upper triangle also makes the grid
        # hold one value per unordered pair, unlike sklearn's euclidean gram
        # trick, whose two triangles differ by up to 1.8e-15.
        # Ranking ~180 000 pairs inside every permutation was the whole cost of
        # the test: at the 2 000-sample cap, 999 permutations took 629.6 s and
        # now take 54.8 s, and at 600 samples 43.8 s became 2.9 s.
        rankdata = _safe_import("scipy.stats", "scipy").rankdata
        ranks = np.zeros_like(right_matrix)
        ranks[triangle] = rankdata(right_pairs)
        left_stat, right_grid, stat_fn = rankdata(left_pairs), ranks + ranks.T, _correlation_of_ranks
    observed = float(stat_fn(left_stat, right_grid[rows, columns]))
    permutations = min(_int_param(params, "permutations", 999, 1), 5_000)
    rng = np.random.default_rng(_seed(params))
    null = np.empty(permutations, dtype=np.float64)
    if progress:
        progress(0.05, "computing Mantel statistic")
    for index in range(permutations):
        permutation = rng.permutation(size)
        # Reordering the samples only reorders the entries of the grid that was
        # already built; recomputing distances costs O(n^2*D) per permutation for
        # an identical result.
        null[index] = float(stat_fn(left_stat, right_grid[permutation[rows], permutation[columns]]))
        if progress and (index % 25 == 0 or index == permutations - 1):
            progress(0.05 + 0.9 * (index + 1) / permutations, "running Mantel permutations")
    if alternative == "greater":
        exceed = int((null >= observed).sum())
    elif alternative == "less":
        exceed = int((null <= observed).sum())
    else:
        exceed = int((np.abs(null) >= abs(observed)).sum())
    p_value = float((exceed + 1) / (permutations + 1))
    if progress:
        progress(1.0, "Mantel test complete")
    warnings = [*left_warnings, *right_warnings]
    if a.shape[0] < left.n_samples:
        warnings.append(f"Mantel permutations limited to {a.shape[0]} deterministic aligned samples")
    return {
        "arrays": {
            "sample_indices": sample_indices,
            "left_pair_distances": left_pairs.astype(np.float64),
            "right_pair_distances": right_pairs.astype(np.float64),
            "null_distribution": null,
        },
        "preview": {
            "kind": "mantel",
            "method": method,
            "metric": metric,
            "alternative": alternative,
            "statistic": observed,
            "p_value": p_value,
            "permutations": permutations,
            "sample_count": int(a.shape[0]),
            "pair_count": int(left_pairs.size),
            "significant_at_05": bool(p_value < 0.05),
        },
        "warnings": warnings,
    }


def compare(left: DescriptorMatrix, right: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    a, left_warnings, _left_keep = _preprocess(left.values, params, "standardized")
    b, right_warnings, _right_keep = _preprocess(right.values, params, "standardized")
    _check_samples(a, 3)
    _check_samples(b, 3)
    if left.n_samples != right.n_samples or left.sample_ids != right.sample_ids:
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "comparison requires aligned sample IDs",
            {"left_samples": left.n_samples, "right_samples": right.n_samples},
        )
    if progress:
        progress(0.15, "comparing descriptor geometry")
    metrics, result = _aligned_space_metrics(a, b, params)
    left_dimension, left_thresholds = _effective_dimension_metrics(a)
    right_dimension, right_thresholds = _effective_dimension_metrics(b)
    preview = {
        "kind": "compare",
        "left_samples": left.n_samples,
        "right_samples": right.n_samples,
        "left_feature_count": left.n_features,
        "right_feature_count": right.n_features,
        **metrics,
        "left_effective_dimension": left_dimension,
        "right_effective_dimension": right_dimension,
        "left_components_for_threshold": left_thresholds,
        "right_components_for_threshold": right_thresholds,
        "comparison_level": "aligned descriptor-space geometry",
    }
    if left.n_features == right.n_features:
        mean_delta = b.mean(axis=0) - a.mean(axis=0)
        var_delta = b.var(axis=0) - a.var(axis=0)
        result.update({"mean_delta": mean_delta, "variance_delta": var_delta})
        preview.update({"feature_count": left.n_features, "mean_absolute_delta": float(np.mean(np.abs(mean_delta))), "variance_absolute_delta": float(np.mean(np.abs(var_delta)))})
    if progress:
        progress(1.0, "comparison complete")
    return {"arrays": result, "preview": preview, "warnings": [*left_warnings, *right_warnings]}


def drift(reference: DescriptorMatrix, query: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    # All drift statistics must use the same reference-scaled coordinates.
    # Otherwise coverage is measured in raw units while MMD/centroid/
    # covariance shifts are measured in standardized units.
    drift_params = dict(params or {})
    if not drift_params.get("preprocess"):
        drift_params["preprocess"] = "standardized"
    ref, qry, warnings, keep = _preprocess_reference_query(reference.values, query.values, drift_params)
    # Two phases share one bar, and the second is not the cheap one: an n x n
    # distance matrix for the bandwidth plus three kernel matrices.  Passing
    # coverage the raw callback reported it as done and then ran the MMD phase
    # without a single progress call - the only place cancellation is observed.
    coverage_progress = (lambda fraction, message: progress(0.7 * fraction, message)) if progress else None
    result = _coverage(ref, qry, warnings, keep, drift_params, coverage_progress)
    distances = result["arrays"]["distances"]
    limit = min(_int_param(drift_params, "distribution_samples", 500, 20), 2_000)
    a = ref[_bounded_indices(ref.shape[0], limit)]
    b = qry[_bounded_indices(qry.shape[0], limit)]
    combined = np.vstack([a, b])
    if progress:
        progress(0.78, "estimating the MMD bandwidth")
    combined_distances = _pairwise_matrix(combined, "euclidean")
    nonzero = combined_distances[combined_distances > np.finfo(np.float64).eps]
    bandwidth = _float_param(drift_params, "bandwidth", float(np.median(nonzero)) if nonzero.size else 1.0, np.finfo(np.float64).eps)
    gamma = 1.0 / (2.0 * bandwidth * bandwidth)
    kernels = _safe_import("sklearn.metrics.pairwise", "scikit-learn")
    if progress:
        progress(0.88, "computing MMD")
    kxx = kernels.rbf_kernel(a, a, gamma=gamma)
    kyy = kernels.rbf_kernel(b, b, gamma=gamma)
    kxy = kernels.rbf_kernel(a, b, gamma=gamma)
    mmd2 = max(float(kxx.mean() + kyy.mean() - 2.0 * kxy.mean()), 0.0)
    centroid_distance = float(np.linalg.norm(a.mean(axis=0) - b.mean(axis=0)))
    covariance_shift: float | None
    if a.shape[0] < 2 or b.shape[0] < 2:
        covariance_shift = None
        result["warnings"].append("covariance shift requires at least two samples in both sets")
    else:
        covariance_a = np.atleast_2d(np.cov(a, rowvar=False))
        covariance_b = np.atleast_2d(np.cov(b, rowvar=False))
        covariance_shift = float(np.linalg.norm(covariance_a - covariance_b) / max(np.linalg.norm(covariance_a), 1e-15))
    result["preview"] = {**result["preview"], "kind": "drift", "mean_distance": float(distances.mean()), "median_distance": float(np.median(distances)), "max_distance": float(distances.max()), "mmd": float(np.sqrt(mmd2)), "mmd_squared": mmd2, "bandwidth": bandwidth, "centroid_distance": centroid_distance, "covariance_shift": covariance_shift,
            # The three numbers above describe a bounded sample, while everything else on the
            # strip describes every query row; without this the panel showed one
            # population wearing another's label (pass 5, 5-C6).
            "mmd_reference_rows": int(a.shape[0]), "mmd_query_rows": int(b.shape[0])}
    if progress:
        progress(1.0, "drift complete")
    return result



class Coverage:
    name = "coverage"
    category = "pair"

    def run(self, left, right, params: dict, progress=None) -> dict:
        return coverage(left, right, params, progress)


class Overlap:
    name = "overlap"
    category = "pair"

    def run(self, left, right, params: dict, progress=None) -> dict:
        return overlap(left, right, params, progress)


class Acquisition:
    name = "acquisition"
    category = "pair"

    def run(self, left, right, params: dict, progress=None) -> dict:
        return acquisition(left, right, params, progress)


class Mantel:
    name = "mantel"
    category = "pair"

    def run(self, left, right, params: dict, progress=None) -> dict:
        return mantel(left, right, params, progress)


class Compare:
    name = "compare"
    category = "pair"

    def run(self, left, right, params: dict, progress=None) -> dict:
        return compare(left, right, params, progress)


class Drift:
    name = "drift"
    category = "pair"

    def run(self, left, right, params: dict, progress=None) -> dict:
        return drift(left, right, params, progress)
