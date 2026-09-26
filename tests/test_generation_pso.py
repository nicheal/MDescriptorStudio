"""G5-1 PSO optimizer: per-slot memory, distance-modulated pull masses,
immigrant resets, and the contract lifecycle — USPEX-PSO without crossover."""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.datasets.base import DatasetFrame
from mdescriptor_studio_backend.generation.models import Budget, StructureCandidate
from mdescriptor_studio_backend.generation.operators import AtomicDisplacement, IsotropicStrain
from mdescriptor_studio_backend.generation.optimization import (
    CandidateObservation,
    ObservationBatch,
    OptimizationContext,
)
from mdescriptor_studio_backend.generation.optimizers import PSOOptimizer


def _seed_pool(n: int = 5) -> list[StructureCandidate]:
    rng = np.random.default_rng(11)
    pool = []
    for i in range(n):
        frame = DatasetFrame(
            numbers=np.array([14, 14]),
            positions=rng.uniform(0.5, 3.0, size=(2, 3)),
            cell=np.eye(3) * 8.0,
            pbc=np.ones(3, dtype=bool),
            index=i,
        )
        pool.append(StructureCandidate.from_frame(frame, candidate_id=f"seed_{i}", parent_frame=i))
    return pool


def _optimizer(**params) -> PSOOptimizer:
    return PSOOptimizer(
        [AtomicDisplacement(0.1), IsotropicStrain(0.03)],
        children_per_seed=params.pop("children_per_seed", 3),
        **params,
    )


def _context(pool=None, n_seeds=4) -> OptimizationContext:
    return OptimizationContext(seed_pool=tuple(pool or _seed_pool()), n_seeds=n_seeds, budget=Budget())


def _obs(candidate, *, fitness=1.0, descriptor=None, accepted=False, rank=None, generation=1) -> CandidateObservation:
    return CandidateObservation(
        candidate_id=candidate.candidate_id,
        generation=generation,
        candidate=candidate,
        valid=True,
        accepted=accepted,
        selection_rank=rank,
        fitness=fitness,
        structure_descriptor=descriptor if descriptor is not None else np.asarray(candidate.positions.mean(axis=0)),
    )


class TestLifecycleContract:
    def test_propose_before_initialize_is_an_error(self):
        optimizer = _optimizer()
        with pytest.raises(RuntimeError, match="initialize"):
            optimizer.propose(budget=100, rng=np.random.default_rng(0))

    def test_invalid_constructor_params_rejected(self):
        with pytest.raises(ValueError, match="at least one operator"):
            PSOOptimizer([])
        with pytest.raises(ValueError, match="pso_weight"):
            PSOOptimizer([AtomicDisplacement()], pso_weight_pbest=-1.0)
        with pytest.raises(ValueError, match="pso weight"):
            PSOOptimizer([AtomicDisplacement()], pso_weight_pbest=0.0, pso_weight_gbest=0.0, pso_weight_mut=0.0)
        with pytest.raises(ValueError, match="immigrant_fraction"):
            PSOOptimizer([AtomicDisplacement()], immigrant_fraction=0.95)

    def test_round_one_matches_random_distribution(self):
        # No memory yet → every child is a fresh-seed immigrant.
        optimizer = _optimizer()
        optimizer.initialize(_context(n_seeds=4))
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(0))
        assert len(proposal.candidates) == 4 * 3
        assert all(candidate.parent_candidate_id.startswith("seed_") for candidate in proposal.candidates)

    def test_propose_respects_the_budget_and_round_size(self):
        optimizer = _optimizer()
        optimizer.initialize(_context(n_seeds=2))
        assert len(optimizer.propose(budget=10_000, rng=np.random.default_rng(1)).candidates) == 2 * 3
        assert len(optimizer.propose(budget=5, rng=np.random.default_rng(2)).candidates) == 5

    def test_fixed_seed_proposals_are_reproducible(self):
        a, b = _optimizer(), _optimizer()
        a.initialize(_context())
        b.initialize(_context())
        pa = a.propose(budget=100, rng=np.random.default_rng(42))
        pb = b.propose(budget=100, rng=np.random.default_rng(42))
        assert [c.positions.tolist() for c in pa.candidates] == [c.positions.tolist() for c in pb.candidates]


def _feed_round(optimizer, children, fitnesses, generation=1):
    """Push one round of outcomes through observe with z-relevant fitnesses."""
    observations = [
        _obs(candidate, fitness=fitness, generation=generation)
        for candidate, fitness in zip(children, fitnesses)
    ]
    optimizer.observe(ObservationBatch(generation=generation, observations=observations))


