"""Analysis algorithms: kernel."""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, AppError
from ..models import DescriptorMatrix
from ._common import _bounded_indices, _float_param, _int_param, _preprocess, _safe_import

def kernel(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
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



class Kernel:
    name = "kernel"
    category = "engine"

    def validate(self, params: dict) -> None:
        return None

    def run(self, data, params: dict, progress=None) -> dict:
        return kernel(data, params, progress)
