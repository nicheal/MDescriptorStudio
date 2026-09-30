"""Engine resume roundtrips (audit R5.5).

For every optimizer family: run a few rounds while snapshotting at each
round boundary, restore the round-k snapshot into a fresh engine with an
extended budget, continue, and require the remainder to be bit-identical to
an uninterrupted run with the same seed.
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
from mdescriptor_studio_backend.generation.models import Budget, StructureCandidate
from mdescriptor_studio_backend.generation.objectives import CompositeObjective
from mdescriptor_studio_backend.generation.operators import AtomicDisplacement, IsotropicStrain
from mdescriptor_studio_backend.generation.optimizers import GeneticOptimizer, PSOOptimizer, RandomSearchOptimizer

KINDS = ["random", "random-targeted", "genetic", "pso"]


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
        pool.append(StructureCandidate.from_frame(frame, candidate_id=f"seed_{i}", parent_frame=i))
    return pool


class _StubEvaluator:
    """Descriptor = per-atom [xyz]; structure value = mean over atoms."""

    def evaluate(self, candidates, *, return_atomic=False, control=None) -> DescriptorEvaluation:
        rows = np.concatenate([np.asarray(c.positions, dtype=np.float64) for c in candidates])
        offsets = np.concatenate([[0], np.cumsum([len(c.positions) for c in candidates])]).astype(np.int64)
        pooled = np.stack(
            [rows[int(offsets[i]) : int(offsets[i + 1])].mean(axis=0) for i in range(len(candidates))]
        )
        return DescriptorEvaluation(structure_values=pooled, atomic_values=rows, row_offsets=offsets)


def _build(kind: str, seed: int, max_generations: int, on_round=None) -> GenerationEngine:
    pool = _seed_pool(6)
    reference = np.stack([c.positions.mean(axis=0) for c in pool])
    structure_scaling, _ = fit_scaling(reference, "robust")
    structure_archive = DescriptorArchive(reference, structure_scaling)
    atom_reference = np.concatenate([c.positions for c in pool])
    local_scaling, _ = fit_scaling(atom_reference, "robust")
    local_archive = LocalEnvironmentArchive(atom_reference, local_scaling)
    anchor = np.asarray(reference[0], dtype=np.float64)

    if kind == "random":
        optimizer = RandomSearchOptimizer([AtomicDisplacement(0.3), IsotropicStrain(0.03)], children_per_seed=4)
    elif kind == "random-targeted":
        optimizer = RandomSearchOptimizer([AtomicDisplacement(0.3)], children_per_seed=4)
    elif kind == "genetic":
        optimizer = GeneticOptimizer(
            [AtomicDisplacement(0.3), IsotropicStrain(0.03)], children_per_seed=4, batch_accept=4
        )
    elif kind == "pso":
        optimizer = PSOOptimizer([AtomicDisplacement(0.3), IsotropicStrain(0.03)], children_per_seed=4, batch_accept=4)
    else:
        raise ValueError(kind)

    kwargs: dict = {}
    if kind == "random-targeted":
        kwargs = {
            "seed_descriptors": tuple(np.asarray(row, dtype=np.float64) for row in reference),
            "anchor_descriptors": (anchor,),
            "region_radius": 15.0,
            "local_anchor_descriptors": (np.zeros(3),),
            "seed_local_distances": tuple(0.0 for _ in pool),
        }

    return GenerationEngine(
        seed_pool=pool,
        evaluator=_StubEvaluator(),
        structure_archive=structure_archive,
        local_archive=local_archive,
        objective=CompositeObjective(structure_weight=0.0, local_weight=1.0, novelty_threshold=0.25),
        optimizer=optimizer,
        constraints=build_constraints({"min_distance_mode": "none"}),
        budget=Budget(max_evaluations=10**6, max_accepted=10**6, max_generations=max_generations),
        rng=np.random.default_rng(seed),
        n_seeds=4,
        **kwargs,
    )


def _signature(result) -> list:
    return [
        (c.candidate_id, np.round(c.positions, 10).tolist(), round(e.fitness, 10))
        for c, e in zip(result.accepted, result.evaluations)
    ]


def _run_with_snapshots(kind: str, seed: int, max_generations: int, tmp: Path):
    snapshot_dir = tmp / "snapshots"
    snapshots: list[Path] = []

    def on_round(record) -> None:
        target = snapshot_dir / f"round{record.generation:04d}"
        engine.write_snapshot(target)
        snapshots.append(target)

    engine = _build(kind, seed=seed, max_generations=max_generations, on_round=on_round)
    engine.run(on_round=on_round)
    assert len(snapshots) == max_generations
    return engine, snapshots


@pytest.mark.parametrize("kind", KINDS)
def test_resume_continues_bit_identically(tmp_path: Path, kind: str):
    split, total = 3, 6
    _, snapshots = _run_with_snapshots(kind, seed=5, max_generations=split, tmp=tmp_path)

    restored = _build(kind, seed=5, max_generations=total)
    restored.restore_state(snapshots[split - 1])
    resumed_result = restored.run()

    expected = _build(kind, seed=5, max_generations=total).run()

    assert resumed_result.accepted_count == expected.accepted_count
    assert _signature(resumed_result) == _signature(expected)
    assert [r.to_json() for r in resumed_result.rounds] == [r.to_json() for r in expected.rounds]
    assert resumed_result.stopped_by == expected.stopped_by


def test_resume_from_the_final_snapshot_is_a_fixpoint(tmp_path: Path):
    total = 4
    _, snapshots = _run_with_snapshots("random", seed=7, max_generations=total, tmp=tmp_path)
    restored = _build("random", seed=7, max_generations=total)
    restored.restore_state(snapshots[-1])
    result = restored.run()
    # The restored budget is already exhausted: the run appends nothing.
    assert len(result.rounds) == total


def test_snapshot_rejects_a_foreign_algorithm_version(tmp_path: Path):
    _, snapshots = _run_with_snapshots("random", seed=5, max_generations=2, tmp=tmp_path)
    state_file = snapshots[-1] / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["algorithm_version"] = "gen-999"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    restored = _build("random", seed=5, max_generations=4)
    with pytest.raises(ValueError, match="does not match"):
        restored.restore_state(snapshots[-1])


def test_snapshot_rejects_a_foreign_snapshot_version(tmp_path: Path):
    _, snapshots = _run_with_snapshots("random", seed=5, max_generations=2, tmp=tmp_path)
    state_file = snapshots[-1] / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["version"] = 999
    state_file.write_text(json.dumps(state), encoding="utf-8")
    restored = _build("random", seed=5, max_generations=4)
    with pytest.raises(ValueError, match="unsupported snapshot version"):
        restored.restore_state(snapshots[-1])
