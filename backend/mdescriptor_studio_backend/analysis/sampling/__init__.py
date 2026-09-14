"""Representative-structure sampling on plain descriptor matrices.

This layer sits *above* the descriptor engine and *below* the GUI: it accepts
2-D feature matrices only and never imports ASE, a descriptor implementation,
or the Studio services.  Every descriptor therefore shares one sampling
framework (descriptor engine → feature matrix → analysis.sampling).
"""

from .blocks import CompositeSpace, FeatureBlock, combine_feature_blocks
from .fps import FPSResult, GroupedFPSResult, coverage_statistics, farthest_point_sampling, grouped_farthest_point_sampling
from .grouping import group_sizes, sqrt_quota, validate_group_labels
from .preprocessing import SCALING_MODES, FeatureScaling, apply_scaling, fit_scaling

__all__ = [
    "CompositeSpace",
    "FPSResult",
    "FeatureBlock",
    "FeatureScaling",
    "GroupedFPSResult",
    "SCALING_MODES",
    "apply_scaling",
    "combine_feature_blocks",
    "coverage_statistics",
    "farthest_point_sampling",
    "fit_scaling",
    "group_sizes",
    "grouped_farthest_point_sampling",
    "sqrt_quota",
    "validate_group_labels",
]
