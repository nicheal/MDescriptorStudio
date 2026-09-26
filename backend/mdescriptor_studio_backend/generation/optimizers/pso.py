"""Particle-swarm optimizer with per-slot memory (G5-1), after USPEX-PSO.

Faithful mapping of the USPEX 9.4.4 PSO mechanics
(``docs/reviews/2026-09-25-uspex-pso-analysis.md``) onto the G3.5 optimizer
contract, minus crossover: USPEX-PSO has no velocity vector — the "velocity"
is a probability mass over move targets, scaled by the descriptor distance
to each memory target. Per particle slot:

* **pbest** — the slot's best position ever, and **gbest** — the best pbest;
* each round, a non-immigrant child picks its move target by competing
  masses ``P_p = U·w_pbest·d(pos, pbest)``, ``P_g = U·w_gbest·d(pos, gbest)``,
  ``P_m = U·w_mut`` — i.e. the farther the particle sits from a memory
  target, the stronger the pull to propose *around* that target (mutate it);
  with no pull it mutates its own current position (USPEX's soft-mutation
  mass, remapped to plain mutation since we have no force constants);
* a fixed immigrant share resets slots to fresh seed draws (USPEX's
  ``fracRand`` + its failed-particle reset).

Deliberate deviations from USPEX, forced by our fitness semantics:

* **Memory comparisons run on per-round standardized fitness** (z-score over
  the round's finite fitness values). USPEX compares enthalpies — a physical,
  time-invariant quantity — while our fitness is novelty-shaped and decays as
  the archive grows, so raw cross-round comparisons would systematically
  favour early structures. A z-score asks "how exceptional was this child in
  its own round?", which stays comparable as the archive saturates.

No genome/mask machinery: operators apply at their user-configured params
and are drawn uniformly among those applicable — memory, not amplitude
adaptation, is the mechanism under test here.

Prior expectation (stated before measuring): on the discovery metrics this
concentrates proposals harder than the genetic optimizer, which already
loses to plain random — the sweep exists to close the roadmap item with
data, not to find a winner.
"""

from __future__ import annotations

import numpy as np

from ..optimization import ObservationBatch, OptimizationContext, ProposalBatch

_APPLY_ERRORS = (ValueError, ZeroDivisionError, np.linalg.LinAlgError)


class _Particle:
    """One slot's memory: current position plus best-ever position."""

    __slots__ = ("position", "position_desc", "pbest", "pbest_desc", "pbest_z")

    def __init__(self) -> None:
        self.position: object | None = None
        self.position_desc: np.ndarray | None = None
        self.pbest: object | None = None
        self.pbest_desc: np.ndarray | None = None
        self.pbest_z: float | None = None


