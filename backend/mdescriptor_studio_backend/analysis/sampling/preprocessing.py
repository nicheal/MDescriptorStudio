"""Feature scaling for descriptor-space sampling.

Euclidean geometry is meaningless on raw descriptors whose feature scales
differ by orders of magnitude: the widest feature silently dominates every
distance.  Sampling therefore runs on an explicitly scaled copy of the matrix,
and the resolved mode is always reported back so results stay reproducible.

Modes:
    raw          identity — untouched values.
    standardized (x - mean) / std.
    robust       (x - median) / (IQR / 1.349), falling back to the standard
                 deviation when the IQR degenerates and to a unit scale when
                 the feature is constant (a constant feature carries no
                 distance information either way).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..algorithms._common import _meaningful_scale

SCALING_MODES = ("raw", "standardized", "robust")

# IQR of a standard normal distribution: divides a raw IQR into sigma units.
_IQR_TO_SIGMA = 1.349


@dataclass(frozen=True)
class FeatureScaling:
    """Affine transform ``(x - center) / scale`` fitted on one feature matrix.

    Warm-start sampling fits the transform once and applies the *same* one to
    the candidate and existing matrices, so their distances stay comparable.
    """

    mode: str
    center: np.ndarray
    scale: np.ndarray


def fit_scaling(x: np.ndarray, mode: str = "robust") -> tuple[FeatureScaling, list[str]]:
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 2 or x.shape[0] == 0 or x.shape[1] == 0:
        raise ValueError("scaling input must be a non-empty 2D matrix")
    if mode not in SCALING_MODES:
        raise ValueError(f"scaling must be one of {', '.join(SCALING_MODES)}")
    if mode == "raw":
        ones = np.ones(x.shape[1], dtype=np.float64)
        return FeatureScaling("raw", np.zeros_like(ones), ones), []
    if mode == "standardized":
        center = x.mean(axis=0)
        scale = x.std(axis=0)
    else:
        quartiles = np.quantile(x, (0.25, 0.5, 0.75), axis=0)
        center = quartiles[1]
        iqr_spread = (quartiles[2] - quartiles[0]) / _IQR_TO_SIGMA
        # A degenerate IQR (more than half the samples share one value) carries
        # no reliable spread signal — the standard deviation is the fallback.
        std = x.std(axis=0)
        scale = np.where(_meaningful_scale(center, iqr_spread), iqr_spread, std)
    warnings: list[str] = []
    constant = ~_meaningful_scale(center, scale)
    if bool(constant.any()):
        warnings.append(f"{int(constant.sum())} constant feature(s) carry no distance information")
    scale = np.where(constant, 1.0, scale)
    return FeatureScaling(mode, np.asarray(center, dtype=np.float64), scale), warnings


def apply_scaling(scaling: FeatureScaling, x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 2 or x.shape[1] != scaling.center.size:
        raise ValueError(
            f"matrix width {x.shape[1] if x.ndim == 2 else 'n/a'} does not match fitted scaling width {scaling.center.size}"
        )
    return (x - scaling.center) / scaling.scale
