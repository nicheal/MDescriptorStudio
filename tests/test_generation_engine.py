"""Generation engine tests: fixed-seed reproducibility, frozen archive,
stop criteria, and batch-internal diversity — with a stub evaluator so no
descriptor engine is needed."""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis.sampling import fit_scaling
from mdescriptor_studio_backend.datasets.base import DatasetFrame
from mdescriptor_studio_backend.generation.archive import DescriptorArchive, LocalEnvironmentArchive
from mdescriptor_studio_backend.generation.constraints import build_constraints
from mdescriptor_studio_backend.generation.engine import GenerationEngine
from mdescriptor_studio_backend.generation.evaluator import DescriptorEvaluation
from mdescriptor_studio_backend.generation.models import Budget, OperatorSpec, StructureCandidate
from mdescriptor_studio_backend.generation.objectives import CompositeObjective, NoveltyObjective
from mdescriptor_studio_backend.generation.operators import AtomicDisplacement, IsotropicStrain
from mdescriptor_studio_backend.generation.optimizers import RandomSearchOptimizer
from mdescriptor_studio_backend.generation.registry import GENERATION_REGISTRY


def _seed_pool(n: int = 6) -> list[StructureCandidate]:
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
        pool.append(
            StructureCandidate.from_frame(
                frame,
                candidate_id=f"seed_{i}",
                parent_frame=i,
                metadata={"parent_composition": [14, 14], "parent_atom_count": 2},
            )
        )
    return pool


class _StubEvaluator:
    """Descriptor = per-atom [xyz]; structure value = mean over atoms."""

    def __init__(self, return_atomic: bool = True):
        self.calls = 0
        self._return_atomic = return_atomic

    def evaluate(self, candidates, *, return_atomic=False, control=None) -> DescriptorEvaluation:
        self.calls += 1
        atom_rows = np.concatenate(
            [np.asarray(c.positions, dtype=np.float64) for c in candidates]
        )
        offsets = np.concatenate([[0], np.cumsum([len(c.positions) for c in candidates])]).astype(np.int64)
        pooled = np.stack(
            [atom_rows[int(offsets[i]) : int(offsets[i + 1])].mean(axis=0) for i in range(len(candidates))]
        )
        return DescriptorEvaluation(
            structure_values=pooled,
            atomic_values=atom_rows if return_atomic else None,
            row_offsets=offsets if return_atomic else None,
        )


def _archives(pool):
    reference = np.stack([c.positions.mean(axis=0) for c in pool])
    structure_scaling, _ = fit_scaling(reference, "robust")
    structure_archive = DescriptorArchive(reference, structure_scaling)
    atom_reference = np.concatenate([c.positions for c in pool])
    local_scaling, _ = fit_scaling(atom_reference, "robust")
    local_archive = LocalEnvironmentArchive(atom_reference, local_scaling)
    return structure_archive, local_archive


def _engine(seed, *, budget=None, objective=None, evaluator=None, pool=None, **optimizer_params):
    pool = pool or _seed_pool()
    structure_archive, local_archive = _archives(pool)
    operators = [
        GENERATION_REGISTRY.build_operator(spec)
        for spec in [
            OperatorSpec("atomic_displacement", {"max_sigma": 0.3}),
            OperatorSpec("isotropic_strain", {"max_strain": 0.03}),
        ]
    ]
    optimizer = RandomSearchOptimizer(operators, children_per_seed=optimizer_params.pop("children_per_seed", 4), **optimizer_params)
    return GenerationEngine(
        seed_pool=pool,
        evaluator=evaluator or _StubEvaluator(),
        structure_archive=structure_archive,
        local_archive=local_archive,
        objective=objective or NoveltyObjective(),
        optimizer=optimizer,
        constraints=build_constraints({"min_distance_mode": "covalent", "min_distance_factor": 0.5}),
        budget=budget or Budget(max_evaluations=200, max_accepted=6, max_generations=50),
        rng=np.random.default_rng(seed),
        n_seeds=4,
    )


def _signature(result) -> list:
    return [
        (c.candidate_id, np.round(c.positions, 12).tolist(), round(e.fitness, 12))
        for c, e in zip(result.accepted, result.evaluations)
    ]


class TestReproducibility:
    def test_fixed_seed_reproduces_accepted_set(self):
        a = _engine(42).run()
        b = _engine(42).run()
        assert _signature(a) == _signature(b)
        assert a.stopped_by == b.stopped_by
        assert a.evaluation_count == b.evaluation_count

    def test_different_seed_changes_run(self):
        a = _engine(42).run()
        b = _engine(7).run()
        assert _signature(a) != _signature(b)


