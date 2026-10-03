"""Descriptor diagnostics: formal correctness, cutoff continuity, Jacobian rank,
degeneracy and distance consistency.

The diagnostics family answers "can this descriptor be trusted at all" before
the statistical suite answers "what does the data look like in it"
(review 2026-10-03, P0).  Algorithms here are engine-free: they consume numpy
matrices, duck-typed frames and recompute callables, so every one of them is
testable against toy descriptors with known analytic properties.
"""

from .consistency import distance_consistency
from .cutoff import cutoff_smoothness
from .degeneracy import degeneracy_search
from .formal import formal_invariance
from .geometry import (
    DescriptorRecompute,
    composition_counts,
    composition_distance,
    environment_distance,
    environment_fingerprint,
    minimum_image,
    pairwise_minimum_image_distances,
    permute_frame,
    random_rotation,
    reflection_matrix,
    replace_geometry,
    structure_distance,
    structure_fingerprint,
    transform_frame,
)
from .jacobian import environment_jacobian

__all__ = [
    "DescriptorRecompute",
    "composition_counts",
    "cutoff_smoothness",
    "degeneracy_search",
    "distance_consistency",
    "environment_distance",
    "environment_fingerprint",
    "environment_jacobian",
    "formal_invariance",
    "composition_distance",
    "minimum_image",
    "pairwise_minimum_image_distances",
    "permute_frame",
    "random_rotation",
    "reflection_matrix",
    "replace_geometry",
    "structure_distance",
    "structure_fingerprint",
    "transform_frame",
]
