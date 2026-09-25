"""Golden regression baseline for the Random optimizer (G3.5 lifecycle refactor).

The Random optimizer is the reference proposal policy every future optimizer
(GA/PSO) is benchmarked against, and the review for G3.5 requires that the
lifecycle refactor leave its behaviour *numerically identical*. The fixture
holds the accepted-set signature captured from the pre-refactor engine
(``choose_seeds`` → ``propose`` + engine-side feedback pools) — the post-
refactor ``initialize`` → ``propose`` → ``observe`` lifecycle reproduces it
exactly. If this test fails, an optimizer or engine change altered Random's
results and any "GA vs Random" comparison would be tainted.

Note: the coverage objective and coverage metric changed semantics in G3.5
(gen-3 → gen-4); these baselines use the novelty and composite objectives,
which were untouched, so they pin the refactor itself, not the old bugs.
"""

from __future__ import annotations

import json
from pathlib import Path

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
from mdescriptor_studio_backend.generation.optimizers import RandomSearchOptimizer
from mdescriptor_studio_backend.generation.registry import GENERATION_REGISTRY

FIXTURE = Path(__file__).resolve().parent / "data" / "generation_random_baseline.json"


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

    def evaluate(self, candidates, *, return_atomic=False, control=None) -> DescriptorEvaluation:
        atom_rows = np.concatenate([np.asarray(c.positions, dtype=np.float64) for c in candidates])
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


def _engine(
    seed,
    *,
    objective=None,
    pool=None,
    optimizer=None,
    constraints=None,
    budget=None,
) -> GenerationEngine:
    pool = pool or _seed_pool()
    structure_archive, local_archive = _archives(pool)
    if optimizer is None:
        operators = [
            GENERATION_REGISTRY.build_operator(spec)
            for spec in [
                OperatorSpec("atomic_displacement", {"max_sigma": 0.3}),
                OperatorSpec("isotropic_strain", {"max_strain": 0.03}),
            ]
        ]
        optimizer = RandomSearchOptimizer(operators, children_per_seed=4)
    return GenerationEngine(
        seed_pool=pool,
        evaluator=_StubEvaluator(),
        structure_archive=structure_archive,
        local_archive=local_archive,
        objective=objective or NoveltyObjective(),
        optimizer=optimizer,
        constraints=constraints or build_constraints({"min_distance_mode": "covalent", "min_distance_factor": 0.5}),
        budget=budget or Budget(max_evaluations=200, max_accepted=6, max_generations=50),
        rng=np.random.default_rng(seed),
        n_seeds=4,
    )


def _signature(result) -> list:
    return [
        [c.candidate_id, np.round(c.positions, 12).tolist(), round(e.fitness, 12)]
        for c, e in zip(result.accepted, result.evaluations)
    ]


@pytest.fixture(scope="module")
def baseline() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("seed", [42, 7, 5])
def test_novelty_runs_match_the_pre_refactor_baseline(baseline, seed):
    expected = baseline[f"novelty_seed{seed}"]
    result = _engine(seed).run()
    assert result.stopped_by == expected["stopped_by"]
    assert result.evaluation_count == expected["evaluations"]
    assert _signature(result) == expected["signature"]


def test_accepted_seed_feedback_matches_the_pre_refactor_baseline(baseline):
    expected = baseline["feedback_seed42"]
    pool = _seed_pool()
    operators = [
        GENERATION_REGISTRY.build_operator(spec)
        for spec in [
            OperatorSpec("atomic_displacement", {"max_sigma": 0.3}),
            OperatorSpec("isotropic_strain", {"max_strain": 0.03}),
        ]
    ]
    optimizer = RandomSearchOptimizer(
        operators, children_per_seed=4, batch_accept=2, reuse_accepted_seeds=True
    )
    result = _engine(
        42,
        pool=pool,
        optimizer=optimizer,
        budget=Budget(max_evaluations=300, max_accepted=12, max_generations=50),
    ).run()
    assert result.stopped_by == expected["stopped_by"]
    assert result.evaluation_count == expected["evaluations"]
    assert _signature(result) == expected["signature"]


def test_discovery_stop_scenario_matches_the_pre_refactor_baseline(baseline):
    expected = baseline["composite_discovery"]
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
    assert result.stopped_by == expected["stopped_by"]
    assert result.evaluation_count == expected["evaluations"]
    assert len(result.rounds) == expected["rounds"]