class TestStopCriteria:
    def test_max_evaluations(self):
        result = _engine(42, budget=Budget(max_evaluations=16, max_accepted=10**6, max_generations=10**6)).run()
        assert result.stopped_by == "max_evaluations"
        assert result.evaluation_count <= 16 + 4  # one batch may overshoot the cap

    def test_max_accepted(self):
        result = _engine(42, budget=Budget(max_evaluations=10**6, max_accepted=3, max_generations=10**6)).run()
        assert result.stopped_by == "max_accepted"
        assert result.accepted_count == 3

    def test_max_generations(self):
        result = _engine(42, budget=Budget(max_evaluations=10**6, max_accepted=10**6, max_generations=2)).run()
        assert result.stopped_by == "max_generations"
        assert len(result.rounds) == 2

    def test_no_improvement_stops_when_saturated(self):
        # Tiny displacement keeps every child essentially on the seeds, so the
        # best novelty saturates immediately.
        pool = _seed_pool(2)
        structure_archive, local_archive = _archives(pool)
        operator = GENERATION_REGISTRY.build_operator(
            OperatorSpec("atomic_displacement", {"max_sigma": 1e-9})
        )
        optimizer = RandomSearchOptimizer([operator], children_per_seed=2, batch_accept=1)
        engine = GenerationEngine(
            seed_pool=pool,
            evaluator=_StubEvaluator(),
            structure_archive=structure_archive,
            local_archive=local_archive,
            objective=NoveltyObjective(),
            optimizer=optimizer,
            constraints=build_constraints({"min_distance_mode": "none"}),
            budget=Budget(
                max_evaluations=10**6,
                max_accepted=10**6,
                max_generations=10**6,
                no_improvement_rounds=3,
            ),
            rng=np.random.default_rng(5),
            n_seeds=2,
        )
        result = engine.run()
        assert result.stopped_by == "no_improvement"
        assert len(result.rounds) <= 3 + 1 + 3  # warmup of the best + 3 stagnant

    def test_discovery_rate_saturation_stops_the_run(self):
        # Zero-displacement children land exactly on the seeds: every atom row
        # is already archived, so the trailing window adds no novel
        # environments and the scientific stop fires.
        pool = _seed_pool(2)
        structure_archive, local_archive = _archives(pool)
        operator = GENERATION_REGISTRY.build_operator(
            OperatorSpec("atomic_displacement", {"max_sigma": 1e-9})
        )
        optimizer = RandomSearchOptimizer([operator], children_per_seed=2, batch_accept=1)
        engine = GenerationEngine(
            seed_pool=pool,
            evaluator=_StubEvaluator(),
            structure_archive=structure_archive,
            local_archive=local_archive,
            objective=CompositeObjective(structure_weight=0.5, local_weight=0.5, novelty_threshold=1e9),
            optimizer=optimizer,
            constraints=build_constraints({"min_distance_mode": "none"}),
            budget=Budget(
                max_evaluations=10**6,
                max_accepted=10**6,
                max_generations=10**6,
                no_improvement_rounds=None,
                discovery_window=2,
                min_novel_per_100_evals=1.0,
            ),
            rng=np.random.default_rng(5),
            n_seeds=2,
        )
        result = engine.run()
        assert result.stopped_by == "discovery_saturated"
        assert len(result.rounds) >= 2

    def test_discovery_rate_does_not_stop_a_productive_run(self):
        result = _engine(
            42,
            budget=Budget(
                max_evaluations=200,
                max_accepted=4,
                max_generations=50,
                no_improvement_rounds=None,
                discovery_window=3,
                min_novel_per_100_evals=0.0,
            ),
        ).run()
        # A zero threshold never triggers; the run ends on its real budget.
        assert result.stopped_by in {"max_accepted", "max_evaluations", "max_generations", "no_improvement"}


class TestEngineBehaviour:
    def test_one_batch_evaluate_per_round(self):
        evaluator = _StubEvaluator()
        result = _engine(42, evaluator=evaluator, budget=Budget(max_evaluations=200, max_accepted=4, max_generations=50)).run()
        assert evaluator.calls == len(result.rounds)

    def test_archive_updates_once_per_accepting_round(self):
        pool = _seed_pool()
        structure_archive, local_archive = _archives(pool)
        engine = _engine(42, pool=pool)
        engine.structure_archive = structure_archive
        result = engine.run()
        accepting = [r for r in result.rounds if r.accepted]
        assert structure_archive.size == sum(r.accepted for r in result.rounds)
        assert structure_archive.size == len(accepting) * engine.optimizer.batch_accept or structure_archive.size <= result.accepted_count

    def test_geometry_rejections_are_counted(self):
        pool = _seed_pool(2)
        structure_archive, local_archive = _archives(pool)
        operator = GENERATION_REGISTRY.build_operator(
            OperatorSpec("atomic_displacement", {"max_sigma": 0.1})
        )
        optimizer = RandomSearchOptimizer([operator], children_per_seed=4, batch_accept=2)
        engine = GenerationEngine(
            seed_pool=pool,
            evaluator=_StubEvaluator(),
            structure_archive=structure_archive,
            local_archive=local_archive,
            objective=NoveltyObjective(),
            optimizer=optimizer,
            constraints=build_constraints({"min_distance_mode": "none", "max_displacement": 0.01}),
            budget=Budget(max_evaluations=100, max_accepted=4, max_generations=5),
            rng=np.random.default_rng(3),
            n_seeds=2,
        )
        result = engine.run()
        assert sum(r.rejected_geometry for r in result.rounds) > 0

    def test_composite_objective_with_atomic_rows(self):
        result = _engine(
            42,
            objective=CompositeObjective(structure_weight=0.3, local_weight=0.7, novelty_threshold=0.2),
            budget=Budget(max_evaluations=100, max_accepted=4, max_generations=10),
        ).run()
        assert result.accepted_count >= 1
        for evaluation in result.evaluations:
            assert evaluation.novelty is not None
            assert evaluation.local_diversity is not None

    def test_round_records_converge_to_preview_shape(self):
        result = _engine(42, budget=Budget(max_evaluations=200, max_accepted=4, max_generations=50)).run()
        record = result.rounds[-1].to_json()
        for key in (
            "generation",
            "evaluations",
            "rejected_geometry",
            "rejected_duplicate",
            "accepted",
            "best_fitness",
            "best_novelty",
            "mean_novelty",
            "coverage_radius",
            "novel_environments",
        ):
            assert key in record
        assert record["coverage_radius"] >= 0.0