class TestMemory:
    def test_position_and_pbest_take_the_most_exceptional_child(self):
        optimizer = _optimizer()
        optimizer.initialize(_context(n_seeds=1))
        children = optimizer.propose(budget=100, rng=np.random.default_rng(3)).candidates
        _feed_round(optimizer, children, [0.0, 5.0, 1.0])
        particle = optimizer._particles[0]
        # Middle child had the top fitness; with one slot its z is the max.
        assert particle.position is children[1]
        assert particle.pbest is children[1]
        assert particle.pbest_z == pytest.approx(1.389, abs=0.01)  # z of 5.0 over {0,5,1}

    def test_pbest_survives_a_worse_followup_round(self):
        optimizer = _optimizer()
        optimizer.initialize(_context(n_seeds=1))
        first = optimizer.propose(budget=100, rng=np.random.default_rng(4)).candidates
        _feed_round(optimizer, first, [10.0, 0.0, 0.0], generation=1)
        particle = optimizer._particles[0]
        assert particle.pbest is first[0]
        second = optimizer.propose(budget=100, rng=np.random.default_rng(5)).candidates
        _feed_round(optimizer, second, [1.0, 2.0, 0.0], generation=2)
        # Round 2's z-scores are computed within round 2; none beats the
        # stored pbest z of +1.155.
        assert particle.pbest is first[0]
        assert particle.position is second[1]  # position tracks the latest round's best

    def test_geometry_rejected_children_are_ignored(self):
        optimizer = _optimizer()
        optimizer.initialize(_context(n_seeds=1))
        children = optimizer.propose(budget=100, rng=np.random.default_rng(6)).candidates
        rejected = CandidateObservation(
            candidate_id=children[0].candidate_id,
            generation=1,
            candidate=children[0],
            valid=False,
            geometry_rejection="minimum_distance",
        )
        optimizer.observe(ObservationBatch(generation=1, observations=[rejected]))
        assert optimizer._particles[0].position is None
        assert optimizer.state_dict()["particles"][0]["pbest"] is None

    def test_gbest_is_the_best_pbest_across_slots(self):
        optimizer = _optimizer()
        optimizer.initialize(_context(n_seeds=2))
        children = optimizer.propose(budget=100, rng=np.random.default_rng(7)).candidates
        assert len(children) == 6  # 2 slots × 3 children, slot 0 first
        fitnesses = [10.0, 10.0, 10.0, 0.0, 0.0, 0.0]
        _feed_round(optimizer, children, fitnesses)
        # z of 10.0 over {10,10,10,0,0,0} is +1.0; slot 1's best z is −1.0.
        assert optimizer._particles[0].pbest_z == pytest.approx(1.0)
        assert optimizer._particles[1].pbest_z == pytest.approx(-1.0)
        assert optimizer._gbest() is optimizer._particles[0]


class TestAttraction:
    @staticmethod
    def _memory_optimizer():
        optimizer = _optimizer(children_per_seed=1)
        optimizer.initialize(_context(n_seeds=1))
        children = optimizer.propose(budget=100, rng=np.random.default_rng(8)).candidates
        _feed_round(optimizer, children, [1.0, 0.0, 0.0])
        return optimizer

    def test_zero_distance_leaves_the_pull_on_the_current_position(self):
        # d(pos, pbest) = 0 when the position IS the pbest: with the mutation
        # mass the only non-zero term, the own-position branch must win.
        optimizer = self._memory_optimizer()
        particle = optimizer._particles[0]
        particle.position = particle.pbest
        particle.position_desc = particle.pbest_desc
        rng = np.random.default_rng(0)
        targets = {id(optimizer._move_target(particle, rng)) for _ in range(50)}
        assert targets == {id(particle.position)}

    def test_far_memory_targets_attract_most_proposals(self):
        optimizer = _optimizer(children_per_seed=1)
        optimizer.initialize(_context(n_seeds=1))
        children = optimizer.propose(budget=100, rng=np.random.default_rng(9)).candidates
        _feed_round(optimizer, children, [1.0, 0.0, 0.0])
        particle = optimizer._particles[0]
        # Park the position very far from the memories: the pull masses
        # (scaled by distance) dominate the mutation mass w_mut=0.5.
        particle.position_desc = particle.pbest_desc + 1000.0
        rng = np.random.default_rng(1)
        targets = [optimizer._move_target(particle, rng) for _ in range(200)]
        memory_hits = sum(1 for target in targets if target is particle.pbest)
        assert memory_hits > 180

    def test_immigrant_share_resets_to_seeds(self):
        optimizer = _optimizer(immigrant_fraction=0.9)
        optimizer.initialize(_context(n_seeds=1))
        first = optimizer.propose(budget=100, rng=np.random.default_rng(10)).candidates
        _feed_round(optimizer, first, [1.0, 0.0, 0.0])
        second = optimizer.propose(budget=100, rng=np.random.default_rng(11)).candidates
        seed_ids = {f"seed_{i}" for i in range(5)}
        # 90% immigrant share: the large majority of round-2 children still
        # draw fresh seeds despite available memory.
        from_seeds = sum(1 for c in second if c.parent_candidate_id in seed_ids)
        assert from_seeds > len(second) * 0.6


class TestState:
    def test_state_dict_shape(self):
        optimizer = _optimizer()
        optimizer.initialize(_context())
        state = optimizer.state_dict()
        assert state["name"] == "pso"
        assert state["children_per_seed"] == 3
        assert state["pso_weight_gbest"] == 1.5
        assert len(state["particles"]) == 4
        assert all(p["pbest"] is None for p in state["particles"])


