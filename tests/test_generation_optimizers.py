"""G3.5 optimizer lifecycle contract: initialize → propose → observe → state.

The optimizer protocol is the GA/PSO precondition (review §12): an optimizer
that cannot receive fitness feedback can only generate blindly. These tests
pin the Random optimizer's implementation of the contract — it stays the
regression baseline for every future optimizer.
"""

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


def _context(pool=None, n_seeds=4) -> OptimizationContext:
    return OptimizationContext(seed_pool=tuple(pool or _seed_pool()), n_seeds=n_seeds, budget=Budget())


class TestLifecycleContract:
    def test_propose_before_initialize_is_an_error(self):
        optimizer = _optimizer()
        with pytest.raises(RuntimeError, match="initialize"):
            optimizer.propose(budget=100, rng=np.random.default_rng(0))

    def test_initialize_resets_internal_state(self):
        optimizer = _optimizer(reuse_accepted_seeds=True)
        optimizer._feedback_seed_pool.append(_seed_pool(1)[0])
        optimizer.initialize(_context())
        assert optimizer._feedback_seed_pool == []
        assert optimizer.batch_size == 4 * 3

    def test_propose_returns_the_full_round_within_budget(self):
        optimizer = _optimizer()
        optimizer.initialize(_context(n_seeds=4))
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(0))
        assert len(proposal.candidates) == 4 * 3  # n_seeds × children_per_seed
        assert all(candidate.parent_candidate_id is not None for candidate in proposal.candidates)

    def test_propose_respects_the_budget_cap(self):
        optimizer = _optimizer()
        optimizer.initialize(_context(n_seeds=4))
        proposal = optimizer.propose(budget=5, rng=np.random.default_rng(0))
        assert len(proposal.candidates) == 5

    def test_fixed_seed_proposals_are_reproducible(self):
        a = _optimizer()
        a.initialize(_context())
        b = _optimizer()
        b.initialize(_context())
        pa = a.propose(budget=100, rng=np.random.default_rng(42))
        pb = b.propose(budget=100, rng=np.random.default_rng(42))
        assert [c.positions.tolist() for c in pa.candidates] == [c.positions.tolist() for c in pb.candidates]

    def test_state_dict_reports_lineage_and_knobs(self):
        optimizer = _optimizer(reuse_accepted_seeds=True)
        optimizer.initialize(_context())
        state = optimizer.state_dict()
        assert state["name"] == "random"
        assert state["children_per_seed"] == 3
        assert state["reuse_accepted_seeds"] is True
        assert state["feedback_pool"] == []


class TestObserveFeedbackPool:
    @staticmethod
    def _accepted_observation(candidate, rank):
        return CandidateObservation(
            candidate_id=candidate.candidate_id,
            generation=1,
            candidate=candidate,
            valid=True,
            accepted=True,
            selection_rank=rank,
            structure_descriptor=np.asarray(candidate.positions.mean(axis=0)),
        )

    def test_observe_is_a_noop_without_reuse_accepted_seeds(self):
        optimizer = _optimizer()
        optimizer.initialize(_context())
        pool = _seed_pool(2)
        optimizer.observe(
            ObservationBatch(generation=1, observations=[self._accepted_observation(pool[0], 0)])
        )
        assert optimizer.state_dict()["feedback_pool"] == []

    def test_accepted_candidates_enter_the_pool_in_selection_order(self):
        optimizer = _optimizer(reuse_accepted_seeds=True)
        optimizer.initialize(_context())
        pool = _seed_pool(3)
        # Proposal order (0,1,2) differs from selection order (2,0).
        optimizer.observe(
            ObservationBatch(
                generation=1,
                observations=[
                    self._accepted_observation(pool[0], 1),
                    CandidateObservation(candidate_id="rejected", generation=1, valid=False, geometry_rejection="minimum_distance"),
                    self._accepted_observation(pool[2], 0),
                ],
            )
        )
        assert optimizer.state_dict()["feedback_pool"] == ["seed_2", "seed_0"]

    def test_pool_is_fps_pruned_to_the_round_size_bound(self):
        optimizer = _optimizer(reuse_accepted_seeds=True)
        pool = _seed_pool(5)
        optimizer.initialize(_context(pool, n_seeds=1))  # limit = min(256, 4·1) = 4
        observations = [self._accepted_observation(candidate, rank) for rank, candidate in enumerate(pool)]
        optimizer.observe(ObservationBatch(generation=1, observations=observations))
        state = optimizer.state_dict()
        assert len(state["feedback_pool"]) == 4
        # The farthest-point subset keeps the descriptor-diverse parents.
        assert set(state["feedback_pool"]) <= {c.candidate_id for c in pool}

    def test_subsequent_runs_start_from_a_clean_pool(self):
        optimizer = _optimizer(reuse_accepted_seeds=True)
        optimizer.initialize(_context())
        pool = _seed_pool(1)
        optimizer.observe(
            ObservationBatch(generation=1, observations=[self._accepted_observation(pool[0], 0)])
        )
        optimizer.initialize(_context())  # a new run must not inherit parents
        assert optimizer.state_dict()["feedback_pool"] == []
