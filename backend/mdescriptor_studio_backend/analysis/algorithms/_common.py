"""Shared numerical helpers for analysis algorithm plugins."""

from __future__ import annotations

import threading
from itertools import product
from typing import Any, Callable

import numpy as np

from ...errors import (
    ANALYSIS_DEPENDENCY_MISSING,
    ANALYSIS_INPUT_INVALID,
    ANALYSIS_INSUFFICIENT_SAMPLES,
    AppError,
)
from ...lattice import image_shift_limits
from ..models import DescriptorMatrix


_warmup_gate = threading.Event()
_warmup_gate.set()

_TRAJECTORY_EVENT_METHODS = ("mad", "zscore", "percentile")

# Relative tolerance behind _meaningful_scale: a feature must spread by more
# than this fraction of its own magnitude to count as carrying information.
SCALE_RELATIVE_TOLERANCE = 1e-12

# Bounds for the periodic image stencil in _local_neighbor_graph. A degenerate
# cell wants ~1e6 images per axis and would freeze this runner past any
# cancellation, but a merely *skewed* cell legitimately needs several images on
# every axis at once, so the total is what has to be budgeted as well: 1024
# images over a frame is tens of megabytes and seconds, not gigabytes and never.
_GRAPH_MAX_IMAGES_PER_AXIS = 8
_GRAPH_MAX_TOTAL_IMAGES = 1024
# Atoms per neighbour query block. A block is one scipy pass plus one progress
# (and therefore cancellation) point: 4096 atoms at a dense cutoff is a few
# hundred thousand pairs, which is also what bounds the temporary memory.
_GRAPH_QUERY_BLOCK = 4096

# sklearn and hdbscan fit calls do not expose a cooperative cancellation hook.
# Keep their worst-case native sections bounded; JobService still settles a
# cancellation immediately, and these caps prevent a cancelled black-box run
# from occupying an analysis worker for an unbounded period.
_BLACK_BOX_SAMPLE_LIMITS = {
    "hdbscan": 30_000,
    "dbscan": 30_000,
    "agglomerative": 20_000,
    "hierarchical": 20_000,
    "kmeans": 100_000,
    "lof": 30_000,
    "isolation_forest": 100_000,
    "property_correlation": 50_000,
}

def warmup() -> dict[str, bool]:
    """Import optional numeric backends on the background warmup thread.

    Native sklearn and hdbscan dependencies can acquire process import
    locks or initialize DLL state during their first import. Doing that
    work in a single dedicated thread before any request touches these
    modules (gated via ``_safe_import``) keeps a Windows sidecar from
    hanging at ``loading descriptor results`` on its first analysis
    request while no longer blocking ``backend.ready``. Missing optional
    packages are reported and are still converted to
    ANALYSIS_DEPENDENCY_MISSING when selected.
    """
    import importlib

    try:
        availability: dict[str, bool] = {}
        for name in ("sklearn", "hdbscan"):
            try:
                importlib.import_module(name)
            except ImportError:
                availability[name] = False
            else:
                availability[name] = True
        return availability
    finally:
        _warmup_gate.set()

def arm_analysis_warmup_gate() -> None:
    """Switch _safe_import into wait-for-warmup mode before warmup starts."""
    _warmup_gate.clear()

def _as_float64(values: Any, *, allow_nonfinite: bool = False) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 1:
        array = array.reshape(-1, 1)
    if array.ndim != 2:
        raise AppError(ANALYSIS_INPUT_INVALID, "analysis input must be a 2D matrix")
    if array.shape[0] == 0 or array.shape[1] == 0:
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "analysis input cannot be empty",
            {"shape": list(array.shape)},
        )
    # Do not silently drop bad rows for ordinary analyses: a NaN/Inf descriptor
    # usually indicates a broken upstream calculation and hiding it would
    # destroy reproducibility. Feature-variance is the one diagnostic that
    # deliberately handles them per column and reports the invalid counts.
    finite = np.isfinite(array)
    if not allow_nonfinite and not bool(finite.all()):
        bad = np.argwhere(~finite)
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "analysis input contains NaN or Inf",
            {"bad_values": int((~finite).sum()), "first_index": bad[0].tolist()},
        )
    return array

def _int_param(params: dict, name: str, default: int, minimum: int = 1) -> int:
    value = params.get(name, default)
    if isinstance(value, bool):
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be an integer")
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be an integer") from exc
    if value < minimum:
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be >= {minimum}")
    return value

def _float_param(params: dict, name: str, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    value = params.get(name, default)
    if isinstance(value, bool):
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be a finite number")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be a finite number") from exc
    if not np.isfinite(value):
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be <= {maximum}")
    return value

def _seed(params: dict) -> int:
    return _int_param(params, "seed", 42, 0)

