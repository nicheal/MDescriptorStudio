"""Descriptor-guided random expansion: the first (and reference) optimizer.

Every round draws seeds from the source pool and, optionally, a descriptor-
diverse pool of accepted structures. Accepted parents are shaken with random
atomic displacements; other parents use a random enabled operator. The engine
scores the batch and selects (novelty ranking + FPS, shared across optimizers).

This is deliberately the simplest thing that exercises the whole pipeline;
GA/PSO later only replace this module's seed/propose policy.
"""

from __future__ import annotations

import numpy as np


class RandomSearchOptimizer:
    name = "random"

    def __init__(
        self,
        operators: list,
        *,
        children_per_seed: int = 8,
        batch_accept: int = 8,
        reuse_accepted_seeds: bool = False,
    ) -> None:
        if not operators:
            raise ValueError("random search requires at least one operator")
        if children_per_seed < 1 or batch_accept < 1:
            raise ValueError("children_per_seed and batch_accept must be >= 1")
        if not isinstance(reuse_accepted_seeds, bool):
            raise ValueError("reuse_accepted_seeds must be a boolean")
        self.operators = list(operators)
        self.children_per_seed = int(children_per_seed)
        self.batch_accept = int(batch_accept)
        self.reuse_accepted_seeds = reuse_accepted_seeds
        self._displacement_operator = next(
            (operator for operator in self.operators if getattr(operator, "name", None) == "atomic_displacement"),
            None,
        )
        if self.reuse_accepted_seeds and self._displacement_operator is None:
            raise ValueError("accepted-seed feedback requires the atomic_displacement operator")
        self._feedback_seed_ids: set[int] = set()

    @property
    def batch_size(self) -> int:
        """Candidates proposed per round (drives evaluations accounting)."""
        return self.children_per_seed

    def choose_seeds(
        self,
        seed_pool: list,
        n_seeds: int,
        rng: np.random.Generator,
        *,
        feedback_pool: list | None = None,
    ) -> list:
        self._feedback_seed_ids.clear()
        if self.reuse_accepted_seeds and feedback_pool:
            n_seeds = int(n_seeds)
            feedback_count = min(len(feedback_pool), n_seeds // 2 if n_seeds > 1 else 1)
            source_count = min(len(seed_pool), n_seeds - feedback_count)
            remaining = n_seeds - feedback_count - source_count
            feedback_count += min(remaining, len(feedback_pool) - feedback_count)

            def sample(pool: list, count: int) -> list:
                if count >= len(pool):
                    return list(pool)
                indices = rng.choice(len(pool), size=count, replace=False)
                return [pool[int(index)] for index in indices]

            feedback_seeds = sample(feedback_pool, feedback_count)
            selected = feedback_seeds + sample(seed_pool, source_count)
            self._feedback_seed_ids = {id(seed) for seed in feedback_seeds}
            order = rng.permutation(len(selected))
            return [selected[int(index)] for index in order]

        n = len(seed_pool)
        if n_seeds >= n:
            return list(seed_pool)
        indices = rng.choice(n, size=int(n_seeds), replace=False)
        return [seed_pool[int(i)] for i in indices]

    def propose(self, seeds: list, rng: np.random.Generator) -> list:
        children = []
        for seed in seeds:
            for _ in range(self.children_per_seed):
                if id(seed) in self._feedback_seed_ids:
                    operator = self._displacement_operator
                else:
                    available = []
                    for candidate_operator in self.operators:
                        params = getattr(candidate_operator, "operator_params", {})
                        can_apply = getattr(candidate_operator, "can_apply", None)
                        if can_apply is None or can_apply(seed, params):
                            available.append(candidate_operator)
                    if not available:
                        continue
                    operator = available[int(rng.integers(len(available)))]
                operator_params = getattr(operator, "operator_params", {})
                children.append(operator.apply(seed, rng, operator_params))
        self._feedback_seed_ids.clear()
        return children
