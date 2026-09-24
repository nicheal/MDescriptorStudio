"""Descriptor-guided random expansion: the first (and reference) optimizer.

Every round draws seeds from the seed pool and applies a random enabled
operator to each seed several times; the engine scores the batch and selects
(novelty ranking + FPS, shared across optimizers).

This is deliberately the simplest thing that exercises the whole pipeline;
GA/PSO later only replace this module's seed/propose policy.
"""

from __future__ import annotations

import numpy as np


class RandomSearchOptimizer:
    name = "random"

    def __init__(self, operators: list, *, children_per_seed: int = 8, batch_accept: int = 8) -> None:
        if not operators:
            raise ValueError("random search requires at least one operator")
        if children_per_seed < 1 or batch_accept < 1:
            raise ValueError("children_per_seed and batch_accept must be >= 1")
        self.operators = list(operators)
        self.children_per_seed = int(children_per_seed)
        self.batch_accept = int(batch_accept)

    @property
    def batch_size(self) -> int:
        """Candidates proposed per round (drives evaluations accounting)."""
        return self.children_per_seed

    def choose_seeds(self, seed_pool: list, n_seeds: int, rng: np.random.Generator) -> list:
        n = len(seed_pool)
        if n_seeds >= n:
            return list(seed_pool)
        indices = rng.choice(n, size=int(n_seeds), replace=False)
        return [seed_pool[int(i)] for i in indices]

    def propose(self, seeds: list, rng: np.random.Generator) -> list:
        children = []
        for seed in seeds:
            for _ in range(self.children_per_seed):
                operator = self.operators[int(rng.integers(len(self.operators)))]
                children.append(operator.apply(seed, rng, operator.operator_params if hasattr(operator, "operator_params") else {}))
        return children
