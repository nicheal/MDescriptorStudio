"""Optimizer contract.

The optimizer owns *how* candidates are proposed (choose_seeds + propose);
the engine owns everything else — constraints, batch descriptor evaluation,
objective scoring, and *selection* (novelty ranking + FPS, see
``GenerationEngine._select_diverse``), because selection guards the shared
archive budget for every optimizer, present and future. GA/PSO later only
replace the proposal policy — constraints, objectives and the evaluator stay
untouched (Generator ≠ Objective ≠ Constraint ≠ Descriptor).
"""

from __future__ import annotations

from typing import Protocol

import numpy as np


class Optimizer(Protocol):
    name: str

    @property
    def batch_size(self) -> int:
        """Candidates proposed per seed-round (drives evaluations accounting)."""
        ...

    def choose_seeds(
        self,
        seed_pool: list,
        n_seeds: int,
        rng: np.random.Generator,
        *,
        feedback_pool: list | None = None,
    ) -> list:
        """Pick parent candidates for the next round."""
        ...

    def propose(self, seeds: list, rng: np.random.Generator) -> list:
        """Generate candidate children from the chosen seeds."""
        ...
