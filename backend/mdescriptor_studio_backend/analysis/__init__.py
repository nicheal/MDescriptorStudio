"""CPU-first descriptor-space analysis primitives.

The package deliberately contains no dependency on the MDescriptor engine.
Descriptor results arrive as validated numpy arrays from the Studio result
service; this keeps the analysis layer replaceable and makes it possible to
test every algorithm with synthetic runs.
"""

from .engine import AnalysisEngine, SampleMatrix, arm_analysis_warmup_gate

__all__ = ["AnalysisEngine", "SampleMatrix", "arm_analysis_warmup_gate"]
