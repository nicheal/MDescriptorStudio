"""What the engine reports back to the optimizer after each round.

This is the fitness feedback the pre-G3.5 contract lacked: without it an
optimizer can only *generate*, so a GA/PSO would either smuggle its state
into the engine or be random search wearing a costume. The engine still owns
constraints, descriptor evaluation, the objective, the shared archive and
final acceptance — the optimizer only receives results to steer the next
round's proposals.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class CandidateObservation:
    """The outcome of one proposed candidate, indexed by proposal order."""

    candidate_id: str
    generation: int
    # The proposed candidate itself (same object the optimizer returned).
    candidate: object = None
    # False when geometry constraints rejected the candidate; in that case
    # fitness is None and ``geometry_rejection`` names the first reason code.
    valid: bool = False
    accepted: bool = False
    # Order within the round's batch selection (0 = picked first); None for
    # rejected candidates. Optimizer-maintained parent pools key off this so
    # pool insertion order matches the engine's selection order exactly.
    selection_rank: int | None = None
    # None when the candidate was geometry-rejected, duplicate-filtered
    # (-inf fitness) or the objective does not produce the metric.
    fitness: float | None = None
    novelty: float | None = None
    local_diversity: float | None = None
    coverage_gain: float | None = None
    novel_environment_count: int | None = None
    geometry_rejection: str | None = None
    # Scaled structure descriptor for candidates that reached evaluation —
    # the input for optimizer-maintained descriptor-diverse parent pools.
    structure_descriptor: np.ndarray | None = None


@dataclass(frozen=True)
class ObservationBatch:
    """One round of observations, in the same order as the ProposalBatch."""

    generation: int
    observations: list = field(default_factory=list)  # list[CandidateObservation]

    def __len__(self) -> int:
        return len(self.observations)

    def accepted(self) -> list:
        return [obs for obs in self.observations if obs.accepted]

    def by_id(self, candidate_id: str):
        for obs in self.observations:
            if obs.candidate_id == candidate_id:
                return obs
        return None
