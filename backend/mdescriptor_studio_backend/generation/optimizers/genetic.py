"""Mutation-only genetic algorithm, modelled on USPEX 9.4.4 (G4-1).

Design follows the USPEX source analysis
(``docs/reviews/2026-09-25-uspex-ga-analysis.md``); the parts deliberately
not carried over: heredity (crossover targets ground-state search, not
environment coverage), soft-mode mutation (needs second-order force
constants), and anti-seeds (the novelty objective already encodes the same
repulsion semantics).

What survives, mapped onto the G3.5 optimizer contract:

* **Square-cumulative rank roulette** (USPEX ``update_STUFF`` tournament):
  parents are drawn only from the accepted pool, with Σk² ticket counts so
  selection pressure decays cubically with rank distance — and never touches
  fitness *values*, which is what makes it safe under the mixed-metric
  fitnesses (novelty/coverage/composite) of gen-4.
* **Continuous amplitude genes**: one genome per pool entry holds the
  displacement σ, strain and shear amplitude caps plus a per-operator mask
  weight. A child is born from its parent's genome mutated (clipped), so
  the amplitudes the *engine accepts* breed into the next round — the
  selection-driven analogue of USPEX's σ contraction on retry. Genes are
  amplitude caps (``max_sigma``/``max_strain``/``max_shear``) with the same
  distribution-bound semantics the operators already implement.
* **Random immigrants** (USPEX ``howManyRand``): a fixed fraction of every
  round's parent slots stays on the seed pool so the run cannot collapse
  onto the accepted set.
* **Operator degradation** (USPEX's retry → Random fallback): a child whose
  operator choice keeps failing degrades to plain displacement at the
  genome σ, and a slot whose operator cannot apply at all is skipped —
  ``propose`` never fabricates work the operator family cannot produce.

G4-1.5 machinery (warm-start flat roulette, annealed immigrants, AutoFrac
mask multipliers) is available as constructor knobs but OFF by default: the
20-seed re-benchmark measured the combination as a net regression at full
budget (discovery −9.02 vs −6.45, coverage edge shrunk) even though it fixed
the diagnosed round-2–4 collapse in isolation. The engine keeps selection
(novelty ranking + FPS) and acceptance, exactly as for Random; this
optimizer only decides *how* candidates are proposed. Round 1 (empty pool)
is distributed like Random round 1, which keeps the first round comparable
in the benchmark.
"""

from __future__ import annotations

import statistics

import numpy as np

from ...analysis.sampling.fps import farthest_point_sampling
from ..optimization import ObservationBatch, OptimizationContext, ProposalBatch

# Amplitude-gene bounds mirror the operator-level validation (displacement
# A11 semantics, strain/shear range checks) so a mutated genome can never
# queue a candidate the operator layer would reject as out-of-range.
_SIGMA_BOUNDS = (0.01, 5.0)
_STRAIN_BOUNDS = (0.005, 0.95)
_MASK_FLOOR = 0.05  # USPEX-style operator floors: an enabled operator never
# dies completely, so the mask can re-discover it later.
_OPERATOR_ATTEMPTS = 3  # USPEX retries before degrading a child slot.
_APPLY_ERRORS = (ValueError, ZeroDivisionError, np.linalg.LinAlgError)

# G4-1.5 mechanisms (evidence: docs/reviews/2026-09-25-g4-2-... and the
# re-benchmark sweep 20260925T090608Z). The warm start fixed the diagnosed
# round-2–4 collapse but the full 20-seed sweep came out WORSE than the
# plain G4-1 profile on both headline metrics (discovery −9.02 vs −6.45,
# coverage edge shrunk), so the shipped default is the plain G4-1 behaviour
# (warmup 0, AutoFrac off); both mechanisms stay available as constructor
# knobs for future operator families. Do not re-enable them on this benchmark
# without a fresh hypothesis — the same 20 seeds must not be iterated on.
_PRESSURE_WARMUP_POOL = 0
_IMMIGRANT_ANNEAL_MAX = 0.4
# G4-1.5 AutoFrac (USPEX update_STUFF): per-operator success rates bias the
# mask multiplicatively, blended with a neutral floor so a cold operator
# keeps half its weight and a hot one at most doubles it.
_AUTOFrac_FLOOR = 0.55  # weight multiplier = FLOOR + (1-FLOOR) · relative success
_AUTOFrac_CLAMP = (0.5, 2.0)