def _meaningful_scale(centre: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """Features whose spread is more than rounding noise at their own size.

    An absolute float64-eps test only rejects a *bitwise* constant column: a
    feature that jitters by a few ulps around 1000.0 has std ~2.4e-13, passes
    that test, and is then divided by its own scale -- which turns pure
    rounding noise into a full-weight axis in every distance, PCA and coverage
    threshold (deep review P1-15, where the same column also got three
    different verdicts from three different tolerances).  One relative
    tolerance, matching the magnitude of the ``constant_tolerance`` /
    ``variance_threshold`` parameters already exposed to users, is now the
    single owner of that judgement.
    """
    magnitude = np.maximum(np.abs(np.asarray(centre, dtype=np.float64)), 1.0)
    return np.asarray(scale, dtype=np.float64) > SCALE_RELATIVE_TOLERANCE * magnitude

def _preprocess_mode(params: dict, default: str) -> str:
    """The scale a single-matrix result was measured on, for its preview to record.

    `_preprocess` resolves the same key the same way: an absent `preprocess`
    means *this* algorithm's default, and the defaults differ by module (`raw`
    for the distance views, `standardized` for trajectory and local diversity).
    Cross-set work already has `_reference_query_preprocess` doing this job;
    without the per-matrix equivalent a stored `2.4` was a distance in unknown
    units, and the history row could not say which (deep review pass 4, B-7).
    """
    return str((params or {}).get("preprocess", default))

def _preprocess(
    x: np.ndarray,
    params: dict,
    default: str,
    *,
    allow_empty: bool = False,
) -> tuple[np.ndarray, list[str], np.ndarray]:
    """Return a single, explicitly informative feature space.

    Geometric and correlation analyses cannot produce a meaningful result from
    an empty informative space.  The one intentional exception is
    ``effective_dimension``: zero informative dimensions is itself the result
    it reports, so that caller opts in with ``allow_empty=True``.
    """
    x = _as_float64(x)
    mode = params.get("preprocess", default)
    if mode not in ("raw", "center", "standardized"):
        raise AppError(ANALYSIS_INPUT_INVALID, "preprocess must be raw, center, or standardized")
    warnings: list[str] = []
    means = x.mean(axis=0)
    centered = x - means
    variances = centered.var(axis=0)
    scale = np.sqrt(variances)
    keep = _meaningful_scale(means, scale)
    if not bool(keep.all()):
        warnings.append(f"ignored {int((~keep).sum())} zero-variance feature(s)")
    if not bool(keep.any()):
        if not allow_empty:
            raise AppError(
                ANALYSIS_INPUT_INVALID,
                "analysis requires at least one informative feature",
                {"informative_feature_count": 0, "feature_count": int(x.shape[1])},
            )
        # Preserve the all-false mask.  Re-adding constant columns here would
        # contradict the warning and let downstream geometry divide by zero.
        return x[:, :0], warnings, keep
    if mode == "raw":
        # Fancy indexing with an all-True mask would copy a matrix that is
        # already the one the caller wanted. (The centred copy above stays:
        # np.var(x) is not bitwise equal to (x - x.mean(0)).var(0), and
        # standardized results are cached by their exact bytes.)
        return (x if bool(keep.all()) else x[:, keep]), warnings, keep
    if mode == "center":
        return centered[:, keep], warnings, keep
    return centered[:, keep] / np.where(scale[keep] > 0, scale[keep], 1.0), warnings, keep

def _check_samples(x: np.ndarray, minimum: int = 2) -> None:
    if x.shape[0] < minimum:
        raise AppError(
            ANALYSIS_INSUFFICIENT_SAMPLES,
            f"at least {minimum} samples are required",
            {"samples": int(x.shape[0]), "minimum": minimum},
        )


def _check_black_box_samples(x: np.ndarray, algorithm: str) -> None:
    """Bound a fit with no callback between its native start and return."""
    limit = _BLACK_BOX_SAMPLE_LIMITS.get(str(algorithm).lower())
    if limit is not None and x.shape[0] > limit:
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            f"{algorithm} is limited to {limit:,} samples because this estimator cannot be cancelled during fit;"
            " sample the descriptor set first",
            {"algorithm": str(algorithm), "samples": int(x.shape[0]), "limit": limit},
        )

def _safe_import(module: str, package: str | None = None):
    try:
        # Hold heavy-module imports until the background warmup pass is done
        # (no-op once warm; see module docstring of the warmup gate).
        _warmup_gate.wait()
        # A non-wildcard fromlist returns the requested module without walking
        # package ``__all__``. sklearn.model_selection deliberately exposes
        # experimental names there that raise during wildcard import.
        return __import__(module, fromlist=["__name__"])
    except ImportError as exc:
        label = package or module
        raise AppError(
            ANALYSIS_DEPENDENCY_MISSING,
            f"analysis dependency {label!r} is not installed",
            {"dependency": label},
        ) from exc

