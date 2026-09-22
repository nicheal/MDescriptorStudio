"""Clustering and outlier algorithms."""

from __future__ import annotations

from typing import Callable

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, ANALYSIS_INSUFFICIENT_SAMPLES, AppError
from ..models import DescriptorMatrix
from ..algorithms._common import _check_black_box_samples, _check_samples, _float_param, _int_param, _nearest_distances, _preprocess, _safe_import, _seed, _visual_pca

def cluster(samples: DescriptorMatrix, params: dict, algorithm: str, progress: Callable[[float, str], None] | None = None) -> dict:
    x, warnings, keep = _preprocess(samples.values, params, "standardized")
    algorithm = algorithm.lower()
    _check_black_box_samples(x, algorithm)
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


def outlier(samples: DescriptorMatrix, params: dict, algorithm: str, progress: Callable[[float, str], None] | None = None) -> dict:
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
        _check_black_box_samples(x, algorithm)
        _check_samples(x, 3)
        cls = _safe_import("sklearn.neighbors", "scikit-learn").LocalOutlierFactor
        model = cls(n_neighbors=min(_int_param(params, "k", 20, 2), x.shape[0] - 1), contamination=contamination)
        predicted = np.asarray(model.fit_predict(x))
        scores = -np.asarray(model.negative_outlier_factor_, dtype=np.float64)
        labels = (predicted < 0).astype(np.int64)
    elif algorithm in ("isolation_forest", "isolation-forest", "iforest"):
        _check_black_box_samples(x, "isolation_forest")
        cls = _safe_import("sklearn.ensemble", "scikit-learn").IsolationForest
        model = cls(contamination=contamination, random_state=_seed(params), n_estimators=_int_param(params, "n_estimators", 200, 10), n_jobs=1)
        model.fit(x)
        scores = -np.asarray(model.score_samples(x), dtype=np.float64)
        labels = (np.asarray(model.predict(x)) < 0).astype(np.int64)
    elif algorithm in ("mahalanobis", "mahalanobis_distance"):
        # One sample makes np.cov all-NaN, and pinv of that raises LinAlgError out
        # of the whole job; knn and lof answer with a proper error at this point.
        _check_samples(x, 2)
        center = x.mean(axis=0)
        covariance = np.atleast_2d(np.cov(x, rowvar=False))
        # The ridge is what pinv truncates, so keep the estimate and the rank
        # separate: the unregularised rank is the space the score can see.
        inv = np.linalg.pinv(covariance + np.eye(x.shape[1]) * 1e-10)
        delta = x - center
        scores = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", delta, inv, delta), 0.0))
        threshold = float(np.quantile(scores, 1.0 - (0.01 if contamination == "auto" else contamination)))
        labels = (scores > threshold).astype(np.int64)
        span = int(np.linalg.matrix_rank(covariance))
        if span < x.shape[1]:
            warnings.append(
                f"Mahalanobis scores span {span} of {x.shape[1]} directions with {x.shape[0]} samples:"
                " the rest are not measured, and a sample that is the only one occupying a direction"
                " absorbs its own variance and scores as ordinary"
            )
    else:
        raise AppError(ANALYSIS_INPUT_INVALID, f"unsupported outlier algorithm: {algorithm}")
    if progress:
        progress(1.0, f"{algorithm} complete")
    return {"arrays": {"scores": scores.astype(np.float64), "labels": labels, "coords": _visual_pca(x)}, "preview": {"kind": "outliers", "algorithm": algorithm, "outlier_count": int(labels.sum()), "contamination": contamination}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}



class Cluster:
    name = "cluster"
    category = "cluster"

    def run(self, data, params: dict, algorithm: str, progress=None) -> dict:
        return cluster(data, params, algorithm, progress)


class Outlier:
    name = "outlier"
    category = "outlier"

    def run(self, data, params: dict, algorithm: str, progress=None) -> dict:
        return outlier(data, params, algorithm, progress)
