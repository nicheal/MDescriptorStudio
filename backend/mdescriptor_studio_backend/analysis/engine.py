"""Numerical analysis engine used by :mod:`services.analysis_service`.

All calculations operate on float64 arrays.  Large arrays are returned to the
service as named artifacts rather than being serialised into an IPC response.
The methods below intentionally expose a small, stable parameter surface; the
Studio API should not become a thin and version-fragile wrapper around every
scikit-learn keyword.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import numpy as np

from ..errors import (
    ANALYSIS_DEPENDENCY_MISSING,
    ANALYSIS_INPUT_INVALID,
    ANALYSIS_INSUFFICIENT_SAMPLES,
    AppError,
)


MAX_PREVIEW_POINTS = 20_000


@dataclass
class SampleMatrix:
    """A matrix plus the identity needed to navigate back to Explore."""

    values: np.ndarray
    frame: np.ndarray
    row: np.ndarray | None = None
    sample_ids: list[str] = field(default_factory=list)
    elements: np.ndarray | None = None
    mode: str = "structure"
    warnings: list[str] = field(default_factory=list)
    properties: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def n_samples(self) -> int:
        return int(self.values.shape[0])

    @property
    def n_features(self) -> int:
        return int(self.values.shape[1])


def _as_float64(values: Any) -> np.ndarray:
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
    # Do not silently drop bad rows: a NaN/Inf descriptor usually indicates a
    # broken upstream calculation and hiding it would destroy reproducibility.
    finite = np.isfinite(array)
    if not bool(finite.all()):
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


def _preprocess(x: np.ndarray, params: dict, default: str) -> tuple[np.ndarray, list[str], np.ndarray]:
    x = _as_float64(x)
    mode = params.get("preprocess", default)
    if mode not in ("raw", "center", "standardized"):
        raise AppError(ANALYSIS_INPUT_INVALID, "preprocess must be raw, center, or standardized")
    warnings: list[str] = []
    means = x.mean(axis=0)
    centered = x - means
    variances = centered.var(axis=0)
    scale = np.sqrt(variances)
    keep = scale > np.finfo(np.float64).eps
    if not bool(keep.all()):
        warnings.append(f"ignored {int((~keep).sum())} zero-variance feature(s)")
    # Keeping at least one column makes constant descriptors report a useful
    # structured error in algorithms that need a non-empty feature space.
    if not bool(keep.any()):
        keep = np.ones(x.shape[1], dtype=bool)
        scale = np.ones(x.shape[1], dtype=np.float64)
    if mode == "raw":
        return x[:, keep], warnings, keep
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


def _safe_import(module: str, package: str | None = None):
    try:
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
    return indices[:, 1:], distances[:, 1:]


def _bounded_indices(count: int, limit: int) -> np.ndarray:
    """Return deterministic, order-preserving indices for bounded visual artifacts."""
    if count <= limit:
        return np.arange(count, dtype=np.int64)
    return np.linspace(0, count - 1, limit, dtype=np.int64)


def _visual_pca(x: np.ndarray) -> np.ndarray:
    """Fast deterministic two-dimensional PCA used only as a visual companion."""
    x = _as_float64(x)
    if x.shape[0] == 1:
        return np.zeros((1, 2), dtype=np.float64)
    centered = x - x.mean(axis=0)
    components = min(2, centered.shape[0], centered.shape[1])
    if components == 0:
        return np.zeros((x.shape[0], 2), dtype=np.float64)
    if min(centered.shape) > 4:
        pca = _safe_import("sklearn.decomposition", "scikit-learn").PCA(
            n_components=components,
            svd_solver="randomized",
            random_state=42,
        )
        coords = np.asarray(pca.fit_transform(centered), dtype=np.float64)
    else:
        _u, _singular, vt = np.linalg.svd(centered, full_matrices=False)
        coords = centered @ vt[:components].T
    if components < 2:
        coords = np.pad(coords, ((0, 0), (0, 2 - components)))
    return coords.astype(np.float64, copy=False)


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
    if float(a.std()) <= np.finfo(np.float64).eps or float(b.std()) <= np.finfo(np.float64).eps:
        return 1.0 if np.allclose(a, b) else 0.0
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


def _preprocess_reference_query(reference: np.ndarray, query: np.ndarray, params: dict, default: str = "raw") -> tuple[np.ndarray, np.ndarray, list[str], np.ndarray]:
    reference = _as_float64(reference)
    query = _as_float64(query)
    if reference.shape[1] != query.shape[1]:
        raise AppError(ANALYSIS_INPUT_INVALID, "reference and query feature counts do not match")
    mode = params.get("preprocess", default)
    if mode not in ("raw", "center", "standardized"):
        raise AppError(ANALYSIS_INPUT_INVALID, "preprocess must be raw, center, or standardized")
    means = reference.mean(axis=0)
    centered_reference = reference - means
    scale = centered_reference.std(axis=0)
    keep = scale > np.finfo(np.float64).eps
    warnings: list[str] = []
    if not bool(keep.all()):
        warnings.append(f"ignored {int((~keep).sum())} zero-variance reference feature(s)")
    if not bool(keep.any()):
        keep = np.ones(reference.shape[1], dtype=bool)
        scale = np.ones(reference.shape[1], dtype=np.float64)
    if mode == "raw":
        return reference[:, keep], query[:, keep], warnings, keep
    centered_query = query[:, keep] - means[keep]
    if mode == "center":
        return centered_reference[:, keep], centered_query, warnings, keep
    denominator = np.where(scale[keep] > 0, scale[keep], 1.0)
    return centered_reference[:, keep] / denominator, centered_query / denominator, warnings, keep


def _cross_nearest(reference: np.ndarray, query: np.ndarray, metric: str, query_chunk: int, reference_chunk: int, progress: Callable[[float, str], None] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Bounded-memory nearest-reference search with source identities."""
    reference = _as_float64(reference)
    query = _as_float64(query)
    if metric not in ("euclidean", "cosine"):
        raise AppError(ANALYSIS_INPUT_INVALID, "cross-dataset metric must be euclidean or cosine")
    distances = np.empty(query.shape[0], dtype=np.float64)
    nearest = np.empty(query.shape[0], dtype=np.int64)
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
                matrix = _safe_import("scipy.spatial.distance", "scipy").cdist(
                    query_block,
                    ref_block,
                    metric="euclidean",
                )
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
    left_neighbors = np.argsort(left_matrix, axis=1)[:, 1 : k + 1]
    right_neighbors = np.argsort(right_matrix, axis=1)[:, 1 : k + 1]
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