def _nearest_distances(x: np.ndarray, k: int, metric: str = "euclidean") -> tuple[np.ndarray, np.ndarray]:
    neighbors = _safe_import("sklearn.neighbors", "scikit-learn").NearestNeighbors
    _check_samples(x, 2)
    k_eff = min(k + 1, x.shape[0])
    model = neighbors(n_neighbors=k_eff, metric=metric, n_jobs=1)
    distances, indices = model.fit(x).kneighbors(x)
    # Exclude self by identity, not by position. Column 0 is the query itself
    # only while no other row coincides with it: exact duplicates tie, and the
    # old [1:] slice then dropped a genuine neighbour and kept the row's own
    # index at distance 0 — which averaged into every neighbour statistic
    # downstream (group scores, outlier depth, pair distances).
    out_k = min(k, x.shape[0] - 1)
    is_self = indices == np.arange(indices.shape[0], dtype=indices.dtype)[:, None]
    order = np.argsort(is_self, kind="stable")[:, :out_k]
    return np.take_along_axis(indices, order, axis=1), np.take_along_axis(distances, order, axis=1)

def _bounded_indices(count: int, limit: int) -> np.ndarray:
    """Return deterministic, order-preserving indices for bounded visual artifacts."""
    if count <= limit:
        return np.arange(count, dtype=np.int64)
    return np.linspace(0, count - 1, limit, dtype=np.int64)

