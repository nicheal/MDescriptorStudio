"""Search-target mode (G5-2 refactor): anchors are a top-level request input
consumed by the random optimizer's targeted branch — distance-weighted parent
selection around the anchor region, immigrant breadth, nearest-retention
parent pool — plus the request-level gating."""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.datasets.base import DatasetFrame
from mdescriptor_studio_backend.generation.models import Budget, StructureCandidate, parse_request
from mdescriptor_studio_backend.generation.operators import AtomicDisplacement, IsotropicStrain
from mdescriptor_studio_backend.generation.optimization import (
    CandidateObservation,
    ObservationBatch,
    OptimizationContext,
)
from mdescriptor_studio_backend.generation.optimizers import RandomSearchOptimizer


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


def _optimizer(**params) -> RandomSearchOptimizer:
    return RandomSearchOptimizer(
        [AtomicDisplacement(0.1), IsotropicStrain(0.03)],
        children_per_seed=params.pop("children_per_seed", 3),
        **params,
    )


def _context(pool=None, n_seeds=4, *, anchors=None, radius=15.0, seed_descriptors=None) -> OptimizationContext:
    return OptimizationContext(
        seed_pool=tuple(pool or _seed_pool()),
        n_seeds=n_seeds,
        budget=Budget(),
        seed_descriptors=seed_descriptors if seed_descriptors is not None else (),
        anchor_descriptors=anchors if anchors is not None else (np.zeros(6),),
        region_radius=radius,
    )


def _accepted(candidate, rank, descriptor) -> CandidateObservation:
    return CandidateObservation(
        candidate_id=candidate.candidate_id,
        generation=1,
        candidate=candidate,
        valid=True,
        accepted=True,
        selection_rank=rank,
        structure_descriptor=descriptor,
    )


class TestLifecycle:
    def test_propose_before_initialize_is_an_error(self):
        optimizer = _optimizer()
        with pytest.raises(RuntimeError, match="initialize"):
            optimizer.propose(budget=100, rng=np.random.default_rng(0))

    def test_no_anchors_keeps_the_classic_random_path(self):
        # Without anchors the targeting branch must be inert: uniform seed
        # draws, reuse pool semantics, no targeting block in state_dict.
        optimizer = _optimizer()
        optimizer.initialize(OptimizationContext(seed_pool=tuple(_seed_pool()), n_seeds=4, budget=Budget()))
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(0))
        assert len(proposal.candidates) == 4 * 3
        assert "targeting" not in optimizer.state_dict()
        optimizer.observe(
            ObservationBatch(generation=1, observations=[_accepted(proposal.candidates[0], 0, np.zeros(6))])
        )
        assert optimizer._targeted_accepted == []

    def test_targeting_activates_on_anchors(self):
        optimizer = _optimizer()
        optimizer.initialize(_context(n_seeds=2))
        optimizer.propose(budget=100, rng=np.random.default_rng(1))
        state = optimizer.state_dict()
        assert state["targeting"]["anchors"] == 1
        assert state["targeting"]["region_radius"] == 15.0


class TestTargetedProposals:
    def test_proposals_concentrate_on_the_near_anchor_seed(self):
        # seed_1 sits on the anchor, seed_0 far away: with the 15% immigrant
        # share, the large majority of proposals must mutate seed_1 —
        # including in round 1, straight from the context descriptors.
        optimizer = _optimizer()
        pool = _seed_pool(2)
        near = np.zeros(6)
        far = np.full(6, 50.0)
        optimizer.initialize(
            OptimizationContext(
                seed_pool=tuple(pool),
                n_seeds=2,
                budget=Budget(),
                seed_descriptors=(far, near),
                anchor_descriptors=(near,),
                region_radius=15.0,
            )
        )
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(3))
        parents = [candidate.parent_candidate_id for candidate in proposal.candidates]
        assert parents.count("seed_1") > parents.count("seed_0") * 5

    def test_round_size_and_budget_cap_hold(self):
        optimizer = _optimizer()
        optimizer.initialize(_context(n_seeds=2))
        assert len(optimizer.propose(budget=10_000, rng=np.random.default_rng(4)).candidates) == 2 * 3
        assert len(optimizer.propose(budget=5, rng=np.random.default_rng(5)).candidates) == 5

    def test_immigrant_share_keeps_global_breadth(self):
        optimizer = _optimizer(immigrant_share=0.5) if False else _optimizer()
        optimizer.initialize(
            OptimizationContext(
                seed_pool=tuple(_seed_pool(3)),
                n_seeds=4,
                budget=Budget(),
                seed_descriptors=(np.zeros(6), np.full(6, 50.0), np.full(6, 60.0)),
                anchor_descriptors=(np.zeros(6),),
                region_radius=15.0,
            )
        )
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(6))
        parents = [candidate.parent_candidate_id for candidate in proposal.candidates]
        # 15% immigrant slots: distant seeds must still appear occasionally.
        assert parents.count("seed_1") + parents.count("seed_2") > 0

    def test_fixed_seed_proposals_are_reproducible(self):
        a, b = _optimizer(), _optimizer()
        for optimizer in (a, b):
            optimizer.initialize(_context(n_seeds=2))
        pa = a.propose(budget=100, rng=np.random.default_rng(42))
        pb = b.propose(budget=100, rng=np.random.default_rng(42))
        assert [c.positions.tolist() for c in pa.candidates] == [c.positions.tolist() for c in pb.candidates]


