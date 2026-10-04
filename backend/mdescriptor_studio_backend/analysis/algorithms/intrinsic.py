"""TwoNN intrinsic dimension (Facco et al., Sci. Rep. 2017).

The second layer of descriptor diagnostics (review 2026-10-03 §3): the
spectral effective dimension (participation ratio) measures how variance
spreads over PCA components; it does NOT measure the dimension of the manifold
the samples actually lie on, and a descriptor with 1000 features can carry a
manifold of intrinsic dimension ~12.  TwoNN estimates that intrinsic dimension
from the ratio of the first two nearest-neighbor distances only:

    mu_i = r_2(i) / r_1(i) (>= 1) is Pareto-distributed with shape d under
    homogeneous Poisson sampling, so the likelihood maximum gives

    d_hat = 1 / mean_i ln(mu_i).

It is metric-sensitive by construction (a per-feature rescaling changes the
geometry), so the preprocess default matches the rest of the suite
("standardized") and stays an explicit part of the space definition.  Exact
duplicate rows (r_1 = 0) carry no logarithm and are excluded with a warning;
the bootstrap over per-point log-ratios gives a spread for the estimate.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, ANALYSIS_INSUFFICIENT_SAMPLES, AppError
from ._common import _bounded_indices, _check_samples, _float_param, _int_param, _nearest_distances, _preprocess
from ..models import DescriptorMatrix


def two_nn_intrinsic_dimension(samples: DescriptorMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    """Estimate the intrinsic manifold dimension from two-NN distance ratios."""
    nn_params = dict(params or {})
    preprocess = nn_params.get("preprocess")
    if preprocess is None or preprocess == "":
        preprocess = "standardized"
        nn_params["preprocess"] = preprocess
    x, warnings, keep = _preprocess(samples.values, nn_params, "standardized", allow_empty=True)
    _check_samples(x, 4)
    if x.shape[1] == 0:
        raise AppError(ANALYSIS_INPUT_INVALID, "all features are constant; TwoNN has no metric to measure")
    metric = str(nn_params.get("metric") or "euclidean")
    if metric not in ("euclidean", "cosine", "manhattan"):
        raise AppError(ANALYSIS_INPUT_INVALID, "TwoNN metric must be euclidean, cosine, or manhattan")
    limit = min(_int_param(nn_params, "max_samples", 5000, 4), 20_000)
    n_bootstrap = min(_int_param(nn_params, "n_bootstrap", 32, 0), 128)
    seed = _int_param(nn_params, "seed", 42, 0)
    scale = _float_param(nn_params, "duplicate_tolerance", 1e-9, 1e-15, 1e-3)

    indices = _bounded_indices(x.shape[0], limit)
    x = x[indices]
    warnings = list(warnings)
    if indices.size < samples.n_samples:
        warnings.append(f"sampled {int(indices.size)} of {samples.n_samples} rows evenly across the run")

    neighbor_indices, distances = _nearest_distances(x, 2, metric)
    r1 = distances[:, 0]
    r2 = distances[:, 1]
    # A duplicate row has r_1 = 0 (the neighbor search excludes self by index,
    # so an exact copy surfaces as a zero distance).  ln(r_2/r_1) is undefined
    # there and the point carries no metric information about the manifold.
    duplicated = r1 <= scale * max(float(r2.max()), 1e-300)
    valid = ~duplicated
    n_duplicates = int(np.count_nonzero(duplicated))
    if n_duplicates:
        warnings.append(f"excluded {n_duplicates} duplicate row(s) (zero first-neighbor distance)")
    if int(np.count_nonzero(valid)) < 4:
        raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "TwoNN needs at least four distinct rows")
    # Facco et al. 2017: under a homogeneous Poisson sampling of a d-dimensional
    # manifold, the two-NN ratio mu = r2/r1 (>= 1) is Pareto-distributed with
    # shape d (survival mu^-d), so the likelihood maximum is
    #   d_hat = 1 / mean_i ln(mu_i).
    # (Verified against uniform clouds in 1/2/3/8 dimensions before shipping.)
    ln_mu = np.log(r2[valid] / r1[valid])
    mean_ln = float(ln_mu.mean())
    if mean_ln <= 0:
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "TwoNN is degenerate on this set: ln(r2/r1) is ~0 (neighbors at zero distance)",
        )
    d_mle = 1.0 / mean_ln

    bootstrap = None
    bootstrap_estimates = np.empty(0, dtype=np.float64)
    if n_bootstrap > 0:
        rng = np.random.default_rng(seed)
        n = ln_mu.size
        draws = np.empty(n_bootstrap, dtype=np.float64)
        for b in range(n_bootstrap):
            sample = ln_mu[rng.integers(0, n, size=n)]
            mean_b = float(sample.mean())
            draws[b] = 1.0 / mean_b if mean_b > 0 else np.nan
        finite = draws[np.isfinite(draws)]
        bootstrap_estimates = draws
        if finite.size:
            bootstrap = {
                "mean": float(finite.mean()),
                "sd": float(finite.std(ddof=1)) if finite.size > 1 else 0.0,
                "ci95": [float(np.quantile(finite, 0.025)), float(np.quantile(finite, 0.975))],
                "draws": int(finite.size),
            }
        if progress:
            progress(0.9, "bootstrap complete")

    if progress:
        progress(1.0, "TwoNN complete")
    return {
        "arrays": {
            "ln_mu": ln_mu.astype(np.float64),
            "mu": (r2[valid] / r1[valid]).astype(np.float64),
            "bootstrap_estimates": bootstrap_estimates,
        },
        "preview": {
            "kind": "two_nn_intrinsic_dimension",
            "intrinsic_dimension": d_mle,
            "bootstrap": bootstrap,
            "points_used": int(ln_mu.size),
            "points_total": int(x.shape[0]),
            "duplicates_excluded": n_duplicates,
            "preprocess": preprocess,
            "metric": metric,
            "sample_count": int(samples.n_samples),
            "feature_count": int(samples.n_features),
            "pca_feature_count": int(x.shape[1]),
            "estimator": "two_nn_mle (Facco et al. 2017): d = 1 / mean(ln r2/r1)",
            "note": (
                "Intrinsic manifold dimension, not the PCA participation ratio: a 1000-feature "
                "descriptor with d_int ~ 12 is strongly redundant without implying a lossless 12-dim projection."
            ),
        },
        "warnings": warnings,
        "feature_indices": np.flatnonzero(keep).astype(np.int64),
    }


class TwoNNIntrinsicDimension:
    name = "two_nn_intrinsic_dimension"
    category = "engine"

    def run(self, data, params: dict, progress=None) -> dict:
        return two_nn_intrinsic_dimension(data, params, progress)
