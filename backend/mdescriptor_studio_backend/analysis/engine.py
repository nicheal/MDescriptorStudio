"""Numerical analysis engine used by :mod:`services.analysis_service`.

All calculations operate on float64 arrays.  Large arrays are returned to the
service as named artifacts rather than being serialised into an IPC response.
The methods below intentionally expose a small, stable parameter surface; the
Studio API should not become a thin and version-fragile wrapper around every
scikit-learn keyword.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
        return __import__(module, fromlist=["*"])
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
        perplexity = _float_param(params, "perplexity", 30.0, 2.0)
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
        arrays: dict[str, np.ndarray] = {"labels": labels}
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
        return {"arrays": {"scores": scores.astype(np.float64), "labels": labels}, "preview": {"kind": "outliers", "algorithm": algorithm, "outlier_count": int(labels.sum()), "contamination": contamination}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}

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
        return {"arrays": {"selected_indices": selected.astype(np.int64)}, "preview": {"kind": "sampling", "algorithm": algorithm, "selected_count": int(selected.size), "requested_count": int(target)}, "warnings": []}

    @staticmethod
    def coverage(reference: SampleMatrix, query: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        if reference.n_features != query.n_features:
            raise AppError(ANALYSIS_INPUT_INVALID, "reference and query feature counts do not match")
        ref, warnings, keep = _preprocess(reference.values, params, "raw")
        qry = _as_float64(query.values)[:, keep]
        if qry.shape[1] != ref.shape[1]:
            raise AppError(ANALYSIS_INPUT_INVALID, "reference and query feature counts do not match")
        metric = params.get("metric", "euclidean")
        if metric not in ("euclidean", "cosine"):
            raise AppError(ANALYSIS_INPUT_INVALID, "coverage metric must be euclidean or cosine")
        _check_samples(ref, 1)
        _check_samples(qry, 1)
        # Chunked nearest-reference distances: no query x reference matrix is
        # ever materialised, so a 12k-frame fixture remains bounded.
        chunk = _int_param(params, "chunk_size", 2048, 1)
        reference_chunk = _int_param(params, "reference_chunk_size", 2048, 1)
        self_dist = _nearest_distances(ref, min(_int_param(params, "k", 1, 1), max(ref.shape[0] - 1, 1)), metric)[1][:, 0] if ref.shape[0] > 1 else np.zeros(1)
        q95 = _float_param(params, "q95", float(np.quantile(self_dist, 0.95)) if self_dist.size else 0.0, 0.0)
        q99 = _float_param(params, "q99", float(np.quantile(self_dist, 0.99)) if self_dist.size else q95, 0.0)
        if q99 < q95:
            raise AppError(ANALYSIS_INPUT_INVALID, "q99 must be >= q95")
        distances = np.empty(qry.shape[0], dtype=np.float64)
        for start in range(0, qry.shape[0], chunk):
            stop = min(start + chunk, qry.shape[0])
            query_block = qry[start:stop]
            best = np.full(query_block.shape[0], np.inf, dtype=np.float64)
            qnorm = np.linalg.norm(query_block, axis=1, keepdims=True) if metric == "cosine" else None
            for ref_start in range(0, ref.shape[0], reference_chunk):
                ref_block = ref[ref_start : ref_start + reference_chunk]
                if metric == "euclidean":
                    # Algebraic form avoids a query x reference x feature
                    # temporary while keeping the same Euclidean result.
                    d2 = (
                        np.maximum(
                            np.sum(query_block * query_block, axis=1)[:, None]
                            + np.sum(ref_block * ref_block, axis=1)[None, :]
                            - 2.0 * (query_block @ ref_block.T),
                            0.0,
                        )
                    )
                    best = np.minimum(best, np.sqrt(d2).min(axis=1))
                else:
                    rnorm = np.linalg.norm(ref_block, axis=1)
                    sim = (query_block @ ref_block.T) / np.maximum(qnorm * rnorm[None, :], 1e-15)
                    best = np.minimum(best, 1.0 - sim.max(axis=1))
            distances[start:stop] = best
            if progress:
                progress(stop / max(qry.shape[0], 1), "coverage nearest-reference distances")
        labels = np.where(distances <= q95, 0, np.where(distances <= q99, 1, 2)).astype(np.int64)
        return {"arrays": {"distances": distances, "labels": labels}, "preview": {"kind": "coverage", "categories": ["covered", "marginal", "out_of_coverage"], "q95": q95, "q99": q99, "metric": metric, "covered": int((labels == 0).sum()), "marginal": int((labels == 1).sum()), "out_of_coverage": int((labels == 2).sum())}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}

    @staticmethod
    def compare(left: SampleMatrix, right: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        a = _as_float64(left.values)
        b = _as_float64(right.values)
        _check_samples(a, 2)
        _check_samples(b, 2)
        if left.n_samples != right.n_samples or left.sample_ids != right.sample_ids:
            raise AppError(
                ANALYSIS_INPUT_INVALID,
                "comparison requires aligned sample IDs",
                {"left_samples": left.n_samples, "right_samples": right.n_samples},
            )
        # Compare distributions without a default pairwise distance matrix.
        # Mean nearest-neighbour distance provides a bounded distance ranking
        # even when two descriptors have different feature dimensions.
        _, left_distances = _nearest_distances(a, 1, "euclidean") if a.shape[0] > 1 else (np.empty((1, 0), dtype=np.int64), np.zeros((1, 0)))
        _, right_distances = _nearest_distances(b, 1, "euclidean") if b.shape[0] > 1 else (np.empty((1, 0), dtype=np.int64), np.zeros((1, 0)))
        left_rank = np.argsort(np.argsort(left_distances[:, 0], kind="stable"), kind="stable").astype(np.float64)
        right_rank = np.argsort(np.argsort(right_distances[:, 0], kind="stable"), kind="stable").astype(np.float64)
        rank_correlation = float(np.corrcoef(left_rank, right_rank)[0, 1])
        result = {"left_nearest_distance": left_distances[:, 0], "right_nearest_distance": right_distances[:, 0], "left_rank": left_rank, "right_rank": right_rank}
        preview = {
            "kind": "compare",
            "left_samples": left.n_samples,
            "right_samples": right.n_samples,
            "left_feature_count": left.n_features,
            "right_feature_count": right.n_features,
            "distance_rank_correlation": rank_correlation,
            "comparison_level": "feature" if left.n_features == right.n_features else "distance/ranking",
        }
        if left.n_features == right.n_features:
            mean_delta = b.mean(axis=0) - a.mean(axis=0)
            var_delta = b.var(axis=0) - a.var(axis=0)
            result.update({"mean_delta": mean_delta, "variance_delta": var_delta})
            preview.update({"feature_count": left.n_features, "mean_absolute_delta": float(np.mean(np.abs(mean_delta))), "variance_absolute_delta": float(np.mean(np.abs(var_delta)))})
        if progress:
            progress(1.0, "comparison complete")
        return {"arrays": result, "preview": preview, "warnings": []}

    @staticmethod
    def feature_variance(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        x = _as_float64(samples.values)
        variance = x.var(axis=0)
        order = np.argsort(-variance, kind="stable")
        top_k = min(_int_param(params, "top_k", 20, 1), x.shape[1])
        if progress:
            progress(1.0, "feature variance complete")
        return {"arrays": {"variance": variance, "top_indices": order[:top_k].astype(np.int64)}, "preview": {"kind": "feature_variance", "top_k": top_k, "top_indices": order[:top_k].tolist(), "top_values": variance[order[:top_k]].tolist()}, "warnings": []}

    @staticmethod
    def feature_correlation(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        x = _as_float64(samples.values)
        _check_samples(x, 2)
        variance = x.var(axis=0)
        valid = variance > np.finfo(np.float64).eps
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
            bounded_matrix = (corr_input.T @ corr_input) / max(xv.shape[0] - 1, 1)
            feature_indices = np.flatnonzero(valid)[candidate]
            warnings.append(f"correlation heatmap limited to {max_heatmap} highest-variance features")
        else:
            bounded_matrix = (standardized.T @ standardized) / max(xv.shape[0] - 1, 1)
            feature_indices = np.flatnonzero(valid)
        np.fill_diagonal(bounded_matrix, 0.0)
        tri = np.triu_indices(bounded_matrix.shape[0], 1)
        order = np.argsort(-np.abs(bounded_matrix[tri]), kind="stable")[:top_k]
        pairs = np.column_stack([feature_indices[tri[0][order]], feature_indices[tri[1][order]]]).astype(np.int64)
        values = bounded_matrix[tri[0][order], tri[1][order]].astype(np.float64)
        arrays: dict[str, np.ndarray] = {"pairs": pairs, "correlations": values}
        arrays["correlation_matrix"] = bounded_matrix.astype(np.float64)
        if progress:
            progress(1.0, "feature correlation complete")
        return {"arrays": arrays, "preview": {"kind": "feature_correlation", "feature_count": int(xv.shape[1]), "pairs": [{"feature_a": int(a), "feature_b": int(b), "correlation": float(v)} for (a, b), v in zip(pairs.tolist(), values.tolist())]}, "warnings": warnings}

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
        thresholds = {str(t): int(np.searchsorted(cumulative, t) + 1) for t in (0.9, 0.95, 0.99)}
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
        x = samples.values[selected]
        deltas = np.linalg.norm(np.diff(x, axis=0), axis=1)
        if params.get("timestep") is not None:
            timestep = _float_param(params, "timestep", 1.0, 0.0)
            time_axis = frames[selected].astype(np.float64) * timestep
            time_unit = str(params.get("time_unit") or "arb. units")
        else:
            time_axis = frames[selected].astype(np.float64)
            time_unit = "frame"
        if progress:
            progress(1.0, "trajectory analysis complete")
        return {"arrays": {"indices": selected.astype(np.int64), "frames": frames[selected], "time": time_axis, "step_distance": np.concatenate([[0.0], deltas])}, "preview": {"kind": "trajectory", "frame_start": start, "frame_end": end, "frame_step": step, "time_unit": time_unit, "total_distance": float(deltas.sum())}, "warnings": []}

    @staticmethod
    def drift(reference: SampleMatrix, query: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        result = AnalysisEngine.coverage(reference, query, params, progress)
        distances = result["arrays"]["distances"]
        result["preview"] = {**result["preview"], "kind": "drift", "mean_distance": float(distances.mean()), "median_distance": float(np.median(distances)), "max_distance": float(distances.max())}
        return result

    @staticmethod
    def sensitivity(runs: list[tuple[dict, SampleMatrix]], params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        if len(runs) < 2:
            raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "parameter sensitivity requires at least two completed runs")
        rows = []
        base_values = runs[0][1].values
        for i, (run, sample) in enumerate(runs):
            if sample.n_features != base_values.shape[1]:
                raise AppError(ANALYSIS_INPUT_INVALID, "parameter sensitivity requires matching feature counts")
            parameters = run.get("parameters_json") or "{}"
            try:
                parameter_value = parameters if isinstance(parameters, dict) else __import__("json").loads(parameters)
            except (TypeError, ValueError):
                parameter_value = {"raw": str(parameters)}
            delta = sample.values.mean(axis=0) - base_values.mean(axis=0)
            rows.append((run["id"], parameter_value, float(np.linalg.norm(delta))))
            if progress:
                progress((i + 1) / len(runs), "comparing completed runs")
        return {"arrays": {"mean_delta_norm": np.asarray([r[2] for r in rows], dtype=np.float64)}, "preview": {"kind": "sensitivity", "runs": [{"run_id": rid, "parameters": p, "mean_delta_norm": norm} for rid, p, norm in rows], "baseline_run_id": rows[0][0]}, "warnings": []}