class TestTargetedPool:
    def test_accepted_children_join_and_overflow_keeps_nearest(self):
        # limit = min(256, 4·n_seeds) = 4; six accepted children must prune
        # back to four, with the on-anchor one surviving (nearest-first).
        optimizer = _optimizer(children_per_seed=6)
        anchor = np.zeros(6)
        optimizer.initialize(
            OptimizationContext(
                seed_pool=tuple(_seed_pool(1)),
                n_seeds=1,
                budget=Budget(),
                seed_descriptors=(np.full(6, 10.0),),
                anchor_descriptors=(anchor,),
                region_radius=15.0,
            )
        )
        proposal = optimizer.propose(budget=100, rng=np.random.default_rng(7))
        assert len(proposal.candidates) == 6
        observations = [
            _accepted(candidate, rank, anchor if rank == 0 else np.full(6, 100.0 + rank))
            for rank, candidate in enumerate(proposal.candidates)
        ]
        optimizer.observe(ObservationBatch(generation=1, observations=observations))
        assert len(optimizer._targeted_accepted) == 4
        assert optimizer.state_dict()["targeting"]["nearest_parent_distance"] == pytest.approx(0.0)

    def test_foreign_accepted_candidates_never_enter_the_pool(self):
        optimizer = _optimizer()
        optimizer.initialize(_context(n_seeds=2))
        optimizer.observe(
            ObservationBatch(generation=1, observations=[_accepted(c, 0, np.zeros(6)) for c in _seed_pool(3)])
        )
        assert optimizer._targeted_accepted == []


class TestRequestGating:
    @staticmethod
    def _request(**overrides) -> dict:
        payload = {
            "dataset_id": "ds",
            "descriptor_run_id": "run",
            "optimizer": "random",
            "optimizer_params": {"children_per_seed": 4, "batch_accept": 2, "n_seeds": 8},
            "anchor_frames": [12, 345],
            "objective": {"type": "novelty"},
            "operators": {"atomic_displacement": {"enabled": True, "max_sigma": 0.1}},
            "constraints": {"min_distance_mode": "none"},
            "budget": {"max_evaluations": 100},
            "seed": 42,
        }
        payload.update(overrides)
        return payload

    def test_anchor_request_normalizes(self):
        request = parse_request(self._request())
        assert request.anchor_frames == [12, 345]
        assert request.region_radius == 15.0
        assert request.optimizer == "random"

    def test_no_anchors_defaults_to_no_target(self):
        request = parse_request(self._request(anchor_frames=None))
        assert request.anchor_frames == []
        assert request.region_radius == 15.0

    def test_anchor_validation(self):
        from mdescriptor_studio_backend.errors import AppError

        # An empty list is "no target", not an error.
        assert parse_request(self._request(anchor_frames=[])).anchor_frames == []
        with pytest.raises(AppError, match="anchor_frames"):
            parse_request(self._request(anchor_frames=[1, 2.5]))
        with pytest.raises(AppError, match="anchor_frames"):
            parse_request(self._request(anchor_frames=[5, 5]))
        with pytest.raises(AppError, match="anchor_frames"):
            parse_request(self._request(anchor_frames=list(range(17))))
        with pytest.raises(AppError, match="region_radius"):
            parse_request(self._request(region_radius=0.0))
        # random/genetic/pso all accept anchors now.
        for name in ("random", "genetic", "pso"):
            assert parse_request(self._request(optimizer=name)).anchor_frames == [12, 345]
        with pytest.raises(AppError, match="reuse_accepted_seeds is not used"):
            parse_request(
                self._request(
                    optimizer_params={"children_per_seed": 4, "batch_accept": 2, "n_seeds": 8, "reuse_accepted_seeds": True}
                )
            )


class TestEngineIntegration:
    def test_targeted_engine_run(self):
        from mdescriptor_studio_backend.analysis.sampling import fit_scaling
        from mdescriptor_studio_backend.generation.archive import DescriptorArchive
        from mdescriptor_studio_backend.generation.constraints import build_constraints
        from mdescriptor_studio_backend.generation.evaluator import DescriptorEvaluation
        from mdescriptor_studio_backend.generation.engine import GenerationEngine
        from mdescriptor_studio_backend.generation.objectives import NoveltyObjective

        pool = _seed_pool(6)
        reference = np.stack([candidate.positions.mean(axis=0) for candidate in pool])
        structure_scaling, _ = fit_scaling(reference, "robust")
        seed_descriptors = tuple(np.asarray(row, dtype=np.float64) for row in reference)
        anchor = seed_descriptors[0]

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
            seed_descriptors=seed_descriptors,
            anchor_descriptors=(anchor,),
            region_radius=15.0,
        )
        result = engine.run()
        assert result.accepted_count > 0
        assert result.evaluation_count <= 200
        targeting = optimizer.state_dict()["targeting"]
        assert targeting["anchors"] == 1
        assert targeting["parent_pool"] >= len(pool)
