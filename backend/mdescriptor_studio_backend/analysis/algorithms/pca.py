"""Analysis algorithms: pca."""

from __future__ import annotations

from typing import Callable

import numpy as np
from ..models import DescriptorMatrix
from ._common import _check_samples, _preprocess

def pca(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
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



class PCA:
    name = "pca"
    category = "engine"

    def run(self, data, params: dict, progress=None) -> dict:
        return pca(data, params, progress)