class PSOOptimizer:
    name = "pso"

    def __init__(
        self,
        operators: list,
        *,
        children_per_seed: int = 8,
        batch_accept: int = 8,
        pso_weight_pbest: float = 1.0,
        pso_weight_gbest: float = 1.5,
        pso_weight_mut: float = 0.5,
        pso_weight_anchor: float = 1.5,
        immigrant_fraction: float = 0.15,
    ) -> None:
        if not operators:
            raise ValueError("pso optimizer requires at least one operator")
        if children_per_seed < 1 or batch_accept < 1:
            raise ValueError("children_per_seed and batch_accept must be >= 1")
        for weight, label in (
            (pso_weight_pbest, "pso_weight_pbest"),
            (pso_weight_gbest, "pso_weight_gbest"),
            (pso_weight_mut, "pso_weight_mut"),
            (pso_weight_anchor, "pso_weight_anchor"),
        ):
            if float(weight) < 0.0:
                raise ValueError(f"{label} must be >= 0")
        if float(pso_weight_pbest) + float(pso_weight_gbest) + float(pso_weight_mut) <= 0.0:
            raise ValueError("at least one of the pso weights must be positive")
        self.pso_weight_anchor = float(pso_weight_anchor)
        if not 0.0 <= float(immigrant_fraction) <= 0.9:
            raise ValueError("immigrant_fraction must be in [0.0, 0.9]")
        self.operators = list(operators)
        self.children_per_seed = int(children_per_seed)
        self.batch_accept = int(batch_accept)
        self.pso_weight_pbest = float(pso_weight_pbest)
        self.pso_weight_gbest = float(pso_weight_gbest)
        self.pso_weight_mut = float(pso_weight_mut)
        self.immigrant_fraction = float(immigrant_fraction)
        self._context: OptimizationContext | None = None
        self._particles: list[_Particle] = []
        # Proposal bookkeeping: id(candidate) → (slot, operator name). The
        # engine hands back the very objects propose returned, so identity is
        # exact; the value keeps the candidate alive so ids cannot recycle.
        self._pending: dict[int, tuple[int, str]] = {}
        self._rounds = 0

    # -- memory --------------------------------------------------------------

    def _gbest(self) -> _Particle | None:
        """The particle holding the best pbest (None until any memory exists)."""
        best = None
        for particle in self._particles:
            if particle.pbest_z is None:
                continue
            if best is None or particle.pbest_z > best.pbest_z:
                best = particle
        return best

    @staticmethod
    def _distance(desc_a: np.ndarray | None, desc_b: np.ndarray | None) -> float:
        """L2 distance in the scaled descriptor space (the engine's metric)."""
        if desc_a is None or desc_b is None:
            return 0.0
        return float(np.linalg.norm(np.asarray(desc_a, dtype=np.float64) - np.asarray(desc_b, dtype=np.float64)))

    def _nearest_anchor_structure(self):
        """The seed-pool structure closest to the anchor region (the
        search-target pull target). Anchors are forced into the seed pool
        by the worker, so this is the anchor frame itself."""
        if self._context is None or not self._context.anchor_descriptors:
            return None
        best, best_d = None, float("inf")
        for candidate, descriptor in zip(
            self._context.seed_pool,
            self._context.seed_descriptors or [None] * len(self._context.seed_pool),
        ):
            if descriptor is None:
                continue
            d = self._distance(
                np.asarray(descriptor, dtype=np.float64),
                min(
                    (np.asarray(a, dtype=np.float64) for a in self._context.anchor_descriptors),
                    key=lambda a: float(np.linalg.norm(np.asarray(descriptor, dtype=np.float64) - a)),
                ),
            )
            if d < best_d:
                best, best_d = candidate, d
        return best

    def _move_target(self, particle: _Particle, rng: np.random.Generator):
        """Competing pull masses (USPEX PSO.m L100–115), without crossover.

        The target structure is what the operator mutates: pulling toward a
        memory target means proposing around the remembered structure. With
        a search target set, a fourth mass pulls toward the structure
        nearest the anchor region; every other mass is unchanged.
        """
        gbest = self._gbest()
        anchor_structure = self._nearest_anchor_structure()
        anchor_desc = None
        if anchor_structure is not None and self._context is not None and self._context.seed_descriptors:
            for candidate, descriptor in zip(
                self._context.seed_pool,
                self._context.seed_descriptors,
            ):
                if candidate is anchor_structure:
                    anchor_desc = np.asarray(descriptor, dtype=np.float64)
                    break
        p_pull = rng.random() * self.pso_weight_pbest * self._distance(particle.position_desc, particle.pbest_desc)
        g_pull = rng.random() * self.pso_weight_gbest * self._distance(particle.position_desc, gbest.pbest_desc if gbest else None)
        m_pull = rng.random() * self.pso_weight_mut
        a_pull = (
            rng.random()
            * self.pso_weight_anchor
            * self._distance(particle.position_desc, anchor_desc)
            if anchor_structure is not None
            else 0.0
        )
        if a_pull >= p_pull and a_pull >= g_pull and a_pull >= m_pull and anchor_structure is not None:
            return anchor_structure
        if g_pull >= p_pull and g_pull >= m_pull and gbest is not None:
            return gbest.pbest
        if p_pull >= m_pull and particle.pbest is not None:
            return particle.pbest
        return particle.position

    # -- lifecycle -----------------------------------------------------------

    @property
    def batch_size(self) -> int:
        n_seeds = self._context.n_seeds if self._context is not None else 0
        return int(n_seeds) * self.children_per_seed

    def initialize(self, context: OptimizationContext) -> None:
        self._context = context
        self._particles = [_Particle() for _ in range(max(1, int(context.n_seeds)))]
        self._pending = {}
        self._rounds = 0

    def _require_context(self) -> OptimizationContext:
        if self._context is None:
            raise RuntimeError("optimizer.propose called before initialize()")
        return self._context

    def _apply(self, parent, rng):
        """Uniform operator choice among those applicable, at configured params."""
        available = []
        for operator in self.operators:
            can_apply = getattr(operator, "can_apply", None)
            if can_apply is not None and not can_apply(parent, getattr(operator, "operator_params", {}) or {}):
                continue
            available.append(operator)
        if not available:
            return None
        operator = available[int(rng.integers(len(available)))]
        return operator, operator.apply(parent, rng, getattr(operator, "operator_params", {}) or {})

    def propose(self, *, budget: int, rng: np.random.Generator) -> ProposalBatch:
        context = self._require_context()
        self._rounds += 1
        self._pending.clear()
        children: list = []
        n_slots = len(self._particles)
        cap = int(budget) if budget is not None and int(budget) > 0 else None
        if cap is not None:
            cap = min(cap, n_slots * self.children_per_seed)
        gbest = self._gbest()
        seed_pool = context.seed_pool
        for slot, particle in enumerate(self._particles):
            for _ in range(self.children_per_seed):
                if cap is not None and len(children) >= cap:
                    return ProposalBatch(candidates=children)
                # Immigrant reset (USPEX checks the reset branch first), and
                # round 1 has no memory yet, so it is all immigrants — the
                # first round matches Random's distribution exactly.
                if gbest is None or particle.pbest is None or rng.random() < self.immigrant_fraction:
                    parent = seed_pool[int(rng.integers(len(seed_pool)))]
                else:
                    parent = self._move_target(particle, rng)
                applied = self._apply(parent, rng)
                if applied is None:
                    continue
                operator, candidate = applied
                self._pending[id(candidate)] = (slot, operator.name)
                children.append(candidate)
        return ProposalBatch(candidates=children)

    def observe(self, observations: ObservationBatch) -> None:
        """Update per-slot positions and pbest memory from round outcomes.

        The slot's new position is its best (most exceptional) evaluated
        child of the round; pbest updates when that position's z-score beats
        the stored one. All comparisons live in z-space (see module docstring).
        """
        if self._context is None:
            return
        # Per-round standardization over every evaluated candidate.
        finite = [obs.fitness for obs in observations.observations if obs.fitness is not None and np.isfinite(obs.fitness)]
        if not finite:
            return
        values = np.asarray(finite, dtype=np.float64)
        mean, std = float(values.mean()), float(values.std())
        if std <= 0.0:
            std = 1.0

        slot_best: dict[int, tuple[float, object, np.ndarray]] = {}
        for obs in observations.observations:
            if obs.fitness is None or not np.isfinite(obs.fitness) or obs.candidate is None or obs.structure_descriptor is None:
                continue
            pending = self._pending.get(id(obs.candidate))
            if pending is None:
                continue
            slot = pending[0]
            z = (float(obs.fitness) - mean) / std
            if slot not in slot_best or z > slot_best[slot][0]:
                slot_best[slot] = (z, obs.candidate, np.asarray(obs.structure_descriptor, dtype=np.float64))
        for slot, (z, candidate, desc) in slot_best.items():
            particle = self._particles[slot]
            particle.position = candidate
            particle.position_desc = desc
            if particle.pbest_z is None or z > particle.pbest_z:
                particle.pbest = candidate
                particle.pbest_desc = desc
                particle.pbest_z = z

    def state_dict(self) -> dict:
        return {
            "name": self.name,
            "children_per_seed": self.children_per_seed,
            "batch_accept": self.batch_accept,
            "pso_weight_pbest": self.pso_weight_pbest,
            "pso_weight_gbest": self.pso_weight_gbest,
            "pso_weight_mut": self.pso_weight_mut,
            "pso_weight_anchor": self.pso_weight_anchor,
            "immigrant_fraction": self.immigrant_fraction,
            "rounds": self._rounds,
            "particles": [
                {
                    "position": particle.position.candidate_id if particle.position is not None else None,
                    "pbest": particle.pbest.candidate_id if particle.pbest is not None else None,
                    "pbest_z": particle.pbest_z,
                }
                for particle in self._particles
            ],
        }
