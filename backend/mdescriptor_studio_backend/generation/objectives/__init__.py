"""Descriptor objectives: turn aligned evaluations into fitness scores.

Also home of the parse-time objective schema (:func:`validate_objective_params`)
— the constructors own the truth about their parameters, and this module
states it once so an invalid objective is rejected at submit (the A10
contract for optimizer params, applied to objectives) instead of failing
the worker after queueing.
"""

from __future__ import annotations

import math

from ...analysis.sampling.preprocessing import SCALING_MODES
from .base import GenerationObjective, ObjectiveBatchResult
from .composite import CompositeObjective
from .coverage import CoverageGainObjective
from .local_diversity import AGGREGATIONS, LocalEnvironmentNoveltyObjective
from .novelty import NoveltyObjective

__all__ = [
    "AGGREGATIONS",
    "CompositeObjective",
    "CoverageGainObjective",
    "GenerationObjective",
    "LocalEnvironmentNoveltyObjective",
    "NoveltyObjective",
    "ObjectiveBatchResult",
    "OBJECTIVE_PARAM_KEYS",
    "SCALING_MODES",
    "validate_objective_params",
]

# Per-type tunable payload keys. ``type`` and ``scaling`` are accepted for
# every objective: ``scaling`` selects the archive scaling mode and is
# consumed by the service, never by the constructors (registry.build_objective
# strips both before construction).
OBJECTIVE_PARAM_KEYS = {
    "novelty": frozenset(),
    "coverage": frozenset(),
    "local_environment_novelty": frozenset({"aggregation", "top_fraction", "quantile", "novelty_threshold"}),
    "composite": frozenset({"aggregation", "top_fraction", "quantile", "novelty_threshold", "structure_weight", "local_weight"}),
}


def _number(value) -> float | None:
    """Strict float: booleans and non-numbers are rejected, None passes."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def validate_objective_params(objective: dict) -> None:
    """Parse-time objective schema.

    Validates the per-type key set and the value ranges the constructors
    enforce, with constructor-consistent wording. Raises ``ValueError``;
    the RPC boundary maps it to ``AppError(INVALID_PARAMS, ...)``.

    Backward-compat note: stored ``params_json`` from completed runs only
    ever carried these keys with valid values (the constructors already
    rejected everything else at build time), so the resume path re-parses
    cleanly.
    """
    otype = str(objective["type"])
    allowed = OBJECTIVE_PARAM_KEYS.get(otype)
    if allowed is None:
        raise ValueError(f"unknown generation objective: {otype}")
    unknown = set(objective) - allowed - {"type", "scaling"}
    if unknown:
        raise ValueError(f"unknown objective params for '{otype}': {', '.join(sorted(unknown))}")

    scaling = objective.get("scaling")
    if scaling is not None and (not isinstance(scaling, str) or scaling not in SCALING_MODES):
        raise ValueError(f"objective scaling must be one of {', '.join(SCALING_MODES)}")

    if "aggregation" in objective:
        value = objective["aggregation"]
        if not isinstance(value, str) or value not in AGGREGATIONS:
            raise ValueError(f"objective 'aggregation' must be one of {', '.join(AGGREGATIONS)}")

    if "top_fraction" in objective:
        value = _number(objective["top_fraction"])
        if value is None or not 0.0 < value <= 1.0:
            raise ValueError("objective 'top_fraction' must be a number in (0, 1]")

    if "quantile" in objective:
        value = _number(objective["quantile"])
        if value is None or not 0.0 < value <= 1.0:
            raise ValueError("objective 'quantile' must be a number in (0, 1]")

    if "novelty_threshold" in objective:
        value = _number(objective["novelty_threshold"])
        if objective["novelty_threshold"] is not None and (value is None or not math.isfinite(value) or value <= 0):
            raise ValueError("objective 'novelty_threshold' must be a positive finite number or null")

    if otype == "composite":
        weights: list[float] = []
        for key in ("structure_weight", "local_weight"):
            if key in objective:
                value = _number(objective[key])
                if value is None or value < 0:
                    raise ValueError(f"objective '{key}' must be a non-negative number")
                weights.append(value)
        # Absent keys fall back to the constructor defaults (0.3/0.7), whose
        # sum is positive — only two explicit zeros can produce a dead sum.
        if len(weights) == 2 and sum(weights) <= 0:
            raise ValueError("objective composite weights must be non-negative with a positive sum")