class GeneticOptimizer:
    name = "genetic"

    def __init__(
        self,
        operators: list,
        *,
        children_per_seed: int = 8,
        batch_accept: int = 8,
        parent_fraction: float = 0.7,
        immigrant_fraction: float = 0.15,
        gene_mutation_rate: float = 0.3,
        pressure_warmup_pool: int = _PRESSURE_WARMUP_POOL,
        autofrac: bool = False,
    ) -> None:
        if not operators:
            raise ValueError("genetic optimizer requires at least one operator")
        if children_per_seed < 1 or batch_accept < 1:
            raise ValueError("children_per_seed and batch_accept must be >= 1")
        if not 0.1 <= float(parent_fraction) <= 1.0:
            raise ValueError("parent_fraction must be in [0.1, 1.0]")
        if not 0.0 <= float(immigrant_fraction) <= 0.9:
            raise ValueError("immigrant_fraction must be in [0.0, 0.9]")
        if float(gene_mutation_rate) <= 0.0:
            raise ValueError("gene_mutation_rate must be positive")
        if int(pressure_warmup_pool) < 0:
            raise ValueError("pressure_warmup_pool must be >= 0")
        self.operators = list(operators)
        self.children_per_seed = int(children_per_seed)
        self.batch_accept = int(batch_accept)
        self.parent_fraction = float(parent_fraction)
        self.immigrant_fraction = float(immigrant_fraction)
        self.gene_mutation_rate = float(gene_mutation_rate)
        self.pressure_warmup_pool = int(pressure_warmup_pool)
        self.autofrac = bool(autofrac)
        self._context: OptimizationContext | None = None
        # Parent pool: (candidate, genome) pairs in engine selection order.
        self._pool: list[tuple[object, dict]] = []
        self._pool_descriptors: list[np.ndarray] = []
        # Proposal bookkeeping: id(candidate) → (candidate, operator name,
        # genome that produced it). Keyed by object identity, not
        # candidate_id: strain ids are derived from the volume change, so two
        # children of one parent with the same |det| ratio would collide on
        # the string. The engine hands back the very objects propose returned
        # (CandidateObservation.candidate), so identity is exact; the value
        # keeps the candidate alive so ids cannot be recycled mid-round.
        self._pending: dict[int, tuple[object, str, dict]] = {}
        # Cumulative per-operator proposal/acceptance counters (the AutoFrac
        # bookkeeping hook; only reported in state_dict for G4-1).
        self._operator_stats: dict[str, dict[str, int]] = {}
        self._rounds = 0

    # -- genome --------------------------------------------------------------

    def _initial_genome(self) -> dict:
        """Fresh genome: mask weight 1 for every enabled operator, amplitude
        genes seeded from each operator's own configured params so the user's
        search-space input is the starting point of the gene pool."""
        sigma, strain, shear = 0.15, 0.05, 0.05
        mask: dict[str, float] = {}
        for operator in self.operators:
            name = operator.name
            mask[name] = 1.0
            params = dict(getattr(operator, "operator_params", {}) or {})
            if name == "atomic_displacement":
                sigma = float(params.get("max_sigma", getattr(operator, "max_sigma", 0.15)))
            elif name in ("isotropic_strain", "anisotropic_strain"):
                strain = float(params.get("max_strain", getattr(operator, "max_strain", 0.05)))
            elif name == "cell_shear":
                shear = float(params.get("max_shear", getattr(operator, "max_shear", 0.05)))
        return {
            "sigma": float(np.clip(sigma, *_SIGMA_BOUNDS)),
            "strain": float(np.clip(strain, *_STRAIN_BOUNDS)),
            "shear": float(np.clip(shear, *_STRAIN_BOUNDS)),
            "mask": mask,
        }

    def _mutate_genome(self, genome: dict, rng: np.random.Generator) -> dict:
        rate = self.gene_mutation_rate
        child = dict(genome)
        child["sigma"] = float(np.clip(genome["sigma"] * (1.0 + rng.normal(0.0, rate)), *_SIGMA_BOUNDS))
        child["strain"] = float(np.clip(genome["strain"] * (1.0 + rng.normal(0.0, rate)), *_STRAIN_BOUNDS))
        child["shear"] = float(np.clip(genome["shear"] * (1.0 + rng.normal(0.0, rate)), *_STRAIN_BOUNDS))
        child["mask"] = {
            name: float(np.clip(weight * (1.0 + rng.normal(0.0, rate)), _MASK_FLOOR, 1.0))
            for name, weight in genome["mask"].items()
        }
        return child

    def _operator_params(self, operator, genome: dict) -> dict:
        """The operator's user params with the genome amplitude override."""
        params = dict(getattr(operator, "operator_params", {}) or {})
        name = operator.name
        if name == "atomic_displacement":
            params["max_sigma"] = genome["sigma"]
        elif name in ("isotropic_strain", "anisotropic_strain"):
            params["max_strain"] = genome["strain"]
        elif name == "cell_shear":
            params["max_shear"] = genome["shear"]
        return params

    # -- parent selection ----------------------------------------------------

    def _pool_is_warm(self) -> bool:
        """True once the pool is large enough for rank pressure.

        Below the warmup size the square-cumulative roulette would aim a
        mostly-uniform proposal stream at a handful of parents (the G4-2
        collapse), so the pool is treated as flat: full pool, uniform draw.
        """
        return len(self._pool) >= self.pressure_warmup_pool

    def _eligible_parents(self) -> list[tuple[object, dict]]:
        """Parents eligible for roulette draws, in rank order (rank 0 first).

        Pool order is the engine's selection order, so ranks are
        fitness-then-diversity — the analogue of USPEX's ranking. A cold pool
        contributes everything (see ``_pool_is_warm``); a warm pool is
        truncated to the top ``parent_fraction``.
        """
        pool = self._pool
        if not pool:
            return []
        if not self._pool_is_warm():
            return list(pool)
        count = max(1, min(len(pool), int(round(self.parent_fraction * len(pool)))))
        return pool[:count]

    def _anchor_distance(self, descriptor: np.ndarray | None) -> float:
        if descriptor is None or self._context is None:
            return float("inf")
        descriptor = np.asarray(descriptor, dtype=np.float64)
        return min(
            float(np.linalg.norm(descriptor - np.asarray(anchor, dtype=np.float64)))
            for anchor in self._context.anchor_descriptors
        )

    def _roulette_pick(self, eligible: list[tuple[object, dict]], rng: np.random.Generator):
        """USPEX square-cumulative roulette over ``eligible`` ranks.

        Rank r (0-based) holds Σ_{k=1}^{m-r} k² tickets, so draw probability
        decays cubically with rank distance; independent of fitness scale.
        A cold pool draws uniformly instead — with ~8 entries the cubic
        profile would pin every round on one or two lineages.
        """
        m = len(eligible)
        if not self._pool_is_warm():
            return eligible[int(rng.integers(m))]
        # Search target set: rank tickets enter the roulette multiplied by
        # exp(-(d/r)^2) over the min-anchor distance. All genome/mask/rank
        # machinery is preserved; the region preference enters
        # multiplicatively. Eligible is a prefix of the pool, so descriptor
        # indices align directly. The seed pool joins with baseline tickets
        # (bootstrap: before anything near the region has been accepted, the
        # anchor seed — forced into the pool at distance 0 — is the only
        # structure that can start the fill; its fresh default genome is the
        # gene pool's starting point, same as immigrants).
        targeted = self._context is not None and self._context.anchor_descriptors
        if targeted:
            radius = self._context.region_radius if self._context.region_radius is not None else 15.0
            candidates = list(
                zip(
                    [entry[0] for entry in eligible],
                    [entry[1] for entry in eligible],
                    [
                        tickets
                        * tickets
                        * float(
                            np.exp(-np.square(self._anchor_distance(self._pool_descriptors[index]) / radius))
                        )
                        for index, tickets in enumerate(range(m, 0, -1))
                    ],
                )
            )
            candidates += [
                (
                    seed,
                    self._initial_genome(),
                    float(np.exp(-np.square(self._anchor_distance(descriptor) / radius))),
                )
                for seed, descriptor in zip(
                    self._context.seed_pool,
                    self._context.seed_descriptors or [None] * len(self._context.seed_pool),
                )
            ]
            total = sum(weight for _, _, weight in candidates)
            draw = float(rng.uniform(0.0, total))
            cumulative = 0.0
            for candidate, genome, weight in candidates:
                cumulative += weight
                if cumulative > draw:
                    return candidate, genome
            return candidates[-1][0], candidates[-1][1]
        draw = float(rng.uniform(0.0, sum(k * k for k in range(1, m + 1))))
        cumulative = 0
        for offset, tickets in enumerate(range(m, 0, -1)):
            cumulative += tickets * tickets
            if cumulative > draw:
                return eligible[offset]
        return eligible[-1]

    def _autofrac_multiplier(self, operator_name: str) -> float:
        """Success-rate mask multiplier (G4-1.5 AutoFrac).

        ``accepted/proposed`` relative to the mean over operators that have
        proposals, blended with a neutral floor and clamped: a never-accepted
        operator drops to at most half weight, a hot one at most doubles.
        Operators without proposals (or without any acceptance anywhere) stay
        neutral at 1.0 — cold-start data must not steer the mask. Inactive
        unless ``autofrac`` is set: the G4-1.5 sweep measured it as part of a
        net regression at full budget.
        """
        if not self.autofrac:
            return 1.0
        stats = self._operator_stats.get(operator_name)
        if not stats or stats["proposed"] == 0:
            return 1.0
        rates = {
            name: counters["accepted"] / counters["proposed"]
            for name, counters in self._operator_stats.items()
            if counters["proposed"] > 0
        }
        mean_rate = statistics.fmean(rates.values())
        if mean_rate <= 0.0:
            return 1.0
        relative = rates[operator_name] / mean_rate
        multiplier = _AUTOFrac_FLOOR + (1.0 - _AUTOFrac_FLOOR) * relative
        return float(np.clip(multiplier, *_AUTOFrac_CLAMP))

    def _choose_operator(self, genome: dict, parent, rng: np.random.Generator):
        """Mask-weighted roulette over operators that can apply to ``parent``."""
        weights = genome["mask"]
        available = []
        total = 0.0
        for operator in self.operators:
            can_apply = getattr(operator, "can_apply", None)
            if can_apply is not None and not can_apply(parent, self._operator_params(operator, genome)):
                continue
            weight = float(weights.get(operator.name, 1.0)) * self._autofrac_multiplier(operator.name)
            available.append((operator, weight))
            total += weight
        if not available:
            return None
        draw = float(rng.uniform(0.0, total)) if total > 0 else 0.0
        cumulative = 0.0
        for operator, weight in available:
            cumulative += weight
            if cumulative > draw:
                return operator
        return available[-1][0]

    def _effective_immigrant_fraction(self) -> float:
        """Annealed immigrant share: extra immigrants while the pool is cold,
        easing linearly to the configured value at the warmup size. The
        anneal can only *raise* the share above the user's floor, never
        lower it."""
        if self._pool_is_warm():
            return self.immigrant_fraction
        anneal = _IMMIGRANT_ANNEAL_MAX * (1.0 - len(self._pool) / self.pressure_warmup_pool)
        return min(max(self.immigrant_fraction, anneal), 0.9)

    # -- lifecycle -----------------------------------------------------------

    @property
    def batch_size(self) -> int:
        n_seeds = self._context.n_seeds if self._context is not None else 0
        return int(n_seeds) * self.children_per_seed

    def initialize(self, context: OptimizationContext) -> None:
        self._context = context
        self._pool = []
        self._pool_descriptors = []
        self._pending = {}
        self._operator_stats = {}
        self._rounds = 0

    def _require_context(self) -> OptimizationContext:
        if self._context is None:
            raise RuntimeError("optimizer.propose called before initialize()")
        return self._context

    def _record(self, operator_name: str, candidate, genome: dict, children: list) -> None:
        self._pending[id(candidate)] = (candidate, operator_name, genome)
        self._operator_stats.setdefault(operator_name, {"proposed": 0, "accepted": 0})
        self._operator_stats[operator_name]["proposed"] += 1
        children.append(candidate)

    def _seed_children(self, seed_pool, slots: int, cap: int | None, rng, children: list) -> None:
        """Fill ``slots`` parent slots from the seed pool (immigrant path).

        Operators are drawn by mask weight at the genome amplitudes of a
        fresh default genome — immigrants are fresh starts, not gene
        carriers, but they use the same amplitude semantics as pool children.
        """
        n_seeds = len(seed_pool)
        for _ in range(slots):
            for _ in range(self.children_per_seed):
                if cap is not None and len(children) >= cap:
                    return
                seed = seed_pool[int(rng.integers(n_seeds))]
                genome = self._initial_genome()
                operator = self._choose_operator(genome, seed, rng)
                if operator is None:
                    continue
                try:
                    candidate = operator.apply(seed, rng, self._operator_params(operator, genome))
                except _APPLY_ERRORS:
                    continue
                self._record(operator.name, candidate, genome, children)

    def _pool_children(self, eligible, slots: int, cap: int | None, rng, children: list) -> None:
        """Fill ``slots`` parent slots from the roulette parent pool.

        Each child is born from its parent's genome mutated, via the
        mask-chosen operator; repeated failures degrade to plain displacement
        at the genome σ (USPEX's retry → Random fallback) and a slot whose
        operator family cannot produce anything is skipped.
        """
        displacement = next((operator for operator in self.operators if operator.name == "atomic_displacement"), None)
        for _ in range(slots):
            for _ in range(self.children_per_seed):
                if cap is not None and len(children) >= cap:
                    return
                parent, genome = self._roulette_pick(eligible, rng)
                child_genome = self._mutate_genome(genome, rng)
                used_name = None
                candidate = None
                for _ in range(_OPERATOR_ATTEMPTS):
                    operator = self._choose_operator(child_genome, parent, rng)
                    if operator is None:
                        break
                    try:
                        candidate = operator.apply(parent, rng, self._operator_params(operator, child_genome))
                    except _APPLY_ERRORS:
                        continue
                    used_name = operator.name
                    break
                if candidate is None and displacement is not None:
                    # Degrade path: no mask choice produced a child.
                    try:
                        candidate = displacement.apply(parent, rng, {"max_sigma": child_genome["sigma"]})
                        used_name = displacement.name
                    except _APPLY_ERRORS:
                        continue
                if candidate is None:
                    continue
                self._record(used_name, candidate, child_genome, children)

    def propose(self, *, budget: int, rng: np.random.Generator) -> ProposalBatch:
        context = self._require_context()
        self._rounds += 1
        self._pending.clear()
        children: list = []
        n_slots = int(context.n_seeds)
        cap = int(budget) if budget is not None and int(budget) > 0 else None
        if cap is not None:
            cap = min(cap, n_slots * self.children_per_seed)
        eligible = self._eligible_parents()
        # Immigrant slots: the fixed fraction stays on the seed pool; with an
        # empty pool (round 1) every slot is an immigrant, making the first
        # round distribution match Random's. While the pool is cold the share
        # is annealed up (G4-1.5) to protect the opening rounds.
        immigrant_slots = (
            int(round(self._effective_immigrant_fraction() * n_slots)) if eligible else n_slots
        )
        immigrant_slots = max(0, min(immigrant_slots, n_slots))
        self._seed_children(context.seed_pool, immigrant_slots, cap, rng, children)
        if eligible and (cap is None or len(children) < cap):
            self._pool_children(eligible, n_slots - immigrant_slots, cap, rng, children)
        return ProposalBatch(candidates=children)

    def observe(self, observations: ObservationBatch) -> None:
        """Fold accepted children into the parent pool with their genomes.

        Insertion follows selection_rank (engine order); the pool is then
        FPS-pruned on the scaled descriptors exactly like the Random parent
        pool, with genomes carried along.
        """
        if self._context is None:
            return
        accepted = [
            obs
            for obs in observations.observations
            if obs.accepted and obs.candidate is not None and obs.structure_descriptor is not None
        ]
        accepted.sort(key=lambda obs: obs.selection_rank if obs.selection_rank is not None else len(observations))
        for obs in accepted:
            pending = self._pending.get(id(obs.candidate))
            if pending is None:
                continue
            _candidate, operator_name, genome = pending
            self._pool.append((obs.candidate, genome))
            self._pool_descriptors.append(np.asarray(obs.structure_descriptor, dtype=np.float64))
            stats = self._operator_stats.setdefault(operator_name, {"proposed": 0, "accepted": 0})
            stats["accepted"] += 1
        limit = min(256, max(1, 4 * int(self._context.n_seeds)))
        if len(self._pool) > limit:
            keep = farthest_point_sampling(np.stack(self._pool_descriptors), n_samples=limit).indices
            self._pool = [self._pool[int(index)] for index in keep]
            self._pool_descriptors = [self._pool_descriptors[int(index)] for index in keep]

    def state_dict(self) -> dict:
        return {
            "name": self.name,
            "children_per_seed": self.children_per_seed,
            "batch_accept": self.batch_accept,
            "parent_fraction": self.parent_fraction,
            "immigrant_fraction": self.immigrant_fraction,
            "pressure_warmup_pool": self.pressure_warmup_pool,
            "autofrac": self.autofrac,
            "rounds": self._rounds,
            "pool": [
                {"candidate_id": candidate.candidate_id, "genome": genome}
                for candidate, genome in self._pool
            ],
            "operator_stats": {name: dict(counters) for name, counters in self._operator_stats.items()},
        }
