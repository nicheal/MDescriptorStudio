"""Analysis algorithms: umap."""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, ANALYSIS_INSUFFICIENT_SAMPLES, AppError
from ..models import DescriptorMatrix
from ..umap_numpy import fit_umap
from ._common import _check_samples, _float_param, _int_param, _preprocess, _seed

def umap(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    _check_samples(samples.values, 3)
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
    coords = fit_umap(
        x,
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        metric=metric,
        seed=_seed(params),
        progress=progress,
    )
    return {
        "arrays": {"coords": coords},
        "preview": {"kind": "projection", "x_label": "UMAP-1", "y_label": "UMAP-2", "parameters": {"n_neighbors": n_neighbors, "min_dist": min_dist, "metric": metric}},
        "warnings": warnings,
        "feature_indices": np.flatnonzero(keep).astype(np.int64),
    }



class UMAP:
    name = "umap"
    category = "engine"

    def run(self, data, params: dict, progress=None) -> dict:
        return umap(data, params, progress)