class TestRegistryAndRequest:
    def test_registry_builds_pso(self):
        from mdescriptor_studio_backend.generation.models import OperatorSpec
        from mdescriptor_studio_backend.generation.registry import GENERATION_REGISTRY

        operators = [GENERATION_REGISTRY.build_operator(OperatorSpec("atomic_displacement", {"max_sigma": 0.2}))]
        optimizer = GENERATION_REGISTRY.build_optimizer(
            "pso", operators, {"children_per_seed": 4, "batch_accept": 2, "pso_weight_gbest": 2.0}
        )
        assert optimizer.name == "pso"
        assert optimizer.children_per_seed == 4
        assert optimizer.pso_weight_gbest == 2.0
        assert optimizer.batch_accept == 2

    def test_catalog_offers_pso_with_defaults(self):
        from mdescriptor_studio_backend.generation.registry import GENERATION_REGISTRY

        catalog = GENERATION_REGISTRY.catalog()
        pso = next(entry for entry in catalog["optimizers"] if entry["name"] == "pso")
        assert pso["params"]["pso_weight_gbest"] == 1.5

    @staticmethod
    def _request(**overrides) -> dict:
        payload = {
            "dataset_id": "ds",
            "descriptor_run_id": "run",
            "optimizer": "pso",
            "optimizer_params": {"children_per_seed": 4, "batch_accept": 2, "n_seeds": 8},
            "objective": {"type": "novelty"},
            "operators": {"atomic_displacement": {"enabled": True, "max_sigma": 0.1}},
            "constraints": {"min_distance_mode": "none"},
            "budget": {"max_evaluations": 100},
            "seed": 42,
        }
        payload.update(overrides)
        return payload

    def test_pso_params_normalize_at_submit(self):
        from mdescriptor_studio_backend.generation.models import parse_request

        request = parse_request(self._request())
        assert request.optimizer_params["pso_weight_pbest"] == 1.0
        assert request.optimizer_params["pso_weight_gbest"] == 1.5
        assert request.optimizer_params["immigrant_fraction"] == 0.15
        assert request.optimizer_params["batch_accept"] == 2

    def test_pso_param_bounds_enforced(self):
        from mdescriptor_studio_backend.errors import AppError
        from mdescriptor_studio_backend.generation.models import parse_request

        with pytest.raises(AppError, match="pso_weight_gbest"):
            parse_request(self._request(optimizer_params={"pso_weight_gbest": -1.0}))
        with pytest.raises(AppError, match="pso weight"):
            parse_request(
                self._request(
                    optimizer_params={"pso_weight_pbest": 0.0, "pso_weight_gbest": 0.0, "pso_weight_mut": 0.0}
                )
            )
        with pytest.raises(AppError, match="unknown optimizer params"):
            parse_request(self._request(optimizer_params={"parent_fraction": 0.7}))


class TestEngineIntegration:
    def test_pso_run_accepts_and_remembers(self):
        from mdescriptor_studio_backend.analysis.sampling import fit_scaling
        from mdescriptor_studio_backend.generation.archive import DescriptorArchive
        from mdescriptor_studio_backend.generation.constraints import build_constraints
        from mdescriptor_studio_backend.generation.evaluator import DescriptorEvaluation
        from mdescriptor_studio_backend.generation.engine import GenerationEngine
        from mdescriptor_studio_backend.generation.objectives import NoveltyObjective

        pool = _seed_pool(6)
        reference = np.stack([candidate.positions.mean(axis=0) for candidate in pool])
        structure_scaling, _ = fit_scaling(reference, "robust")

        class _StubEvaluator:
            def evaluate(self, candidates, *, return_atomic=False, control=None):
                rows = np.concatenate([np.asarray(c.positions, dtype=np.float64) for c in candidates])
                offsets = np.concatenate([[0], np.cumsum([len(c.positions) for c in candidates])]).astype(np.int64)
                pooled = np.stack(
                    [rows[int(offsets[i]) : int(offsets[i + 1])].mean(axis=0) for i in range(len(candidates))]
                )
                return DescriptorEvaluation(structure_values=pooled, atomic_values=None, row_offsets=None)

        optimizer = _optimizer(children_per_seed=2)
        engine = GenerationEngine(
            seed_pool=pool,
            evaluator=_StubEvaluator(),
            structure_archive=DescriptorArchive(reference, structure_scaling),
            local_archive=None,
            objective=NoveltyObjective(),
            optimizer=optimizer,
            constraints=build_constraints({"min_distance_mode": "covalent", "min_distance_factor": 0.5}),
            budget=Budget(max_evaluations=200, max_accepted=10, max_generations=6),
            rng=np.random.default_rng(13),
            n_seeds=3,
        )
        result = engine.run()
        assert result.accepted_count > 0
        assert result.evaluation_count <= 200
        particles = optimizer.state_dict()["particles"]
        assert any(p["pbest"] is not None for p in particles)
