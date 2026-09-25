"""Objective contract.

An objective scores a whole evaluated batch against the frozen archives in
one call — per-candidate scoring would re-walk the reference set for every
row. Batch results are plain arrays; the engine assembles per-candidate
CandidateEvaluation records (including the component breakdown the UI
shows: fitness / novelty / local diversity / penalty).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class ObjectiveBatchResult:
    novelty: np.ndarray | None  # structure-level per-candidate scores
    local_diversity: np.ndarray | None  # local-environment per-candidate scores
    novel_environment_count: np.ndarray | None  # per-candidate new-environment counts
    fitness: np.ndarray  # what selection ranks on (higher = better)
    components: dict = field(default_factory=dict)  # name -> per-candidate array


class GenerationObjective(Protocol):
    name: str
    needs_atomic: bool  # whether the objective reads per-atom descriptor rows
    # Whether evaluate_batch actually fills ``novel_environment_count``. The
    # engine runs the local-environment discovery-rate stop only when this is
    # true — structure-level objectives (novelty, coverage) must never be
    # terminated by a metric they do not produce.
    produces_novel_environment_count: bool

    def evaluate_batch(
        self,
        structure_values: np.ndarray,
        atomic_values: np.ndarray | None,
        row_offsets: np.ndarray | None,
        structure_archive,
        local_archive,
        penalties: np.ndarray,
    ) -> ObjectiveBatchResult:
        """Score one batch; archives stay frozen for the whole call."""
        ...
