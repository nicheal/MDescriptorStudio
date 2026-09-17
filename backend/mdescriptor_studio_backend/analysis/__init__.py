"""CPU-first descriptor-space analysis primitives.

The package deliberately contains no dependency on the MDescriptor engine.
Descriptor results arrive as validated numpy arrays from the Studio result
service; this keeps the analysis layer replaceable and makes it possible to
test every algorithm with synthetic runs.
"""

from .engine import AnalysisEngine, arm_analysis_warmup_gate
from .models import (
    AtomDescriptorMatrix,
    DescriptorMatrix,
    PropertyMatrix,
    SampleMatrix,
    StructureDescriptorMatrix,
    TrajectoryDescriptorMatrix,
    validate_matrix_consistency,
)
from .registry import AnalysisRegistry, AlgorithmSpec, build_default_registry
from .sampling import FPSResult, coverage_statistics, farthest_point_sampling

__all__ = [
    "AnalysisEngine",
    "AnalysisRegistry",
    "AlgorithmSpec",
    "AtomDescriptorMatrix",
    "DescriptorMatrix",
    "FPSResult",
    "PropertyMatrix",
    "SampleMatrix",
    "StructureDescriptorMatrix",
    "TrajectoryDescriptorMatrix",
    "arm_analysis_warmup_gate",
    "coverage_statistics",
    "farthest_point_sampling",
    "validate_matrix_consistency",
]

ANALYSIS_REGISTRY = build_default_registry(AnalysisEngine)
