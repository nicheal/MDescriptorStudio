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


class _RecordingOperator:
    """Spy operator: records the params every apply() call received."""

    def __init__(self, name: str, marker: str, *, applicable: bool = True) -> None:
        self.name = name
        self.operator_params = {"marker": marker}
        self.applicable = applicable
        self.received: list[dict] = []

    def can_apply(self, parent, params: dict) -> bool:
        return self.applicable

    def apply(self, parent, rng, params: dict):
        self.received.append(dict(params))
        return parent.child(
            candidate_id=f"{parent.candidate_id}_{self.name}_{len(self.received)}",
            positions=np.asarray(parent.positions, dtype=np.float64),
            operator=self.name,
            operator_params=dict(params),
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


class TestOperatorParamBinding:
    """The params handed to apply() must belong to the operator actually
    selected — in both the targeted and the classic proposal branch."""

    @staticmethod
    def _spy_optimizer(operators, n_seeds=2) -> RandomSearchOptimizer:
        optimizer = RandomSearchOptimizer(list(operators), children_per_seed=6)
        optimizer.initialize(_context(n_seeds=n_seeds))
        return optimizer

    def test_targeted_branch_binds_the_selected_operator_params(self):
        op_a = _RecordingOperator("op_a", "a")
        op_b = _RecordingOperator("op_b", "b")
        optimizer = self._spy_optimizer([op_a, op_b])
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(8))
        # Both operators must be exercised so the binding is checked per side.
        assert op_a.received and op_b.received
        for operator in (op_a, op_b):
            for params in operator.received:
                assert params["marker"] == operator.operator_params["marker"]
        for candidate in proposal.candidates:
            expected = op_a.operator_params["marker"] if candidate.operator == "op_a" else op_b.operator_params["marker"]
            assert candidate.operator_params["marker"] == expected

    def test_classic_branch_binds_the_selected_operator_params(self):
        op_a = _RecordingOperator("op_a", "a")
        op_b = _RecordingOperator("op_b", "b")
        optimizer = RandomSearchOptimizer([op_a, op_b], children_per_seed=6)
        optimizer.initialize(OptimizationContext(seed_pool=tuple(_seed_pool()), n_seeds=4, budget=Budget()))
        optimizer.propose(budget=1000, rng=np.random.default_rng(9))
        assert op_a.received and op_b.received
        for operator in (op_a, op_b):
            for params in operator.received:
                assert params["marker"] == operator.operator_params["marker"]

    def test_single_operator_receives_its_own_params(self):
        only = _RecordingOperator("only_op", "only")
        optimizer = self._spy_optimizer([only])
        optimizer.propose(budget=1000, rng=np.random.default_rng(10))
        assert only.received
        assert all(params["marker"] == "only" for params in only.received)

    def test_inapplicable_operator_is_skipped_in_the_targeted_branch(self):
        blocked = _RecordingOperator("blocked_op", "blocked", applicable=False)
        fallback = _RecordingOperator("fallback_op", "fallback")
        optimizer = self._spy_optimizer([blocked, fallback])
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(11))
        assert blocked.received == []
        assert fallback.received
        assert all(params["marker"] == "fallback" for params in fallback.received)
        assert {candidate.operator for candidate in proposal.candidates} == {"fallback_op"}