class AnalysisEngine:
    """Pure numerical methods with deterministic defaults."""

    @staticmethod
    def warmup() -> dict[str, bool]:
        """Import optional numeric backends on the backend main thread.

        UMAP/numba and some native sklearn dependencies can acquire process
        import locks or initialize DLL state during their first import. Doing
        that work before JobService starts worker threads prevents a Windows
        sidecar from hanging at ``loading descriptor results`` on its first
        UMAP/HDBSCAN request. Missing optional packages are reported and are
        still converted to ANALYSIS_DEPENDENCY_MISSING when selected.
        """
        import importlib
        import os
        import sys

        if getattr(sys, "frozen", False):
            # UMAP ships numba decorators with cache=True. In a PyInstaller
            # onefile executable inspect.getfile() returns a synthetic module
            # path, so Numba's normal source-backed locators reject it before
            # the frozen-executable fallback can be used. Install a locator
            # directly instead of relying on the environment variable: Numba
            # reads its config during import, which may already have happened
            # through a bundled native dependency.
            import numba
            import tempfile
            from numba.core import caching

            class _FrozenCacheLocator(caching.UserWideCacheLocator):
                def __init__(self, py_func, py_file):
                    self._py_file = py_file
                    self._lineno = py_func.__code__.co_firstlineno
                    # Keep the cache beside the Studio data directory (or the
                    # OS temp directory for a bare protocol smoke test). The
                    # user-profile cache can be read-only on managed Windows
                    # installations, which would make every locator reject
                    # the frozen module.
                    root = os.environ.get("MDS_DATA_DIR") or tempfile.gettempdir()
                    subpath = self.get_suitable_cache_subpath(py_file)
                    self._cache_path = os.path.join(root, "numba-cache", subpath)

                @classmethod
                def from_function(cls, py_func, py_file):
                    self = cls(py_func, py_file)
                    try:
                        self.ensure_cache_path()
                    except OSError:
                        return None
                    return self

                def get_source_stamp(self):
                    try:
                        stat = os.stat(self._py_file)
                    except OSError:
                        stat = os.stat(sys.executable)
                    return stat.st_mtime, stat.st_size

            numba.config.CACHE_LOCATOR_CLASSES = ""
            caching.CacheImpl._locator_classes = [_FrozenCacheLocator]
            caching.CompileResultCacheImpl._locator_classes = [_FrozenCacheLocator]

        availability: dict[str, bool] = {}
        for name in ("sklearn", "umap", "hdbscan"):
            try:
                importlib.import_module(name)
            except ImportError:
                availability[name] = False
            else:
                availability[name] = True
        return availability

    @staticmethod
    def pca(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        _check_samples(samples.values, 2)
        x, warnings, keep = _preprocess(samples.values, params, "center")
        if progress:
            progress(0.2, "preparing PCA")
        _, singular, vt = np.linalg.svd(x, full_matrices=False)
        variance = (singular * singular) / max(x.shape[0] - 1, 1)
        total = float(variance.sum())
        explained = variance / total if total > 0 else np.zeros_like(variance)
        components = min(2, vt.shape[0])
        coords = x @ vt[:components].T
        if components < 2:
            coords = np.pad(coords, ((0, 0), (0, 2 - components)))
        if progress:
            progress(1.0, "PCA complete")
        return {
            "arrays": {"coords": coords.astype(np.float64, copy=False), "explained_variance": explained.astype(np.float64)},
            "preview": {
                "kind": "projection",
                "x_label": f"PC1 ({explained[0] * 100:.1f}%)" if explained.size else "PC1",
                "y_label": f"PC2 ({explained[1] * 100:.1f}%)" if explained.size > 1 else "PC2",
                "explained_variance": explained[: min(10, explained.size)].tolist(),
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def umap(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        _check_samples(samples.values, 3)
        umap_mod = _safe_import("umap", "umap-learn")
        x, warnings, keep = _preprocess(samples.values, params, "raw")
        n_neighbors = _int_param(params, "n_neighbors", 15, 2)
        if n_neighbors >= x.shape[0]:
            n_neighbors = x.shape[0] - 1
        if n_neighbors < 2:
            raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "UMAP needs at least 3 samples")
        min_dist = _float_param(params, "min_dist", 0.1, 0.0, 1.0)
        metric = params.get("metric", "euclidean")
        if metric not in ("euclidean", "cosine", "manhattan"):
            raise AppError(ANALYSIS_INPUT_INVALID, "UMAP metric must be euclidean, cosine, or manhattan")
        if progress:
            progress(0.1, "fitting UMAP")
        reducer = umap_mod.UMAP(
            n_components=2,
            n_neighbors=n_neighbors,
            min_dist=min_dist,
            metric=metric,
            random_state=_seed(params),
            transform_seed=_seed(params),
            n_jobs=1,
        )
        coords = reducer.fit_transform(x).astype(np.float64)
        if progress:
            progress(1.0, "UMAP complete")
        return {
            "arrays": {"coords": coords},
            "preview": {"kind": "projection", "x_label": "UMAP-1", "y_label": "UMAP-2", "parameters": {"n_neighbors": n_neighbors, "min_dist": min_dist, "metric": metric}},
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def tsne(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        _check_samples(samples.values, 4)
        x, warnings, keep = _preprocess(samples.values, params, "raw")
        default_perplexity = min(30.0, max(2.0, float(x.shape[0] - 1)))
        perplexity = _float_param(params, "perplexity", default_perplexity, 2.0)
        if perplexity >= x.shape[0]:
            raise AppError(ANALYSIS_INPUT_INVALID, "t-SNE perplexity must be smaller than sample count")
        max_iter = _int_param(params, "max_iter", 1000, 250)
        tsne_cls = _safe_import("sklearn.manifold", "scikit-learn").TSNE
        if progress:
            progress(0.1, "fitting t-SNE")
        try:
            model = tsne_cls(n_components=2, perplexity=perplexity, max_iter=max_iter, random_state=_seed(params), init="pca", learning_rate="auto")
        except TypeError:  # sklearn <1.5
            model = tsne_cls(n_components=2, perplexity=perplexity, n_iter=max_iter, random_state=_seed(params), init="pca", learning_rate="auto")
        coords = model.fit_transform(x).astype(np.float64)
        if progress:
            progress(1.0, "t-SNE complete")
        return {
            "arrays": {"coords": coords},
            "preview": {"kind": "projection", "x_label": "t-SNE-1", "y_label": "t-SNE-2", "parameters": {"perplexity": perplexity, "max_iter": max_iter}},
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def neighbors(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        x, warnings, keep = _preprocess(samples.values, params, "raw")
        k = _int_param(params, "k", 10, 1)
        metric = params.get("metric", "euclidean")
        if metric not in ("euclidean", "cosine", "manhattan"):
            raise AppError(ANALYSIS_INPUT_INVALID, "metric must be euclidean, cosine, or manhattan")
        indices, distances = _nearest_distances(x, k, metric)
        if progress:
            progress(1.0, "nearest neighbors complete")
        return {"arrays": {"indices": indices.astype(np.int64), "distances": distances.astype(np.float64)}, "preview": {"kind": "neighbors", "k": int(indices.shape[1]), "metric": metric}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}

    @staticmethod
    def similarity(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
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

    @staticmethod
    def pairwise(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
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
                "distance_min": float(distances.min()),
                "distance_max": float(distances.max()),
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def cluster(samples: SampleMatrix, params: dict, algorithm: str, progress: Callable[[float, str], None] | None = None) -> dict:
        x, warnings, keep = _preprocess(samples.values, params, "standardized")
        algorithm = algorithm.lower()
        if algorithm == "kmeans":
            k = _int_param(params, "n_clusters", 6, 2)
            if k > x.shape[0]:
                raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "n_clusters cannot exceed sample count")
            cls = _safe_import("sklearn.cluster", "scikit-learn").KMeans
            model = cls(n_clusters=k, random_state=_seed(params), n_init=10, max_iter=_int_param(params, "max_iter", 300, 1))
        elif algorithm == "dbscan":
            eps = _float_param(params, "eps", 0.5, 0.0)
            min_samples = _int_param(params, "min_samples", 5, 1)
            model = _safe_import("sklearn.cluster", "scikit-learn").DBSCAN(eps=eps, min_samples=min_samples, metric=params.get("metric", "euclidean"))
        elif algorithm == "hdbscan":
            hdb = _safe_import("hdbscan", "hdbscan")
            model = hdb.HDBSCAN(min_cluster_size=_int_param(params, "min_cluster_size", 5, 2), min_samples=params.get("min_samples"), metric=params.get("metric", "euclidean"), prediction_data=False)
        elif algorithm in ("agglomerative", "hierarchical"):
            k = _int_param(params, "n_clusters", 6, 2)
            if k > x.shape[0]:
                raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "n_clusters cannot exceed sample count")
            cls = _safe_import("sklearn.cluster", "scikit-learn").AgglomerativeClustering
            model = cls(n_clusters=k, linkage=params.get("linkage", "ward"))
        else:
            raise AppError(ANALYSIS_INPUT_INVALID, f"unsupported cluster algorithm: {algorithm}")
        if progress:
            progress(0.1, f"fitting {algorithm}")
        labels = np.asarray(model.fit_predict(x), dtype=np.int64)
        arrays: dict[str, np.ndarray] = {"labels": labels, "coords": _visual_pca(x)}
        if hasattr(model, "cluster_centers_"):
            arrays["centers"] = np.asarray(model.cluster_centers_, dtype=np.float64)
        if hasattr(model, "probabilities_"):
            arrays["probabilities"] = np.asarray(model.probabilities_, dtype=np.float64)
        if progress:
            progress(1.0, f"{algorithm} complete")
        return {"arrays": arrays, "preview": {"kind": "clusters", "algorithm": algorithm, "cluster_count": int(len(set(labels.tolist())) - (1 if -1 in labels else 0)), "noise_count": int((labels == -1).sum())}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}

    @staticmethod
    def outlier(samples: SampleMatrix, params: dict, algorithm: str, progress: Callable[[float, str], None] | None = None) -> dict:
        x, warnings, keep = _preprocess(samples.values, params, "standardized")
        algorithm = algorithm.lower()
        contamination = _float_param(params, "contamination", 0.01, 0.0, 0.5)
        if contamination <= 0:
            contamination = "auto"
        if algorithm in ("knn", "k-nearest-neighbor"):
            k = _int_param(params, "k", 10, 1)
            _, distances = _nearest_distances(x, k, "euclidean")
            scores = distances.mean(axis=1)
            threshold = float(np.quantile(scores, 1.0 - (0.01 if contamination == "auto" else contamination)))
            labels = (scores > threshold).astype(np.int64)
        elif algorithm == "lof":
            _check_samples(x, 3)
            cls = _safe_import("sklearn.neighbors", "scikit-learn").LocalOutlierFactor
            model = cls(n_neighbors=min(_int_param(params, "k", 20, 2), x.shape[0] - 1), contamination=contamination)
            predicted = np.asarray(model.fit_predict(x))
            scores = -np.asarray(model.negative_outlier_factor_, dtype=np.float64)
            labels = (predicted < 0).astype(np.int64)
        elif algorithm in ("isolation_forest", "isolation-forest", "iforest"):
            cls = _safe_import("sklearn.ensemble", "scikit-learn").IsolationForest
            model = cls(contamination=contamination, random_state=_seed(params), n_estimators=_int_param(params, "n_estimators", 200, 10), n_jobs=1)
            model.fit(x)
            scores = -np.asarray(model.score_samples(x), dtype=np.float64)
            labels = (np.asarray(model.predict(x)) < 0).astype(np.int64)
        elif algorithm in ("mahalanobis", "mahalanobis_distance"):
            center = x.mean(axis=0)
            cov = np.cov(x, rowvar=False)
            cov = np.atleast_2d(cov) + np.eye(x.shape[1]) * 1e-10
            inv = np.linalg.pinv(cov)
            delta = x - center
            scores = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", delta, inv, delta), 0.0))
            threshold = float(np.quantile(scores, 1.0 - (0.01 if contamination == "auto" else contamination)))
            labels = (scores > threshold).astype(np.int64)
        else:
            raise AppError(ANALYSIS_INPUT_INVALID, f"unsupported outlier algorithm: {algorithm}")
        if progress:
            progress(1.0, f"{algorithm} complete")
        return {"arrays": {"scores": scores.astype(np.float64), "labels": labels, "coords": _visual_pca(x)}, "preview": {"kind": "outliers", "algorithm": algorithm, "outlier_count": int(labels.sum()), "contamination": contamination}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}

    @staticmethod
    def sampling(samples: SampleMatrix, params: dict, algorithm: str, progress: Callable[[float, str], None] | None = None) -> dict:
        x = _as_float64(samples.values)
        n = x.shape[0]
        target = min(_int_param(params, "n_samples", 1000, 1), n)
        algorithm = algorithm.lower().replace("-", "_")
        rng = np.random.default_rng(_seed(params))
        if algorithm == "random":
            selected = np.sort(rng.choice(n, size=target, replace=False))
        elif algorithm in ("fps", "farthest_point"):
            selected = np.empty(target, dtype=np.int64)
            selected[0] = int(params.get("start_index", 0)) % n
            min_dist = np.full(n, np.inf, dtype=np.float64)
            for i in range(1, target):
                if progress and (i % 100 == 0 or i == target - 1):
                    progress(i / max(target, 1), "farthest-point sampling")
                delta = x - x[selected[i - 1]]
                dist = np.einsum("ij,ij->i", delta, delta)
                min_dist = np.minimum(min_dist, dist)
                min_dist[selected[:i]] = -1.0
                selected[i] = int(np.argmax(min_dist))
            selected = np.sort(selected)
        elif algorithm == "stratified":
            labels = samples.elements
            if labels is None or len(labels) != n:
                # Structure-level stratification has a stable frame fallback.
                labels = samples.frame
            selected_list: list[int] = []
            groups = np.unique(labels)
            for group in groups:
                members = np.flatnonzero(labels == group)
                count = max(1, round(target * len(members) / n))
                count = min(count, len(members))
                selected_list.extend(rng.choice(members, size=count, replace=False).tolist())
            selected = np.asarray(sorted(set(selected_list)), dtype=np.int64)
            if selected.size > target:
                selected = np.sort(rng.choice(selected, size=target, replace=False))
        elif algorithm in ("cluster", "cluster_representative"):
            k = _int_param(params, "n_clusters", min(6, max(2, target)), 2)
            cls = _safe_import("sklearn.cluster", "scikit-learn").KMeans
            model = cls(n_clusters=min(k, n), random_state=_seed(params), n_init=10).fit(x)
            selected_list = []
            for center in model.cluster_centers_:
                members = np.flatnonzero(model.labels_ == len(selected_list))
                if len(members):
                    distances = ((x[members] - center) ** 2).sum(axis=1)
                    selected_list.append(int(members[int(np.argmin(distances))]))
            selected = np.asarray(selected_list, dtype=np.int64)
            if selected.size < target:
                remaining = np.setdiff1d(np.arange(n), selected, assume_unique=False)
                selected = np.concatenate([selected, remaining[: target - selected.size]])
            selected = np.sort(selected[:target])
        elif algorithm in ("per_element", "element"):
            labels = samples.elements
            if labels is None or len(labels) != n:
                raise AppError(ANALYSIS_INPUT_INVALID, "per-element sampling requires atom-level element metadata")
            selected_list = []
            for group in np.unique(labels):
                members = np.flatnonzero(labels == group)
                count = max(1, round(target * len(members) / n))
                selected_list.extend(rng.choice(members, size=min(count, len(members)), replace=False).tolist())
            selected = np.asarray(sorted(set(selected_list)), dtype=np.int64)
            if selected.size > target:
                selected = np.sort(rng.choice(selected, size=target, replace=False))
        else:
            raise AppError(ANALYSIS_INPUT_INVALID, f"unsupported sampling algorithm: {algorithm}")
        if progress:
            progress(1.0, f"{algorithm} sampling complete")
        return {"arrays": {"selected_indices": selected.astype(np.int64), "coords": _visual_pca(x)}, "preview": {"kind": "sampling", "algorithm": algorithm, "selected_count": int(selected.size), "requested_count": int(target)}, "warnings": []}

    @staticmethod
    def coverage(reference: SampleMatrix, query: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        ref, qry, warnings, keep = _preprocess_reference_query(reference.values, query.values, params, "raw")
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
        return {"arrays": arrays, "preview": {"kind": "coverage", "categories": ["covered", "marginal", "out_of_coverage"], "q95": q95, "q99": q99, "metric": metric, "covered": int((labels == 0).sum()), "marginal": int((labels == 1).sum()), "out_of_coverage": int((labels == 2).sum()), "mean_distance": float(distances.mean()), "median_distance": float(np.median(distances)), "max_distance": float(distances.max())}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}

    @staticmethod
    def overlap(reference: SampleMatrix, query: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        ref, qry, warnings, keep = _preprocess_reference_query(reference.values, query.values, params, "standardized")
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

    @staticmethod
    def acquisition(reference: SampleMatrix, query: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        """Select a novel and diverse query batch without entering model inference."""
        ref, qry, warnings, keep = _preprocess_reference_query(reference.values, query.values, params, "standardized")
        metric = str(params.get("metric") or "euclidean")
        nearest, novelty = _cross_nearest(
            ref,
            qry,
            metric,
            _int_param(params, "chunk_size", 2048, 1),
            _int_param(params, "reference_chunk_size", 2048, 1),
            progress,
        )
        target = min(_int_param(params, "n_samples", 100, 1), qry.shape[0])
        pool_factor = _float_param(params, "pool_factor", 5.0, 1.0)
        pool_size = min(qry.shape[0], max(target, int(round(target * pool_factor))))
        pool = np.argsort(-novelty, kind="stable")[:pool_size]
        novelty_scale = np.ptp(novelty[pool])
        normalized_novelty = (novelty[pool] - novelty[pool].min()) / max(float(novelty_scale), 1e-15)
        novelty_weight = _float_param(params, "novelty_weight", 0.65, 0.0, 1.0)
        selected_local = [int(np.argmax(normalized_novelty))]
        min_diversity = np.full(pool_size, np.inf, dtype=np.float64)
        acquisition_score = np.zeros(pool_size, dtype=np.float64)
        for step in range(1, target):
            last = qry[pool[selected_local[-1]]]
            distances = np.linalg.norm(qry[pool] - last, axis=1)
            min_diversity = np.minimum(min_diversity, distances)
            diversity_scale = np.ptp(min_diversity[np.isfinite(min_diversity)]) if np.isfinite(min_diversity).any() else 0.0
            normalized_diversity = (min_diversity - np.nanmin(min_diversity)) / max(float(diversity_scale), 1e-15)
            acquisition_score = novelty_weight * normalized_novelty + (1.0 - novelty_weight) * normalized_diversity
            acquisition_score[selected_local] = -1.0
            selected_local.append(int(np.argmax(acquisition_score)))
            if progress and (step % 50 == 0 or step == target - 1):
                progress(step / max(target, 1), "novelty-diversity acquisition")
        selected = pool[np.asarray(selected_local, dtype=np.int64)]
        full_scores = np.zeros(qry.shape[0], dtype=np.float64)
        if np.isfinite(min_diversity).any():
            diversity_component = np.nan_to_num(min_diversity / max(float(np.nanmax(min_diversity[np.isfinite(min_diversity)])), 1e-15), posinf=0.0)
        else:
            diversity_component = np.zeros(pool_size, dtype=np.float64)
        full_scores[pool] = novelty_weight * normalized_novelty + (1.0 - novelty_weight) * diversity_component
        arrays = {
            "selected_indices": selected.astype(np.int64),
            "nearest_indices": nearest,
            "distances": novelty,
            "scores": full_scores,
            "coords": _visual_pca(qry),
        }
        return {
            "arrays": arrays,
            "preview": {
                "kind": "acquisition",
                "algorithm": "novelty_fps",
                "selected_count": int(selected.size),
                "candidate_pool": int(pool_size),
                "novelty_weight": novelty_weight,
                "mean_selected_novelty": float(novelty[selected].mean()),
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def compare(left: SampleMatrix, right: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
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

    @staticmethod
    def feature_variance(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        x = _as_float64(samples.values)
        variance = x.var(axis=0)
        order = np.argsort(-variance, kind="stable")
        top_k = min(_int_param(params, "top_k", 20, 1), x.shape[1])
        threshold = _float_param(params, "variance_threshold", 1e-12, 0.0)
        near_zero = variance <= threshold
        if progress:
            progress(1.0, "feature variance complete")
        return {"arrays": {"variance": variance, "top_indices": order[:top_k].astype(np.int64), "near_zero_mask": near_zero.astype(np.int64)}, "preview": {"kind": "feature_variance", "feature_count": int(x.shape[1]), "top_k": top_k, "top_indices": order[:top_k].tolist(), "top_values": variance[order[:top_k]].tolist(), "variance_threshold": threshold, "near_zero_count": int(near_zero.sum()), "effective_nonzero_dimensions": int((~near_zero).sum())}, "warnings": []}

    @staticmethod
    def feature_correlation(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        x = _as_float64(samples.values)
        _check_samples(x, 2)
        variance = x.var(axis=0)
        variance_threshold = _float_param(params, "variance_threshold", 1e-12, 0.0)
        valid = variance > variance_threshold
        warnings = [f"ignored {int((~valid).sum())} zero-variance feature(s)"] if not bool(valid.all()) else []
        xv = x[:, valid]
        if xv.shape[1] < 2:
            raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "at least two non-constant features are required")
        top_k = _int_param(params, "top_k", 50, 1)
        standardized = (xv - xv.mean(axis=0)) / xv.std(axis=0)
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
        redundancy_threshold = _float_param(params, "redundancy_threshold", 0.95, 0.0, 1.0)
        high_pairs = np.argwhere(np.triu(np.abs(bounded_matrix) >= redundancy_threshold, 1))
        redundant_features: set[int] = set()
        for left_index, right_index in high_pairs.tolist():
            redundant_features.add(int(feature_indices[max(left_index, right_index)]))
        if progress:
            progress(1.0, "feature correlation complete")
        return {"arrays": arrays, "preview": {"kind": "feature_correlation", "feature_count": int(xv.shape[1]), "zero_variance_count": int((~valid).sum()), "redundancy_threshold": redundancy_threshold, "highly_correlated_pairs": int(high_pairs.shape[0]), "redundant_feature_count": int(len(redundant_features)), "redundancy_ratio": float(len(redundant_features) / max(int(xv.shape[1]), 1)), "pairs": [{"feature_a": int(a), "feature_b": int(b), "correlation": float(v)} for (a, b), v in zip(pairs.tolist(), values.tolist())]}, "warnings": warnings}

    @staticmethod
    def property_correlation(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
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
        x, warnings, keep = _preprocess(samples.values[valid], params, "standardized")
        standardized_target = (y - y.mean()) / y.std()
        correlations = (x.T @ standardized_target) / max(x.shape[0], 1)
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
        predictions = np.asarray(sklearn_model_selection.cross_val_predict(model, x, y, cv=splitter, n_jobs=1), dtype=np.float64)
        r2 = float(sklearn_metrics.r2_score(y, predictions))
        rmse = float(np.sqrt(sklearn_metrics.mean_squared_error(y, predictions)))
        mae = float(sklearn_metrics.mean_absolute_error(y, predictions))

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
        if progress:
            progress(1.0, "property correlation complete")
        return {
            "arrays": {
                "sample_indices": np.flatnonzero(valid).astype(np.int64),
                "targets": y,
                "predictions": predictions,
                "residuals": (y - predictions).astype(np.float64),
                "feature_correlations": correlations.astype(np.float64),
                "top_indices": np.flatnonzero(keep)[order[:top_k]].astype(np.int64),
                "pair_distance": pair_distance.astype(np.float64),
                "pair_property_delta": pair_property_delta.astype(np.float64),
            },
            "preview": {
                "kind": "property_correlation",
                "property": property_name,
                "sample_count": int(y.size),
                "missing_count": int((~valid).sum()),
                "r2": r2,
                "rmse": rmse,
                "mae": mae,
                "distance_property_correlation": distance_property_correlation,
                "top_features": [
                    {"feature": int(np.flatnonzero(keep)[index]), "correlation": float(correlations[index])}
                    for index in order[:top_k]
                ],
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def local_diversity(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        if samples.mode != "atom" or samples.row is None:
            raise AppError(ANALYSIS_INPUT_INVALID, "local diversity requires atom/local-environment samples")
        x, warnings, keep = _preprocess(samples.values, params, "standardized")
        _check_samples(x, 3)
        elements = np.asarray(samples.elements if samples.elements is not None else np.zeros(x.shape[0]), dtype=np.int64)
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
                "effective_dimension": effective_dimension,
            })
            if progress:
                progress((group_index + 1) / max(len(np.unique(elements)), 1), "summarizing local environments")
        return {
            "arrays": {
                "sample_indices": sample_indices,
                "coords": coords,
                "labels": categories,
                "scores": scores,
                "cluster_labels": cluster_labels,
                "elements": elements,
            },
            "preview": {
                "kind": "local_diversity",
                "categories": ["main", "distorted", "outlier"],
                "element_summary": summaries,
                "sample_count": int(x.shape[0]),
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def kernel(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        x, warnings, keep = _preprocess(samples.values, params, "standardized")
        limit = min(_int_param(params, "max_samples", 400, 2), 2_000)
        indices = _bounded_indices(x.shape[0], limit)
        if indices.size < x.shape[0]:
            warnings.append(f"kernel matrix limited to {indices.size} deterministic samples")
        x = x[indices]
        algorithm = str(params.get("kernel") or "rbf").lower()
        metrics = _safe_import("sklearn.metrics.pairwise", "scikit-learn")
        if algorithm == "linear":
            matrix = metrics.linear_kernel(x)
        elif algorithm == "cosine":
            matrix = metrics.cosine_similarity(x)
        elif algorithm == "polynomial":
            matrix = metrics.polynomial_kernel(
                x,
                degree=_int_param(params, "degree", 3, 1),
                gamma=params.get("gamma"),
                coef0=_float_param(params, "coef0", 1.0),
            )
        elif algorithm == "rbf":
            gamma = _float_param(params, "gamma", 1.0 / max(x.shape[1], 1), 0.0)
            matrix = metrics.rbf_kernel(x, gamma=gamma)
        else:
            raise AppError(ANALYSIS_INPUT_INVALID, "kernel must be linear, cosine, polynomial, or rbf")
        matrix = np.asarray(matrix, dtype=np.float64)
        centered = matrix - matrix.mean(axis=0, keepdims=True) - matrix.mean(axis=1, keepdims=True) + matrix.mean()
        eigenvalues = np.linalg.eigvalsh(centered)[::-1]
        positive = np.clip(eigenvalues, 0.0, None)
        total = float(positive.sum())
        normalized = positive / total if total > 0 else np.zeros_like(positive)
        effective_rank = float(np.exp(-np.sum(normalized[normalized > 0] * np.log(normalized[normalized > 0])))) if total > 0 else 0.0
        if progress:
            progress(1.0, "kernel analysis complete")
        return {
            "arrays": {"sample_indices": indices, "kernel_matrix": matrix, "eigenvalues": eigenvalues.astype(np.float64)},
            "preview": {
                "kind": "kernel",
                "kernel": algorithm,
                "sample_count": int(indices.size),
                "total_samples": int(samples.n_samples),
                "effective_rank": effective_rank,
                "top_eigenvalue_fraction": float(normalized[0]) if normalized.size else 0.0,
                "kernel_min": float(matrix.min()),
                "kernel_max": float(matrix.max()),
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def effective_dimension(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        x, warnings, keep = _preprocess(samples.values, params, "center")
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
        if progress:
            progress(1.0, "effective dimension complete")
        return {"arrays": {"eigenvalues": eigen.astype(np.float64), "explained_variance": normalized.astype(np.float64)}, "preview": {"kind": "effective_dimension", "participation_ratio": participation, "components_for_threshold": thresholds}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}

    @staticmethod
    def trajectory(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        frames = np.asarray(samples.frame, dtype=np.int64)
        start = _int_param(params, "frame_start", int(frames.min()) if frames.size else 0, 0)
        end = _int_param(params, "frame_end", int(frames.max()) if frames.size else start, start)
        step = _int_param(params, "frame_step", 1, 1)
        selected = np.flatnonzero((frames >= start) & (frames <= end) & (((frames - start) % step) == 0))
        if selected.size < 2:
            raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "trajectory range contains fewer than two samples")
        x, warnings, _keep = _preprocess(samples.values[selected], params, "standardized")
        deltas = np.linalg.norm(np.diff(x, axis=0), axis=1)
        step_distance = np.concatenate([[0.0], deltas])
        reference_distance = np.linalg.norm(x - x[0], axis=1)
        cumulative_distance = np.cumsum(step_distance)
        coords = _visual_pca(x)
        if params.get("timestep") is not None:
            timestep = _float_param(params, "timestep", 1.0, 0.0)
            time_axis = frames[selected].astype(np.float64) * timestep
            time_unit = str(params.get("time_unit") or "arb. units")
        else:
            time_axis = frames[selected].astype(np.float64)
            time_unit = "frame"
        time_delta = np.diff(time_axis, prepend=time_axis[0])
        speed = np.divide(step_distance, time_delta, out=np.zeros_like(step_distance), where=time_delta > 0)
        event_quantile = _float_param(params, "event_quantile", 0.99, 0.5, 1.0)
        event_threshold = float(np.quantile(step_distance[1:], event_quantile)) if step_distance.size > 1 else 0.0
        event_indices = np.flatnonzero(step_distance > event_threshold)
        if progress:
            progress(1.0, "trajectory analysis complete")
        return {"arrays": {"sample_indices": selected.astype(np.int64), "indices": selected.astype(np.int64), "frames": frames[selected], "time": time_axis, "step_distance": step_distance, "reference_distance": reference_distance.astype(np.float64), "cumulative_distance": cumulative_distance.astype(np.float64), "speed": speed.astype(np.float64), "coords": coords, "event_indices": event_indices.astype(np.int64)}, "preview": {"kind": "trajectory", "frame_start": start, "frame_end": end, "frame_step": step, "time_unit": time_unit, "total_distance": float(deltas.sum()), "max_reference_distance": float(reference_distance.max()), "event_threshold": event_threshold, "event_count": int(event_indices.size), "events": [{"frame": int(frames[selected][index]), "step_distance": float(step_distance[index])} for index in event_indices[:20].tolist()]}, "warnings": warnings}

    @staticmethod
    def drift(reference: SampleMatrix, query: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        result = AnalysisEngine.coverage(reference, query, params, progress)
        distances = result["arrays"]["distances"]
        ref, qry, drift_warnings, _keep = _preprocess_reference_query(reference.values, query.values, params, "standardized")
        limit = min(_int_param(params, "distribution_samples", 500, 20), 2_000)
        a = ref[_bounded_indices(ref.shape[0], limit)]
        b = qry[_bounded_indices(qry.shape[0], limit)]
        combined = np.vstack([a, b])
        combined_distances = _pairwise_matrix(combined, "euclidean")
        nonzero = combined_distances[combined_distances > np.finfo(np.float64).eps]
        bandwidth = _float_param(params, "bandwidth", float(np.median(nonzero)) if nonzero.size else 1.0, np.finfo(np.float64).eps)
        gamma = 1.0 / (2.0 * bandwidth * bandwidth)
        kernels = _safe_import("sklearn.metrics.pairwise", "scikit-learn")
        kxx = kernels.rbf_kernel(a, a, gamma=gamma)
        kyy = kernels.rbf_kernel(b, b, gamma=gamma)
        kxy = kernels.rbf_kernel(a, b, gamma=gamma)
        mmd2 = max(float(kxx.mean() + kyy.mean() - 2.0 * kxy.mean()), 0.0)
        centroid_distance = float(np.linalg.norm(a.mean(axis=0) - b.mean(axis=0)))
        covariance_a = np.atleast_2d(np.cov(a, rowvar=False))
        covariance_b = np.atleast_2d(np.cov(b, rowvar=False))
        covariance_shift = float(np.linalg.norm(covariance_a - covariance_b) / max(np.linalg.norm(covariance_a), 1e-15))
        result["preview"] = {**result["preview"], "kind": "drift", "mean_distance": float(distances.mean()), "median_distance": float(np.median(distances)), "max_distance": float(distances.max()), "mmd": float(np.sqrt(mmd2)), "mmd_squared": mmd2, "bandwidth": bandwidth, "centroid_distance": centroid_distance, "covariance_shift": covariance_shift}
        result["warnings"] = [*result.get("warnings", []), *drift_warnings]
        return result

    @staticmethod
    def sensitivity(runs: list[tuple[dict, SampleMatrix]], params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        if len(runs) < 2:
            raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "parameter sensitivity requires at least two completed runs")
        descriptor_names = {str(run.get("descriptor_name")) for run, _sample in runs if run.get("descriptor_name")}
        if len(descriptor_names) > 1:
            raise AppError(
                ANALYSIS_INPUT_INVALID,
                "parameter sensitivity requires the same descriptor; use Compare for different descriptors",
                {"descriptors": sorted(descriptor_names)},
            )
        rows = []
        baseline_run, baseline_sample = runs[0]
        baseline_values, baseline_warnings, _baseline_keep = _preprocess(baseline_sample.values, params, "standardized")
        for i, (run, sample) in enumerate(runs):
            if sample.n_samples != baseline_sample.n_samples or sample.sample_ids != baseline_sample.sample_ids:
                raise AppError(ANALYSIS_INPUT_INVALID, "parameter sensitivity requires aligned sample IDs")
            parameters = run.get("parameters_json") or "{}"
            try:
                parameter_value = parameters if isinstance(parameters, dict) else __import__("json").loads(parameters)
            except (TypeError, ValueError):
                parameter_value = {"raw": str(parameters)}
            values, run_warnings, _run_keep = _preprocess(sample.values, params, "standardized")
            if i == 0:
                geometry = {
                    "pairwise_distance_pearson": 1.0,
                    "pairwise_distance_spearman": 1.0,
                    "neighbor_overlap": 1.0,
                    "pca_topology_error": 0.0,
                    "clustering_stability": 1.0,
                }
            else:
                geometry, _arrays = _aligned_space_metrics(baseline_values, values, params)
            mean_delta_norm = float(np.linalg.norm(values.mean(axis=0) - baseline_values.mean(axis=0))) if values.shape[1] == baseline_values.shape[1] else None
            effective_dimension, thresholds = _effective_dimension_metrics(values)
            runtime = None
            try:
                if run.get("started_at") and run.get("finished_at"):
                    runtime = (datetime.fromisoformat(run["finished_at"]) - datetime.fromisoformat(run["started_at"])).total_seconds()
            except (TypeError, ValueError):
                runtime = None
            rows.append({
                "run_id": run["id"],
                "parameters": parameter_value,
                "feature_count": int(sample.n_features),
                "effective_dimension": effective_dimension,
                "components_95": thresholds["0.95"],
                "runtime_seconds": runtime,
                "mean_delta_norm": mean_delta_norm,
                **geometry,
                "warnings": run_warnings,
            })
            if progress:
                progress((i + 1) / len(runs), "comparing completed runs")
        return {
            "arrays": {
                "pairwise_distance_pearson": np.asarray([row["pairwise_distance_pearson"] for row in rows], dtype=np.float64),
                "neighbor_overlap": np.asarray([row["neighbor_overlap"] for row in rows], dtype=np.float64),
                "effective_dimension": np.asarray([row["effective_dimension"] for row in rows], dtype=np.float64),
            },
            "preview": {"kind": "sensitivity", "runs": rows, "baseline_run_id": baseline_run["id"]},
            "warnings": baseline_warnings,
        }
