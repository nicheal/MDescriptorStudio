"""Descriptor-guided random expansion: the first (and reference) optimizer.

Every round draws seeds from the source pool and, optionally, a descriptor-
diverse pool of accepted structures. Accepted parents are shaken with random
atomic displacements; other parents use a random enabled operator. The engine
scores the batch and selects (novelty ranking + FPS, shared across optimizers).

This is deliberately the simplest thing that exercises the whole pipeline;
GA/PSO later only replace this module's proposal policy. It is also the
regression baseline for the G3.5 lifecycle refactor: with a fixed seed, the
accepted set must be numerically identical to the pre-refactor engine.
"""

from __future__ import annotations

import numpy as np

from ...analysis.sampling.fps import farthest_point_sampling
from ..optimization import ObservationBatch, OptimizationContext, ProposalBatch


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
        self._context: OptimizationContext | None = None
        # Descriptor-diverse pool of accepted parents, maintained through
        # observe() with the same FPS pruning the engine used pre-refactor.
        self._feedback_seed_pool: list = []
        self._feedback_descriptors: list[np.ndarray] = []

    @property
    def batch_size(self) -> int:
        """Candidates proposed per round (n_seeds × children_per_seed).

        Only meaningful after :meth:`initialize`; the pre-refactor property
        reported children_per_seed alone, which never matched the real round
        size and would have corrupted any evaluation accounting built on it.
        """
        n_seeds = self._context.n_seeds if self._context is not None else 0
        return int(n_seeds) * self.children_per_seed

    def initialize(self, context: OptimizationContext) -> None:
        self._context = context
        self._feedback_seed_pool = []
        self._feedback_descriptors = []

    def _require_context(self) -> OptimizationContext:
        if self._context is None:
            raise RuntimeError("optimizer.propose called before initialize()")
        return self._context

    def propose(self, *, budget: int, rng: np.random.Generator) -> ProposalBatch:
        context = self._require_context()
        feedback_pool = self._feedback_seed_pool if self.reuse_accepted_seeds else None
        seeds = self.choose_seeds(context.seed_pool, context.n_seeds, rng, feedback_pool=feedback_pool)
        children: list = []
        cap = int(budget) if budget is not None and int(budget) > 0 else None
        try:
            for seed in seeds:
                for _ in range(self.children_per_seed):
                    if cap is not None and len(children) >= cap:
                        return ProposalBatch(candidates=children)
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
        finally:
            self._feedback_seed_ids.clear()
        return ProposalBatch(candidates=children)

    def observe(self, observations: ObservationBatch) -> None:
        """Fold accepted candidates into the descriptor-diverse parent pool.

        Insertion follows the engine's selection order (selection_rank), not
        proposal order — the pool order feeds the FPS pruning below and must
        stay reproducible.
        """
        if not self.reuse_accepted_seeds or self._context is None:
            return
        accepted = [
            obs
            for obs in observations.observations
            if obs.accepted and obs.candidate is not None and obs.structure_descriptor is not None
        ]
        accepted.sort(key=lambda obs: obs.selection_rank if obs.selection_rank is not None else len(observations))
        for obs in accepted:
            self._feedback_seed_pool.append(obs.candidate)
            self._feedback_descriptors.append(np.asarray(obs.structure_descriptor, dtype=np.float64))
        limit = min(256, max(1, 4 * int(self._context.n_seeds)))
        if len(self._feedback_seed_pool) > limit:
            keep = farthest_point_sampling(np.stack(self._feedback_descriptors), n_samples=limit).indices
            self._feedback_seed_pool = [self._feedback_seed_pool[int(index)] for index in keep]
            self._feedback_descriptors = [self._feedback_descriptors[int(index)] for index in keep]

    def state_dict(self) -> dict:
        """Serializable state; the parent pool keeps only lineage ids."""
        return {
            "name": self.name,
            "children_per_seed": self.children_per_seed,
            "batch_accept": self.batch_accept,
            "reuse_accepted_seeds": self.reuse_accepted_seeds,
            "feedback_pool": [candidate.candidate_id for candidate in self._feedback_seed_pool],
        }

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
