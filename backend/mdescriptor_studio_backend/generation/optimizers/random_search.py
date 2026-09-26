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

# Validated in the G5-2 sweep (docs/reviews/2026-09-25-g5-2-target-region.md):
# with a search target set, this share of every round's parent slots stays on
# uniformly drawn seeds so the run keeps global breadth while the rest of the
# proposals concentrate around the anchor region.
_TARGET_IMMIGRANT_SHARE = 0.15

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
        # Search-target mode (context.anchor_descriptors non-empty): accepted
        # parents retained by NEAREST-to-anchor distance (proximity is the
        # point here, unlike the diversity-pruned reuse pool above), plus the
        # proposal bookkeeping that keeps foreign candidates out.
        self._targeted_accepted: list = []
        self._targeted_descriptors: list[np.ndarray] = []
        self._targeted_pending: dict[int, object] = {}

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
        self._targeted_accepted = []
        self._targeted_descriptors = []
        self._targeted_pending = {}

    def _require_context(self) -> OptimizationContext:
        if self._context is None:
            raise RuntimeError("optimizer.propose called before initialize()")
        return self._context

    def propose(self, *, budget: int, rng: np.random.Generator) -> ProposalBatch:
        context = self._require_context()
        if self._targeting_active():
            return self._propose_targeted(budget, rng)
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

    def _targeting_active(self) -> bool:
        return bool(self._context is not None and self._context.anchor_descriptors)

    def _anchor_distance(self, descriptor: np.ndarray | None) -> float:
        """L2 distance to the nearest anchor center (inf without a descriptor)."""
        if descriptor is None:
            return float("inf")
        descriptor = np.asarray(descriptor, dtype=np.float64)
        return min(
            float(np.linalg.norm(descriptor - np.asarray(anchor, dtype=np.float64)))
            for anchor in self._context.anchor_descriptors
        )

    def _propose_targeted(self, budget: int, rng: np.random.Generator) -> ProposalBatch:
        """Search-target proposals: parents drawn by exp(-(d/r)^2) over the
        min-anchor distance (r = region radius, robust-scaled units), with a
        fixed immigrant share on uniform seeds. The mechanics are the ones
        validated in the G5-2 sweep; the engine's selection and acceptance
        are untouched. Accepted structures join the parent pool nearest-first,
        so the region fills outward one mutation shell at a time.
        """
        context = self._require_context()
        self._targeted_pending.clear()
        children: list = []
        n_slots = int(context.n_seeds)
        cap = int(budget) if budget is not None and int(budget) > 0 else None
        if cap is not None:
            cap = min(cap, n_slots * self.children_per_seed)
        seed_pool = list(context.seed_pool)
        seed_descs = list(context.seed_descriptors) or [None] * len(seed_pool)
        pool = list(zip(seed_pool, seed_descs)) + list(zip(self._targeted_accepted, self._targeted_descriptors))
        radius = context.region_radius if context.region_radius is not None else 15.0
        distances = np.asarray([self._anchor_distance(descriptor) for _, descriptor in pool], dtype=np.float64)
        weights = np.maximum(np.exp(-np.square(distances / radius)), 1e-12)
        immigrant_slots = int(round(_TARGET_IMMIGRANT_SHARE * n_slots))
        target_slots = n_slots - immigrant_slots

        def _draw_parent():
            draw = float(rng.uniform(0.0, float(weights.sum())))
            cumulative = 0.0
            for index, weight in enumerate(weights):
                cumulative += float(weight)
                if cumulative > draw:
                    return pool[index][0]
            return pool[-1][0]

        def _apply_uniform(parent):
            available = []
            for candidate_operator in self.operators:
                params = getattr(candidate_operator, "operator_params", {})
                can_apply = getattr(candidate_operator, "can_apply", None)
                if can_apply is None or can_apply(parent, params):
                    available.append(candidate_operator)
            if not available:
                return None
            operator = available[int(rng.integers(len(available)))]
            return operator.apply(parent, rng, params)

        for _ in range(target_slots):
            for _ in range(self.children_per_seed):
                if cap is not None and len(children) >= cap:
                    return ProposalBatch(candidates=children)
                parent = _draw_parent()
                child = _apply_uniform(parent)
                if child is None:
                    continue
                self._targeted_pending[id(child)] = child
                children.append(child)
        for _ in range(immigrant_slots):
            for _ in range(self.children_per_seed):
                if cap is not None and len(children) >= cap:
                    return ProposalBatch(candidates=children)
                seed = seed_pool[int(rng.integers(len(seed_pool)))]
                child = _apply_uniform(seed)
                if child is None:
                    continue
                self._targeted_pending[id(child)] = child
                children.append(child)
        return ProposalBatch(candidates=children)

    def observe(self, observations: ObservationBatch) -> None:
        """Fold accepted candidates into the descriptor-diverse parent pool.

        Insertion follows the engine's selection order (selection_rank), not
        proposal order — the pool order feeds the FPS pruning below and must
        stay reproducible.
        """
        if self._targeting_active():
            self._observe_targeted(observations)
            return
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

    def _observe_targeted(self, observations: ObservationBatch) -> None:
        """Accepted own proposals join the targeted parent pool in selection
        order; on overflow the NEAREST-to-anchor parents are retained."""
        if self._context is None:
            return
        accepted = [
            obs
            for obs in observations.observations
            if obs.accepted and obs.candidate is not None and obs.structure_descriptor is not None
        ]
        accepted.sort(key=lambda obs: obs.selection_rank if obs.selection_rank is not None else len(observations))
        for obs in accepted:
            if self._targeted_pending.pop(id(obs.candidate), None) is None:
                continue
            self._targeted_accepted.append(obs.candidate)
            self._targeted_descriptors.append(np.asarray(obs.structure_descriptor, dtype=np.float64))
        limit = min(256, max(1, 4 * int(self._context.n_seeds)))
        if len(self._targeted_accepted) > limit:
            ranked = sorted(
                zip(self._targeted_accepted, self._targeted_descriptors),
                key=lambda entry: (self._anchor_distance(entry[1]), entry[0].candidate_id),
            )
            self._targeted_accepted = [entry[0] for entry in ranked[:limit]]
            self._targeted_descriptors = [entry[1] for entry in ranked[:limit]]

    def state_dict(self) -> dict:
        """Serializable state; the parent pool keeps only lineage ids."""
        state = {
            "name": self.name,
            "children_per_seed": self.children_per_seed,
            "batch_accept": self.batch_accept,
            "reuse_accepted_seeds": self.reuse_accepted_seeds,
            "feedback_pool": [candidate.candidate_id for candidate in self._feedback_seed_pool],
        }
        if self._targeting_active():
            pool = list(zip(self._context.seed_pool, self._context.seed_descriptors)) + list(
                zip(self._targeted_accepted, self._targeted_descriptors)
            )
            distances = [self._anchor_distance(descriptor) for _, descriptor in pool]
            finite = [d for d in distances if np.isfinite(d)]
            state["targeting"] = {
                "anchors": len(self._context.anchor_descriptors),
                "region_radius": self._context.region_radius,
                "parent_pool": len(pool),
                "nearest_parent_distance": min(finite) if finite else None,
            }
        return state

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