class TestLocalAnchorTargeting:
    """R3.4: local-environment anchors — the atomic-space target signal."""

    def test_local_kernel_distinguishes_structurally_identical_seeds(self):
        # Both seeds sit ON the structure anchor, so structure targeting
        # cannot tell them apart; only the atomic-space signal does:
        # seed_0's environments are the target, seed_1's are far from it.
        optimizer = _optimizer()
        near = np.zeros(6)
        optimizer.initialize(
            OptimizationContext(
                seed_pool=tuple(_seed_pool(2)),
                n_seeds=2,
                budget=Budget(),
                seed_descriptors=(near, near),
                anchor_descriptors=(near,),
                region_radius=15.0,
                local_anchor_descriptors=(np.array([2.0, 0.0, 0.0, 0.0, 0.0, 0.0]),),
                seed_local_distances=(0.0, 50.0),
            )
        )
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(21))
        parents = [candidate.parent_candidate_id for candidate in proposal.candidates]
        assert parents.count("seed_0") > parents.count("seed_1") * 5

    def test_local_kernel_is_inert_without_local_anchors(self):
        # Same context minus the local anchors: both structure-identical
        # seeds are equally likely again — the local signal is additive, not
        # a replacement for the structure kernel.
        near = np.zeros(6)

        def _make(with_local: bool) -> RandomSearchOptimizer:
            optimizer = _optimizer()
            context = OptimizationContext(
                seed_pool=tuple(_seed_pool(2)),
                n_seeds=2,
                budget=Budget(),
                seed_descriptors=(near, near),
                anchor_descriptors=(near,),
                region_radius=15.0,
                seed_local_distances=(0.0, 50.0),
            )
            if with_local:
                context = OptimizationContext(
                    seed_pool=tuple(_seed_pool(2)),
                    n_seeds=2,
                    budget=Budget(),
                    seed_descriptors=(near, near),
                    anchor_descriptors=(near,),
                    region_radius=15.0,
                    local_anchor_descriptors=(np.array([2.0, 0.0, 0.0, 0.0, 0.0, 0.0]),),
                    seed_local_distances=(0.0, 50.0),
                )
            optimizer.initialize(context)
            return optimizer

        parents_without = [
            candidate.parent_candidate_id for candidate in _make(False).propose(budget=1000, rng=np.random.default_rng(22)).candidates
        ]
        parents_with = [
            candidate.parent_candidate_id for candidate in _make(True).propose(budget=1000, rng=np.random.default_rng(22)).candidates
        ]
        assert parents_without.count("seed_1") > 0
        assert parents_with.count("seed_1") == 0

    def test_retention_uses_the_combined_distance(self):
        # Six accepted children, only the first locally exact: after the
        # nearest-combined pruning to 4, the locally-exact entry survives
        # and sorts first.
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
                local_anchor_descriptors=(np.array([2.0, 0.0, 0.0, 0.0, 0.0, 0.0]),),
                seed_local_distances=(5.0,),
            )
        )
        proposal = optimizer.propose(budget=100, rng=np.random.default_rng(7))
        observations = []
        for rank, candidate in enumerate(proposal.candidates):
            local_rows = np.array([[2.0, 0.0, 0.0, 0.0, 0.0, 0.0]]) if rank == 0 else np.full((1, 6), 100.0)
            observations.append(
                CandidateObservation(
                    candidate_id=candidate.candidate_id,
                    generation=1,
                    candidate=candidate,
                    valid=True,
                    accepted=True,
                    selection_rank=rank,
                    structure_descriptor=anchor if rank == 0 else np.full(6, 100.0),
                    local_descriptor=local_rows,
                )
            )
        optimizer.observe(ObservationBatch(generation=1, observations=observations))
        assert len(optimizer._targeted_accepted) == 4
        assert optimizer._targeted_local_distances[0] == pytest.approx(0.0)
        assert optimizer.state_dict()["targeting"]["local_anchors"] == 1

    def test_engine_feeds_local_descriptors_through_observations(self):
        from mdescriptor_studio_backend.analysis.sampling import fit_scaling
        from mdescriptor_studio_backend.generation.archive import DescriptorArchive, LocalEnvironmentArchive
        from mdescriptor_studio_backend.generation.constraints import build_constraints
        from mdescriptor_studio_backend.generation.engine import GenerationEngine
        from mdescriptor_studio_backend.generation.evaluator import DescriptorEvaluation
        from mdescriptor_studio_backend.generation.objectives import CompositeObjective

        pool = _seed_pool(6)
        reference = np.stack([candidate.positions.mean(axis=0) for candidate in pool])
        structure_scaling, _ = fit_scaling(reference, "robust")
        atom_reference = np.concatenate([candidate.positions for candidate in pool])
        local_scaling, _ = fit_scaling(atom_reference, "robust")
        local_archive = LocalEnvironmentArchive(atom_reference, local_scaling)
        anchor = np.asarray(reference[0], dtype=np.float64)

        class _StubEvaluator:
            def evaluate(self, candidates, *, return_atomic=False, control=None):
                rows = np.concatenate([np.asarray(c.positions, dtype=np.float64) for c in candidates])
                offsets = np.concatenate([[0], np.cumsum([len(c.positions) for c in candidates])]).astype(np.int64)
                pooled = np.stack(
                    [rows[int(offsets[i]) : int(offsets[i + 1])].mean(axis=0) for i in range(len(candidates))]
                )
                return DescriptorEvaluation(structure_values=pooled, atomic_values=rows, row_offsets=offsets)

        optimizer = _optimizer(children_per_seed=2)
        engine = GenerationEngine(
            seed_pool=pool,
            evaluator=_StubEvaluator(),
            structure_archive=DescriptorArchive(reference, structure_scaling),
            local_archive=local_archive,
            objective=CompositeObjective(structure_weight=0.0, local_weight=1.0, novelty_threshold=0.25),
            optimizer=optimizer,
            constraints=build_constraints({"min_distance_mode": "none"}),
            budget=Budget(max_evaluations=60, max_accepted=6, max_generations=3),
            rng=np.random.default_rng(13),
            n_seeds=3,
            seed_descriptors=tuple(np.asarray(row, dtype=np.float64) for row in reference),
            anchor_descriptors=(anchor,),
            region_radius=15.0,
            local_anchor_descriptors=(np.zeros(3),),
            seed_local_distances=tuple(0.0 for _ in pool),
        )
        result = engine.run()
        assert result.accepted_count > 0
        # Observations carried atomic rows into the optimizer's pool.
        assert any(dl is not None for dl in optimizer._targeted_local_distances)
        assert optimizer.state_dict()["targeting"]["local_anchors"] == 1


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

    def test_target_mode_defaults_to_structure(self):
        request = parse_request(self._request())
        assert request.target_mode == "structure"
        assert request.anchor_species == []

    def test_local_mode_normalizes_species(self):
        request = parse_request(self._request(target_mode="local_environment", anchor_species=["C", "O"]))
        assert request.target_mode == "local_environment"
        assert request.anchor_species == ["C", "O"]

    def test_local_mode_requires_anchors(self):
        from mdescriptor_studio_backend.errors import AppError

        with pytest.raises(AppError, match="requires anchor_frames"):
            parse_request(self._request(anchor_frames=[], target_mode="local_environment"))

    def test_local_mode_requires_the_random_optimizer(self):
        from mdescriptor_studio_backend.errors import AppError

        with pytest.raises(AppError, match="random optimizer"):
            parse_request(
                self._request(
                    optimizer="genetic",
                    optimizer_params={"children_per_seed": 4, "batch_accept": 2, "n_seeds": 8},
                    target_mode="local_environment",
                )
            )

    def test_unknown_species_rejected(self):
        from mdescriptor_studio_backend.errors import AppError

        with pytest.raises(AppError, match="unknown element symbols"):
            parse_request(self._request(target_mode="local_environment", anchor_species=["Xx"]))

    def test_anchor_mode_and_strategy_gating(self):
        from mdescriptor_studio_backend.errors import AppError

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
