"""Stable public facade for the modular analysis algorithms.

The implementation lives under :mod:`analysis.algorithms`, with metrics,
clustering and sampling split into their own packages. Keeping the
``AnalysisEngine`` name here preserves the import path used by the backend,
tests and older clients.
"""

from .algorithms.facade import AnalysisEngine, arm_analysis_warmup_gate
from .models import (
    AtomDescriptorMatrix,
    DescriptorMatrix,
    PropertyMatrix,
    SampleMatrix,
    StructureDescriptorMatrix,
    TrajectoryDescriptorMatrix,
    validate_matrix_consistency,
)

__all__ = [
    "AnalysisEngine",
    "AtomDescriptorMatrix",
    "DescriptorMatrix",
    "PropertyMatrix",
    "SampleMatrix",
    "StructureDescriptorMatrix",
    "TrajectoryDescriptorMatrix",
    "arm_analysis_warmup_gate",
    "validate_matrix_consistency",
]