def _visual_pca_components(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two-dimensional visual PCA plus the explained-variance ratio of both axes.

    The ratio is what lets a panel state how much descriptor information the
    two drawn axes still carry: a visual jump in a 25%-variance panel is not on
    its own evidence of a descriptor-space jump.
    """
    x = _as_float64(x)
    if x.shape[0] == 1:
        return np.zeros((1, 2), dtype=np.float64), np.zeros(2, dtype=np.float64)
    centered = x - x.mean(axis=0)
    components = min(2, centered.shape[0], centered.shape[1])
    if components == 0:
        return np.zeros((x.shape[0], 2), dtype=np.float64), np.zeros(2, dtype=np.float64)
    if min(centered.shape) > 4:
        pca = _safe_import("sklearn.decomposition", "scikit-learn").PCA(
            n_components=components,
            svd_solver="randomized",
            random_state=42,
        )
        coords = np.asarray(pca.fit_transform(centered), dtype=np.float64)
        ratio = np.asarray(pca.explained_variance_ratio_, dtype=np.float64)
    else:
        _u, singular, vt = np.linalg.svd(centered, full_matrices=False)
        coords = centered @ vt[:components].T
        total = float(np.sum(singular * singular))
        ratio = (singular[:components] ** 2) / total if total > 0 else np.zeros(components, dtype=np.float64)
    if components < 2:
        coords = np.pad(coords, ((0, 0), (0, 2 - components)))
        ratio = np.pad(ratio, (0, 2 - components))
    return coords.astype(np.float64, copy=False), np.asarray(ratio[:2], dtype=np.float64)

def _visual_pca(x: np.ndarray) -> np.ndarray:
    """Fast deterministic two-dimensional PCA used only as a visual companion."""
    return _visual_pca_components(x)[0]

def _trajectory_threshold(steps: np.ndarray, params: dict) -> tuple[str, float, float, dict[str, float], list[str]]:
    """Event threshold over descriptor-space step distances.

    ``mad`` is the robust default (median + k * 1.4826 * MAD) because one large
    structural jump inflates the standard deviation and hides later events.
    ``zscore`` is offered for comparison and ``percentile`` reproduces a fixed
    top fraction of frames.  Returns any warnings the chosen method has to
    report about itself.
    """
    method = str(params.get("event_method") or "mad").lower()
    if method not in _TRAJECTORY_EVENT_METHODS:
        raise AppError(ANALYSIS_INPUT_INVALID, "event_method must be mad, zscore, or percentile")
    default_sensitivity = 1.0 if method == "percentile" else 3.0
    sensitivity = _float_param(params, "event_sensitivity", default_sensitivity, np.finfo(np.float64).eps)
    median = float(np.median(steps))
    mad = float(np.median(np.abs(steps - median)))
    mean = float(steps.mean())
    std = float(steps.std())
    robust_sigma = 1.4826 * mad
    warnings: list[str] = []
    if method == "percentile":
        if sensitivity >= 50.0:
            raise AppError(ANALYSIS_INPUT_INVALID, "event_sensitivity must be below 50 for percentile detection")
        threshold = float(np.quantile(steps, 1.0 - sensitivity / 100.0))
    elif method == "mad" and robust_sigma > 0.0:
        threshold = median + sensitivity * robust_sigma
    else:
        threshold = mean + sensitivity * std
        if method == "mad":
            # Half the steps being identical (every frame recorded twice, a
            # quantized descriptor, rejected Monte Carlo steps) drives the MAD
            # to zero, and a threshold of "median + 0" then flags about half of
            # the trajectory as events with no ratio to report.  The standard
            # deviation still describes this data; say that it is what was used.
            warnings.append(
                "step distances have no median absolute deviation, so the event threshold fell back to mean + k * standard deviation"
            )
    return method, sensitivity, threshold, {
        "median": median,
        "mad": mad,
        "robust_sigma": robust_sigma,
        "mean": mean,
        "std": std,
    }, warnings

def _effective_dimension_metrics(x: np.ndarray) -> tuple[float, dict[str, int]]:
    centered = _as_float64(x) - np.asarray(x, dtype=np.float64).mean(axis=0)
    singular = np.linalg.svd(centered, compute_uv=False, full_matrices=False)
    eigen = singular * singular
    total = float(eigen.sum())
    if total <= 0:
        return 0.0, {"0.9": 0, "0.95": 0, "0.99": 0}
    normalized = eigen / total
    participation = float(1.0 / np.sum(normalized * normalized))
    cumulative = np.cumsum(normalized)
    thresholds = {str(t): int(np.searchsorted(cumulative, t) + 1) for t in (0.9, 0.95, 0.99)}
    return participation, thresholds

def _safe_correlation(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    if a.size != b.size or a.size < 2:
        return 0.0
    if not bool(_meaningful_scale(np.array([a.mean(), b.mean()]), np.array([a.std(), b.std()])).all()):
        return 1.0 if np.allclose(a, b) else 0.0
    value = float(np.corrcoef(a, b)[0, 1])
    return value if np.isfinite(value) else 0.0

def _correlation_of_ranks(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson over vectors that have already been ranked, for Spearman loops.

    Spearman's rho *is* the Pearson correlation of average-method ranks, and on
    continuous and tie-heavy inputs `spearmanr(x, y)` and
    `pearson(rankdata(x), rankdata(y))` agree bit for bit. The fallback is
    deliberately _rank_correlation's, not _safe_correlation's: two constant
    vectors are NaN in scipy (and so 0.0 here), whereas _safe_correlation would
    call an equal pair of them 1.0.
    """
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    if a.size != b.size or a.size < 2:
        return 0.0
    if not (np.std(a) > 0.0 and np.std(b) > 0.0):
        return 0.0
    value = float(np.corrcoef(a, b)[0, 1])
    return value if np.isfinite(value) else 0.0

def _rank_correlation(a: np.ndarray, b: np.ndarray) -> float:
    stats = _safe_import("scipy.stats", "scipy")
    value = float(stats.spearmanr(np.asarray(a), np.asarray(b)).statistic)
    return value if np.isfinite(value) else 0.0

def _pairwise_matrix(x: np.ndarray, metric: str) -> np.ndarray:
    if metric not in ("euclidean", "cosine", "manhattan"):
        raise AppError(ANALYSIS_INPUT_INVALID, "metric must be euclidean, cosine, or manhattan")
    fn = _safe_import("sklearn.metrics", "scikit-learn").pairwise_distances
    return np.asarray(fn(_as_float64(x), metric=metric, n_jobs=1), dtype=np.float64)

def _clustered_feature_order(correlation: np.ndarray, feature_indices: np.ndarray) -> np.ndarray:
    """Return a deterministic order that groups features by ``1 - |r|``."""
    if correlation.shape[0] < 2:
        return feature_indices.copy()
    distance = 1.0 - np.clip(np.abs(correlation), 0.0, 1.0)
    np.fill_diagonal(distance, 0.0)
    try:
        hierarchy = _safe_import("scipy.cluster.hierarchy", "scipy")
        spatial_distance = _safe_import("scipy.spatial.distance", "scipy")
        condensed = spatial_distance.squareform(distance, checks=False)
        linkage = hierarchy.linkage(condensed, method="average", optimal_ordering=True)
        leaves = np.asarray(hierarchy.leaves_list(linkage), dtype=np.int64)
    except (AppError, ValueError):
        # Keep correlation analysis usable in reduced runtimes without SciPy.
        strength = np.sum(np.abs(correlation), axis=1) - 1.0
        leaves = np.argsort(-strength, kind="stable").astype(np.int64)
    return feature_indices[leaves]

def _connected_component_count(edges: np.ndarray, size: int) -> int:
    """Count connected components represented by local feature-index edges."""
    if edges.size == 0:
        return 0
    parent = np.arange(size, dtype=np.int64)

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = int(parent[value])
        return value

    for left, right in edges.tolist():
        left_root = find(int(left))
        right_root = find(int(right))
        if left_root != right_root:
            parent[right_root] = left_root
    involved = np.unique(edges)
    return len({find(int(index)) for index in involved.tolist()})

CROSS_SET_PREPROCESS_DEFAULT = "standardized"
PREPROCESS_MODES = ("raw", "center", "standardized")

def _reference_query_preprocess(params: dict) -> str:
    """The scale every cross-dataset comparison runs on, resolved in one place.

    Distances between two descriptor sets are not comparable when the features
    carry mixed units and magnitudes, so the default standardises (deep review
    P1-14: coverage used to default to raw while overlap and acquisition
    standardised, and the same panel therefore gave opposite answers for one
    input pair).  The algorithms read the value through here and their previews
    record it, so a stored result states the scale it was computed on.
    """
    mode = params.get("preprocess") or CROSS_SET_PREPROCESS_DEFAULT
    if mode not in PREPROCESS_MODES:
        raise AppError(ANALYSIS_INPUT_INVALID, "preprocess must be raw, center, or standardized")
    return str(mode)

def _preprocess_reference_query(reference: np.ndarray, query: np.ndarray, params: dict) -> tuple[np.ndarray, np.ndarray, list[str], np.ndarray]:
    reference = _as_float64(reference)
    query = _as_float64(query)
    if reference.shape[1] != query.shape[1]:
        raise AppError(ANALYSIS_INPUT_INVALID, "reference and query feature counts do not match")
    mode = _reference_query_preprocess(params)
    means = reference.mean(axis=0)
    centered_reference = reference - means
    scale = centered_reference.std(axis=0)
    # A feature that is constant in the reference set is still informative
    # when the query set moves away from that reference value.  Dropping it
    # would make raw coverage and OOD distances silently ignore an entire
    # direction.  For standardized cross-set work, retain the feature with a
    # unit scale so a reference-zero/query-nonzero displacement remains
    # measurable.
    keep = np.ones(reference.shape[1], dtype=bool)
    constant = ~_meaningful_scale(means, scale)
    warnings: list[str] = []
    if bool(constant.any()) and mode != "raw":
        warnings.append(f"retained {int(constant.sum())} reference-constant feature(s) with unit scale")
    if mode == "raw":
        return reference, query, warnings, keep
    centered_query = query - means
    if mode == "center":
        return centered_reference, centered_query, warnings, keep
    denominator = np.where(~constant, scale, 1.0)
    return centered_reference / denominator, centered_query / denominator, warnings, keep

def _cross_nearest(reference: np.ndarray, query: np.ndarray, metric: str, query_chunk: int, reference_chunk: int, progress: Callable[[float, str], None] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Bounded-memory nearest-reference search with source identities."""
    reference = _as_float64(reference)
    query = _as_float64(query)
    if metric not in ("euclidean", "cosine", "manhattan"):
        raise AppError(ANALYSIS_INPUT_INVALID, "cross-dataset metric must be euclidean, cosine, or manhattan")
    distances = np.empty(query.shape[0], dtype=np.float64)
    nearest = np.empty(query.shape[0], dtype=np.int64)
    # Hoisted like _cross_k_nearest below: a warmup-gated import per block would
    # be repeated tens of thousands of times for one search.
    cdist = _safe_import("scipy.spatial.distance", "scipy").cdist
    for start in range(0, query.shape[0], query_chunk):
        stop = min(start + query_chunk, query.shape[0])
        query_block = query[start:stop]
        best = np.full(query_block.shape[0], np.inf, dtype=np.float64)
        best_index = np.full(query_block.shape[0], -1, dtype=np.int64)
        query_norm = np.linalg.norm(query_block, axis=1, keepdims=True) if metric == "cosine" else None
        for ref_start in range(0, reference.shape[0], reference_chunk):
            ref_block = reference[ref_start : ref_start + reference_chunk]
            if metric == "euclidean":
                # scipy's cdist subtracts coordinates directly. This preserves
                # exact zero for identical rows, unlike the cancellation-prone
                # ||a||² + ||b||² - 2a·b identity.
                matrix = cdist(query_block, ref_block, metric="euclidean")
            elif metric == "manhattan":
                matrix = cdist(query_block, ref_block, metric="cityblock")
            else:
                ref_norm = np.linalg.norm(ref_block, axis=1)
                similarity = (query_block @ ref_block.T) / np.maximum(query_norm * ref_norm[None, :], 1e-15)
                matrix = np.maximum(1.0 - similarity, 0.0)
            local = np.argmin(matrix, axis=1)
            local_distance = matrix[np.arange(matrix.shape[0]), local]
            improved = local_distance < best
            best[improved] = local_distance[improved]
            best_index[improved] = ref_start + local[improved]
        distances[start:stop] = best
        nearest[start:stop] = best_index
        if progress:
            progress(stop / max(query.shape[0], 1), "nearest-reference distances")
    return nearest, distances

def _cross_k_nearest(
    reference: np.ndarray,
    query: np.ndarray,
    metric: str,
    query_chunk: int,
    reference_chunk: int,
    k: int,
    progress: Callable[[float, str], None] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Bounded-memory k-nearest reference search.

    The acquisition layer uses the complete local neighborhood rather than a
    single nearest point to estimate descriptor-space extrapolation.  Keeping
    only the best ``k`` values per query preserves the memory bound of the
    existing cross-dataset search even for large reference sets.
    """
    reference = _as_float64(reference)
    query = _as_float64(query)
    if metric not in ("euclidean", "cosine", "manhattan"):
        raise AppError(ANALYSIS_INPUT_INVALID, "cross-dataset metric must be euclidean, cosine, or manhattan")
    if reference.shape[0] == 0:
        raise AppError(ANALYSIS_INPUT_INVALID, "reference descriptor set cannot be empty")
    k_eff = min(max(int(k), 1), reference.shape[0])
    distances = np.full((query.shape[0], k_eff), np.inf, dtype=np.float64)
    indices = np.full((query.shape[0], k_eff), -1, dtype=np.int64)
    # One import lookup per (query x reference) block would repeat a
    # warmup-gated call tens of thousands of times.
    cdist = _safe_import("scipy.spatial.distance", "scipy").cdist
    for start in range(0, query.shape[0], query_chunk):
        stop = min(start + query_chunk, query.shape[0])
        query_block = query[start:stop]
        best_distances = np.full((query_block.shape[0], k_eff), np.inf, dtype=np.float64)
        best_indices = np.full((query_block.shape[0], k_eff), -1, dtype=np.int64)
        query_norm = np.linalg.norm(query_block, axis=1, keepdims=True) if metric == "cosine" else None
        for ref_start in range(0, reference.shape[0], reference_chunk):
            ref_block = reference[ref_start : ref_start + reference_chunk]
            if metric == "euclidean":
                block = cdist(query_block, ref_block, metric="euclidean")
            elif metric == "manhattan":
                block = cdist(query_block, ref_block, metric="cityblock")
            else:
                ref_norm = np.linalg.norm(ref_block, axis=1)
                similarity = (query_block @ ref_block.T) / np.maximum(query_norm * ref_norm[None, :], 1e-15)
                block = np.maximum(1.0 - similarity, 0.0)
            candidate_distances = np.concatenate([best_distances, block], axis=1)
            candidate_indices = np.concatenate([
                best_indices,
                np.broadcast_to(np.arange(ref_start, ref_start + ref_block.shape[0]), block.shape),
            ], axis=1)
            keep = np.argpartition(candidate_distances, k_eff - 1, axis=1)[:, :k_eff]
            row = np.arange(query_block.shape[0])[:, None]
            best_distances = candidate_distances[row, keep]
            best_indices = candidate_indices[row, keep]
            order = np.argsort(best_distances, axis=1, kind="stable")
            best_distances = np.take_along_axis(best_distances, order, axis=1)
            best_indices = np.take_along_axis(best_indices, order, axis=1)
        distances[start:stop] = best_distances
        indices[start:stop] = best_indices
        if progress:
            progress(stop / max(query.shape[0], 1), "nearest-reference neighborhoods")
    return indices, distances

def _local_neighbor_graph(
    samples: DescriptorMatrix,
    cutoff: float,
    max_neighbors: int,
    report: Callable[[float, str], None] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Build a coordinate-aware local-environment graph.

    The returned CSR-like arrays use atom rows as vertices.  Periodic frames
    are queried against translated images.  Every periodic image contact is
    retained, including repeated source indices for small cells; the stored
    index still points to the original atom.  CSR rows are emitted in global
    atom order even when frame rows are interleaved.

    ``max_neighbors`` budgets only the stored neighbour list.  The coordination
    row is the *global* contact count within the cutoff, because it is read as a
    physical quantity while the list is read as a plot (deep review P1-16).

    ``report`` is called between query blocks with a fraction of the neighbour
    phase and is the only cancellation point this function has.
    """
    if samples.positions is None:
        return (
            np.zeros(samples.n_samples, dtype=np.int64),
            np.zeros(samples.n_samples + 1, dtype=np.int64),
            np.zeros(0, dtype=np.int64),
            np.zeros(0, dtype=np.float64),
            ["atom coordinates are unavailable; coordination and neighbor shell were not computed"],
        )
    positions = np.asarray(samples.positions, dtype=np.float64)
    if positions.shape != (samples.n_samples, 3) or not np.isfinite(positions).all():
        return (
            np.zeros(samples.n_samples, dtype=np.int64),
            np.zeros(samples.n_samples + 1, dtype=np.int64),
            np.zeros(0, dtype=np.int64),
            np.zeros(0, dtype=np.float64),
            ["atom coordinates are invalid; coordination and neighbor shell were not computed"],
        )
    cKDTree = _safe_import("scipy.spatial", "scipy").cKDTree
    cells = np.asarray(samples.cells, dtype=np.float64) if samples.cells is not None else None
    pbc = np.asarray(samples.pbc, dtype=bool) if samples.pbc is not None else None
    row_indices: list[list[int]] = [[] for _ in range(samples.n_samples)]
    row_distances: list[list[float]] = [[] for _ in range(samples.n_samples)]
    offsets = np.zeros(samples.n_samples + 1, dtype=np.int64)
    coordination = np.zeros(samples.n_samples, dtype=np.int64)
    warnings: list[str] = []
    processed_atoms = 0
    frame_values = np.asarray(samples.frame, dtype=np.int64)
    for frame in np.unique(frame_values).tolist():
        members = np.flatnonzero(frame_values == int(frame))
        frame_positions = positions[members]
        frame_cell = None
        frame_pbc = None
        if cells is not None and cells.ndim == 3 and cells.shape[0] == samples.n_samples:
            frame_cell = cells[members[0]]
        if pbc is not None and pbc.ndim == 2 and pbc.shape == (samples.n_samples, 3):
            frame_pbc = pbc[members[0]]
        periodic = bool(
            frame_cell is not None
            and frame_cell.shape == (3, 3)
            and frame_pbc is not None
            and frame_pbc.any()
            and abs(float(np.linalg.det(frame_cell))) > 1e-10
        )
        if periodic:
            periodic_axes = [axis for axis in range(3) if bool(frame_pbc[axis])]
            # Wrap periodic coordinates first so the finite image stencil is
            # valid for datasets whose coordinates have crossed a cell many
            # times.  The inverse-cell columns give a conservative number of
            # lattice images needed for the requested Cartesian cutoff.
            frame_inverse = np.linalg.inv(frame_cell)
            fractional = frame_positions @ frame_inverse
            fractional[:, periodic_axes] -= np.floor(fractional[:, periodic_axes])
            graph_positions = fractional @ frame_cell
            shift_limits = image_shift_limits(
                frame_cell,
                cutoff,
                tuple(periodic_axes),
                max_per_axis=_GRAPH_MAX_IMAGES_PER_AXIS,
                max_total_images=_GRAPH_MAX_TOTAL_IMAGES,
            )
            if shift_limits is None:
                # The lattice-coefficient bound needs an unbounded number of
                # images for this cell/cutoff combination. Enumerating them
                # would consume the shift list before any neighbour is found,
                # and the runner cannot be cancelled inside it, so refuse the
                # frame: an unbounded stencil is not a result.
                raise AppError(
                    ANALYSIS_INPUT_INVALID,
                    f"frame {frame}: cell geometry is too narrow for the {cutoff} Å neighbour cutoff",
                    {"frame": int(frame), "cutoff": cutoff},
                )
            shift_tuples = list(product(*[range(-limit, limit + 1) for limit in shift_limits]))
            shifts = []
            for compact_shift in shift_tuples:
                shift = np.zeros(3, dtype=np.float64)
                for axis, value in zip(periodic_axes, compact_shift):
                    shift[axis] = value
                shifts.append(shift)
            shifts_array = np.asarray(shifts, dtype=np.float64)
            translated = (graph_positions[None, :, :] + shifts_array[:, None, :] @ frame_cell).reshape(-1, 3)
            zero_shift_index = shift_tuples.index(tuple(0 for _ in periodic_axes))
            tree = cKDTree(translated)
        else:
            graph_positions = frame_positions
            zero_shift_index = 0
            tree = cKDTree(frame_positions)

        # One scipy pass per query block rather than a Python loop per candidate:
        # query_ball_point no longer returns distances, and recomputing them with
        # np.linalg.norm cost more than the search itself (1.6M norm calls and
        # two thirds of the runtime on a 20k-atom frame).
        atom_count = members.size
        for block_start in range(0, atom_count, _GRAPH_QUERY_BLOCK):
            stop = min(block_start + _GRAPH_QUERY_BLOCK, atom_count)
            block_slice = slice(block_start, stop)
            pairs = cKDTree(graph_positions[block_slice]).sparse_distance_matrix(
                tree, cutoff, p=2.0, output_type="coo_matrix"
            )
            # `translated` is shift-major over the frame's atoms, so an image
            # index decodes as shift * atom_count + source.
            row = np.asarray(pairs.row, dtype=np.int64) + block_start
            image = np.asarray(pairs.col, dtype=np.int64)
            distance = np.asarray(pairs.data, dtype=np.float64)
            source = image % atom_count
            shift = image // atom_count
            # Do not collapse by ``source``: two different periodic image
            # contacts of the same atom are distinct neighbors in a small unit
            # cell (for example the six self-images of a cubic cell).
            contact = (distance > 1e-10) & ~((source == row) & (shift == zero_shift_index))
            row, source, distance, image = (values[contact] for values in (row, source, distance, image))
            # The stored order is part of the result: nearest first, ties broken
            # by source atom and then by image index.
            order = np.lexsort((image, source, distance, row))
            row, source, distance = row[order], source[order], distance[order]
            counts = np.bincount(row - block_start, minlength=stop - block_start)
            starts = np.zeros(counts.size + 1, dtype=np.int64)
            np.cumsum(counts, out=starts[1:])
            for offset, local_index in enumerate(range(block_start, stop)):
                lo, hi = int(starts[offset]), int(starts[offset + 1])
                global_index = int(members[local_index])
                # The coordination number is the physical contact count;
                # max_neighbors budgets only the stored neighbour list. Capping
                # the number itself made dense environments report the budget as
                # a material property (deep review P1-16: 3000 atoms at 3 Å
                # pinned at mean == max).
                coordination[global_index] = hi - lo
                kept = min(hi, lo + max_neighbors)
                row_indices[global_index] = members[source[lo:kept]].tolist()
                row_distances[global_index] = distance[lo:kept].tolist()
            if report:
                processed_atoms += stop - block_start
                report(processed_atoms / max(samples.n_samples, 1), "building local neighbor graph")

    graph_indices: list[int] = []
    graph_distances: list[float] = []
    for global_index in range(samples.n_samples):
        offsets[global_index] = len(graph_indices)
        graph_indices.extend(row_indices[global_index])
        graph_distances.extend(row_distances[global_index])
    offsets[-1] = len(graph_indices)
    return coordination, offsets, np.asarray(graph_indices, dtype=np.int64), np.asarray(graph_distances, dtype=np.float64), warnings

def _joint_projection(reference: np.ndarray, query: np.ndarray, max_each: int) -> dict[str, np.ndarray]:
    ref_indices = _bounded_indices(reference.shape[0], max_each)
    query_indices = _bounded_indices(query.shape[0], max_each)
    stacked = np.vstack([reference[ref_indices], query[query_indices]])
    coords = _visual_pca(stacked)
    return {
        "projection_coords": coords,
        "projection_source": np.concatenate([
            np.zeros(ref_indices.size, dtype=np.int64),
            np.ones(query_indices.size, dtype=np.int64),
        ]),
        "projection_sample_indices": np.concatenate([ref_indices, query_indices]).astype(np.int64),
    }

def _aligned_space_metrics(left: np.ndarray, right: np.ndarray, params: dict) -> tuple[dict[str, float], dict[str, np.ndarray]]:
    """Compare two aligned descriptor spaces without requiring equal feature counts."""
    left = _as_float64(left)
    right = _as_float64(right)
    if left.shape[0] != right.shape[0]:
        raise AppError(ANALYSIS_INPUT_INVALID, "aligned descriptor spaces require matching sample counts")
    limit = min(_int_param(params, "max_samples", 600, 2), 2_000)
    sample_indices = _bounded_indices(left.shape[0], limit)
    a = left[sample_indices]
    b = right[sample_indices]
    left_matrix = _pairwise_matrix(a, "euclidean")
    right_matrix = _pairwise_matrix(b, "euclidean")
    triangle = np.triu_indices(sample_indices.size, 1)
    left_pairs = left_matrix[triangle]
    right_pairs = right_matrix[triangle]
    pearson = _safe_correlation(left_pairs, right_pairs)
    spearman = _rank_correlation(left_pairs, right_pairs)

    k = min(_int_param(params, "k", 10, 1), max(sample_indices.size - 1, 1))

    def nearest_by_identity(matrix: np.ndarray) -> np.ndarray:
        # Not `[:, 1:k+1]`: a duplicate row ties at distance 0, so the query is
        # not necessarily first after argsort, and the slice then kept the row's
        # own index while dropping a real neighbour - a leak on both sides of the
        # comparison inflates the overlap. `_nearest_distances` is fixed the same
        # way (deep review pass 5, 5-C5).
        candidates = matrix.copy()
        np.fill_diagonal(candidates, np.inf)
        return np.argsort(candidates, axis=1, kind="stable")[:, :k]

    left_neighbors = nearest_by_identity(left_matrix)
    right_neighbors = nearest_by_identity(right_matrix)
    overlap = np.asarray(
        [len(set(a_row.tolist()) & set(b_row.tolist())) / max(k, 1) for a_row, b_row in zip(left_neighbors, right_neighbors)],
        dtype=np.float64,
    )

    left_coords = _visual_pca(a)
    right_coords = _visual_pca(b)
    left_norm = left_coords / max(float(np.linalg.norm(left_coords)), 1e-15)
    right_norm = right_coords / max(float(np.linalg.norm(right_coords)), 1e-15)
    u, _s, vt = np.linalg.svd(right_norm.T @ left_norm, full_matrices=False)
    aligned_right = right_norm @ (u @ vt)
    topology_error = float(np.sqrt(np.mean((left_norm - aligned_right) ** 2)))

    cluster_count = min(_int_param(params, "n_clusters", 6, 2), sample_indices.size)
    kmeans = _safe_import("sklearn.cluster", "scikit-learn").KMeans
    left_labels = kmeans(n_clusters=cluster_count, random_state=_seed(params), n_init=10).fit_predict(a)
    right_labels = kmeans(n_clusters=cluster_count, random_state=_seed(params), n_init=10).fit_predict(b)
    cluster_stability = float(
        _safe_import("sklearn.metrics", "scikit-learn").adjusted_rand_score(left_labels, right_labels)
    )

    pair_limit = min(_int_param(params, "display_pairs", 50_000, 100), 100_000)
    pair_indices = _bounded_indices(left_pairs.size, pair_limit)
    metrics = {
        "pairwise_distance_pearson": pearson,
        "pairwise_distance_spearman": spearman,
        "neighbor_overlap": float(overlap.mean()),
        "pca_topology_error": topology_error,
        "clustering_stability": cluster_stability,
    }
    arrays = {
        "sample_indices": sample_indices,
        "left_coords": left_coords,
        "right_coords": right_coords,
        "left_pair_distances": left_pairs[pair_indices].astype(np.float64),
        "right_pair_distances": right_pairs[pair_indices].astype(np.float64),
        "neighbor_overlap": overlap,
    }
    return metrics, arrays
