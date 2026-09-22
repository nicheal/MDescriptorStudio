"""Analysis algorithms: tsne."""

from __future__ import annotations

from typing import Callable

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, AppError
from ..models import DescriptorMatrix
from ._common import _check_black_box_samples, _check_samples, _float_param, _int_param, _preprocess, _safe_import, _seed

MAX_ITERATIONS = 5000

def tsne(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    _check_samples(samples.values, 4)
    x, warnings, keep = _preprocess(samples.values, params, "raw")
    _check_black_box_samples(x, "tsne")
    default_perplexity = min(30.0, max(2.0, float(x.shape[0] - 1)))
    perplexity = _float_param(params, "perplexity", default_perplexity, 2.0)
    if perplexity >= x.shape[0]:
        raise AppError(ANALYSIS_INPUT_INVALID, "t-SNE perplexity must be smaller than sample count")
    if x.shape[1] < 2:
        # sklearn answers a one-column matrix with a bare ValueError, which the
        # job layer can only report as an internal failure.
        raise AppError(ANALYSIS_INPUT_INVALID, "t-SNE needs at least two features")
    # sklearn reports no progress while it optimises, so iterations are the one
    # bound on how long this job can run uncancelled.
    max_iter = _int_param(params, "max_iter", 1000, 250)
    if max_iter > MAX_ITERATIONS:
        warnings.append(f"max_iter reduced from {max_iter} to {MAX_ITERATIONS}")
        max_iter = MAX_ITERATIONS
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



class TSNE:
    name = "tsne"
    category = "engine"

    def run(self, data, params: dict, progress=None) -> dict:
        return tsne(data, params, progress)
