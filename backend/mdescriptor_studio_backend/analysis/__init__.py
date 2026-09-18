"""CPU-first descriptor-space analysis primitives.

The package deliberately contains no dependency on the MDescriptor engine.
Descriptor results arrive as validated numpy arrays from the Studio result
service; this keeps the analysis layer replaceable and makes it possible to
test every algorithm with synthetic runs.
"""

from .algorithms import arm_analysis_warmup_gate, warmup
from .models import (
    AtomDescriptorMatrix,
    DescriptorMatrix,
    StructureDescriptorMatrix,
    validate_matrix_consistency,
)
from .registry import AnalysisRegistry, AlgorithmSpec, build_default_registry
from .sampling import FPSResult, coverage_statistics, farthest_point_sampling

__all__ = [
    "AnalysisRegistry",
    "AlgorithmSpec",
    "AtomDescriptorMatrix",
    "DescriptorMatrix",
    "FPSResult",
    "StructureDescriptorMatrix",
    "arm_analysis_warmup_gate",
    "coverage_statistics",
    "farthest_point_sampling",
    "validate_matrix_consistency",
    "warmup",
]

ANALYSIS_REGISTRY = build_default_registry()
