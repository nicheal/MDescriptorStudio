"""Descriptor-guided structure generation (dataset expansion).

Sampling decides *which of the existing structures* to keep; generation
decides *which new structures* to create. The two share descriptors,
distance kernels, coverage statistics, the job service, artifact storage
and dataset lineage, but live in separate modules with separate lifecycles.

G0 lifts the shared numerical kernels out of the analysis code so FPS,
archive novelty queries and the perturbation-sensitivity runner all use
one implementation (AGENTS.md: single, clear implementation path).
"""

# Re-exported lazily (PEP 562): analysis.sampling.fps imports generation._distance,
# so this package __init__ must stay import-cycle-free until the heavy modules
# are actually needed.
_LAZY_EXPORTS = {
    "DescriptorArchive": (".archive", "DescriptorArchive"),
    "LocalEnvironmentArchive": (".archive", "LocalEnvironmentArchive"),
    "GenerationEngine": (".engine", "GenerationEngine"),
    "GenerationRunResult": (".engine", "GenerationRunResult"),
    "RoundRecord": (".engine", "RoundRecord"),
    "DescriptorEvaluation": (".evaluator", "DescriptorEvaluation"),
    "DescriptorEvaluator": (".evaluator", "DescriptorEvaluator"),
    "evaluate_batch": (".evaluator", "evaluate_batch"),
    "ArchiveEntry": (".models", "ArchiveEntry"),
    "Budget": (".models", "Budget"),
    "CandidateEvaluation": (".models", "CandidateEvaluation"),
    "ConstraintResult": (".models", "ConstraintResult"),
    "GenerationRequest": (".models", "GenerationRequest"),
    "ObjectiveResult": (".models", "ObjectiveResult"),
    "OperatorSpec": (".models", "OperatorSpec"),
    "StructureCandidate": (".models", "StructureCandidate"),
    "parse_request": (".models", "parse_request"),
    "GENERATION_ALGORITHM_VERSION": (".registry", "GENERATION_ALGORITHM_VERSION"),
    "GENERATION_REGISTRY": (".registry", "GENERATION_REGISTRY"),
}


def __getattr__(name):
    if name in _LAZY_EXPORTS:
        module_name, attr = _LAZY_EXPORTS[name]
        import importlib

        module = importlib.import_module(module_name, __package__)
        value = getattr(module, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals()) + list(_LAZY_EXPORTS))


__all__ = sorted(_LAZY_EXPORTS)
