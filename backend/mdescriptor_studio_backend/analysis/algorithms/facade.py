"""Stable :class:`AnalysisEngine` facade over the plugin implementations.

The engine object is kept only for import compatibility (tests, older callers
and ``main`` warmup). New code should use :mod:`analysis.registry`.
"""

from __future__ import annotations

from ._common import arm_analysis_warmup_gate, warmup
from .correlation import feature_correlation, property_correlation
from .kernel import kernel
from .pairs import acquisition, compare, coverage, drift, mantel, overlap
from .pca import pca
from .sensitivity import perturbation_sensitivity, sensitivity
from .tsne import tsne
from .umap import umap
from ..clustering import cluster, outlier
from ..metrics import effective_dimension, feature_variance, local_diversity, neighbors, pairwise, similarity
from ..sampling.engine import sampling
from ..metrics import trajectory


class AnalysisEngine:
    """Pure numerical methods with deterministic defaults (compatibility shim)."""

    warmup = staticmethod(warmup)
    pca = staticmethod(pca)
    umap = staticmethod(umap)
    tsne = staticmethod(tsne)
    neighbors = staticmethod(neighbors)
    similarity = staticmethod(similarity)
    pairwise = staticmethod(pairwise)
    cluster = staticmethod(cluster)
    outlier = staticmethod(outlier)
    sampling = staticmethod(sampling)
    coverage = staticmethod(coverage)
    overlap = staticmethod(overlap)
    acquisition = staticmethod(acquisition)
    mantel = staticmethod(mantel)
    perturbation_sensitivity = staticmethod(perturbation_sensitivity)
    compare = staticmethod(compare)
    feature_variance = staticmethod(feature_variance)
    feature_correlation = staticmethod(feature_correlation)
    property_correlation = staticmethod(property_correlation)
    local_diversity = staticmethod(local_diversity)
    kernel = staticmethod(kernel)
    effective_dimension = staticmethod(effective_dimension)
    trajectory = staticmethod(trajectory)
    drift = staticmethod(drift)
    sensitivity = staticmethod(sensitivity)


__all__ = ["AnalysisEngine", "arm_analysis_warmup_gate"]
