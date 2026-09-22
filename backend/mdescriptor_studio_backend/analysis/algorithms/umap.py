"""Analysis algorithms: umap."""

from __future__ import annotations

from typing import Callable

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, ANALYSIS_INSUFFICIENT_SAMPLES, AppError
from ..models import DescriptorMatrix
from ..umap_numpy import fit_umap
from ._common import _bounded_indices, _check_samples, _float_param, _int_param, _preprocess, _seed

UMAP_MAX_SAMPLES = 10_000

def umap(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    _check_samples(samples.values, 3)
    x, warnings, keep = _preprocess(samples.values, params, "raw")
    sample_indices = _bounded_indices(x.shape[0], UMAP_MAX_SAMPLES)
    if sample_indices.size < x.shape[0]:
        warnings.append(
            f"UMAP sampled {int(sample_indices.size)} of {int(x.shape[0])} samples"
            " to bound the neighbour search"
        )
    fit_x = x[sample_indices]
    if fit_x.shape[0] > 2_048:
        warnings.append(
            "UMAP used an exact chunked neighbour search for this selection"
        )
    n_neighbors = _int_param(params, "n_neighbors", 15, 2)
    if n_neighbors >= fit_x.shape[0]:
        n_neighbors = fit_x.shape[0] - 1
    if n_neighbors < 2:
        raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "UMAP needs at least 3 samples")
    min_dist = _float_param(params, "min_dist", 0.1, 0.0, 1.0)
    metric = str(params.get("metric") or "euclidean")
    if metric not in ("euclidean", "cosine", "manhattan"):
        raise AppError(ANALYSIS_INPUT_INVALID, "UMAP metric must be euclidean, cosine, or manhattan")
    coords = fit_umap(
        fit_x,
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        metric=metric,
        seed=_seed(params),
        progress=progress,
    )
    return {
        "arrays": {"coords": coords},
        "preview": {
            "kind": "projection",
            "x_label": "UMAP-1",
            "y_label": "UMAP-2",
            "parameters": {"n_neighbors": n_neighbors, "min_dist": min_dist, "metric": metric},
            "total_samples": int(x.shape[0]),
            "sampled_samples": int(sample_indices.size),
        },
        "sample_indices": sample_indices,
        "warnings": warnings,
        "feature_indices": np.flatnonzero(keep).astype(np.int64),
    }



class UMAP:
    name = "umap"
    category = "engine"

    def run(self, data, params: dict, progress=None) -> dict:
        return umap(data, params, progress)
