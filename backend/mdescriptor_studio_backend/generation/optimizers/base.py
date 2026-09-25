"""Optimizer contract (G3.5 lifecycle refactor).

The optimizer owns *how* candidates are proposed; the engine owns everything
else — constraints, batch descriptor evaluation, objective scoring, and
*selection* (novelty ranking + FPS, see ``engine.select_diverse_batch``),
because selection guards the shared archive budget for every optimizer,
present and future (Generator ≠ Objective ≠ Constraint ≠ Descriptor).

The lifecycle is what makes real GA/PSO possible:

    initialize(context)     once, before round 1
    propose(budget, rng)    → ProposalBatch   each round
    observe(observations)   ← ObservationBatch  results of the round
    state_dict()            serializable optimizer state

``observe`` is the fitness feedback the pre-G3.5 contract lacked: without it
an optimizer can only generate blindly, so a "GA" would either smuggle its
population into the engine or be random search in disguise. Optimizers that
need no feedback (Random) simply ignore it.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

from ..optimization import CandidateObservation, ObservationBatch, OptimizationContext, ProposalBatch

__all__ = ["Optimizer"]


class Optimizer(Protocol):
    name: str

    def initialize(self, context: OptimizationContext) -> None:
        """Bind the fixed proposal environment; called exactly once per run."""
        ...

    def propose(self, *, budget: int, rng: np.random.Generator) -> ProposalBatch:
        """Propose one round of candidates.

        ``budget`` is how many candidates the engine can still evaluate this
        round; optimizers should not propose meaningfully more (the engine
        still enforces the evaluation cap itself).
        """
        ...

    def observe(self, observations: ObservationBatch) -> None:
        """Receive per-candidate outcomes (fitness, novelty, acceptance, ...)
        for the round just finished; steers the next round's proposals."""
        ...

    def state_dict(self) -> dict:
        """Serializable optimizer state for artifacts and reproducibility."""
        ...
