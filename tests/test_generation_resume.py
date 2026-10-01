"""Engine resume roundtrips (audit R5.5).

For every optimizer family: run a few rounds while snapshotting at each
round boundary, restore the round-k snapshot into a fresh engine with an
extended budget, continue, and require the remainder to be identical to an
uninterrupted run with the same seed — compared BYTE-EXACTLY (positions,
descriptors, fitness, both archives, optimizer memory and RNG state), not
rounded to 10 digits.

Beyond the happy path, every R5.5 hardening case has a regression test:
torn same-directory rewrites, corrupted/truncated/lying-count data files,
missing required optimizer fields, foreign optimizer types, changed run
configurations, restores into non-empty archives, empty (zero-accept)
replay blocks, already-triggered scientific stops, non-default bit
generators, and screening-verdict fidelity.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import mdescriptor_studio_backend.generation.engine as engine_module
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

KINDS = ["random", "random-targeted", "genetic", "pso", "random-feedback"]


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


class _StubScreening:
    """Every candidate passes, with deterministic fake measurements."""

    def screen(self, candidates):
        return [
            SimpleNamespace(
                accepted=True,
                status="pass",
                energy=-1.0 * index,
                energy_per_atom=-1.0 * index / max(1, len(candidate.atomic_numbers)),
                max_force=0.25 * index,
                reasons=[],
            )
            for index, candidate in enumerate(candidates)
        ]


def _build(
    kind: str,
    seed: int,
    max_generations: int,
    duplicate_threshold: float | None = None,
    energy_screening=None,
    **budget_kwargs,
) -> GenerationEngine:
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
    elif kind == "random-feedback":
        # Exercises the accepted-seed feedback pool's snapshot/load path
        # (R5.5: the Random fixture never enabled reuse_accepted_seeds).
        optimizer = RandomSearchOptimizer(
            [AtomicDisplacement(0.3), IsotropicStrain(0.03)], children_per_seed=4, reuse_accepted_seeds=True
        )
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

    budget_kwargs.setdefault("max_evaluations", 10**6)
    budget_kwargs.setdefault("max_accepted", 10**6)

    return GenerationEngine(
        seed_pool=pool,
        evaluator=_StubEvaluator(),
        structure_archive=structure_archive,
        local_archive=local_archive,
        objective=CompositeObjective(structure_weight=0.0, local_weight=1.0, novelty_threshold=0.25),
        optimizer=optimizer,
        constraints=build_constraints({"min_distance_mode": "none"}),
        budget=Budget(max_generations=max_generations, **budget_kwargs),
        rng=np.random.default_rng(seed),
        n_seeds=4,
        duplicate_threshold=duplicate_threshold,
        energy_screening=energy_screening,
        **kwargs,
    )


def _run_with_snapshots(kind: str, seed: int, max_generations: int, tmp: Path, **build_kwargs):
    snapshot_dir = tmp / "snapshots"
    snapshots: list[Path] = []

    def on_round(record) -> None:
        target = snapshot_dir / f"round{record.generation:04d}"
        engine.write_snapshot(target)
        snapshots.append(target)

    engine = _build(kind, seed=seed, max_generations=max_generations, **build_kwargs)
    engine.run(on_round=on_round)
    return engine, snapshots


# -- byte-exact comparators ---------------------------------------------------


def _json_default(value):
    if isinstance(value, np.ndarray):
        return {"__ndarray__": [value.dtype.str, value.tolist()]}
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def _engine_state_payload(engine: GenerationEngine) -> dict:
    """Full search-carrying state: RNG, optimizer memory, both archives."""
    payload = {
        "rng": json.loads(json.dumps(engine.rng.bit_generator.state, default=_json_default)),
        "optimizer": engine.optimizer.state_dict(),
    }
    for name, archive in (("structure", engine.structure_archive), ("local", engine.local_archive)):
        payload[name] = {
            "size": archive.size,
            "entries": [dataclasses.asdict(entry) for entry in archive.entries],
            "accepted": None if archive.accepted_matrix is None else archive.accepted_matrix.tolist(),
        }
    return payload


def _assert_same_result(resumed, expected) -> None:
    """The resumed run must equal the uninterrupted run bit-for-bit."""
    assert resumed.stopped_by == expected.stopped_by
    assert [r.to_json() for r in resumed.rounds] == [r.to_json() for r in expected.rounds]
    assert [c.candidate_id for c in resumed.accepted] == [c.candidate_id for c in expected.accepted]
    for got, want in zip(resumed.accepted, expected.accepted):
        assert np.array_equal(got.positions, want.positions), got.candidate_id
        assert np.array_equal(got.atomic_numbers, want.atomic_numbers)
        assert np.array_equal(got.cell, want.cell)
        assert np.array_equal(got.pbc, want.pbc)
        assert got.generation == want.generation
        assert got.operator == want.operator
        assert got.parent_candidate_id == want.parent_candidate_id
        assert got.parent_frame == want.parent_frame
        assert got.operator_params == want.operator_params
    assert [e.candidate_id for e in resumed.evaluations] == [e.candidate_id for e in expected.evaluations]
    for got, want in zip(resumed.evaluations, expected.evaluations):
        assert got.fitness == want.fitness  # bit-exact, not rounded
        assert got.novelty == want.novelty
        assert got.local_diversity == want.local_diversity
        assert got.novel_environment_count == want.novel_environment_count
        assert got.atom_count == want.atom_count
        assert np.array_equal(got.structure_descriptor, want.structure_descriptor)
        if got.atomic_descriptors is None or want.atomic_descriptors is None:
            assert got.atomic_descriptors is None and want.atomic_descriptors is None
        else:
            assert np.array_equal(got.atomic_descriptors, want.atomic_descriptors)
        assert (got.energy, got.energy_per_atom, got.max_force) == (
            want.energy,
            want.energy_per_atom,
            want.max_force,
        )
        assert got.screening_status == want.screening_status
        assert got.screening_reasons == want.screening_reasons


def _read_snapshot(directory: Path) -> tuple[dict, dict]:
    manifest = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    files = {
        key: json.loads((directory / entry["name"]).read_text(encoding="utf-8"))
        for key, entry in manifest["files"].items()
    }
    return manifest, files


def _assert_snapshot_equivalent(left: Path, right: Path) -> None:
    """Two snapshots carrying the same state: manifests and data payloads
    must be identical once the per-commit file names are stripped (the
    content hashes must then agree too, because the payloads do)."""
    left_manifest, left_files = _read_snapshot(left)
    right_manifest, right_files = _read_snapshot(right)
    for manifest in (left_manifest, right_manifest):
        for entry in manifest["files"].values():
            entry.pop("name")
    assert left_manifest == right_manifest
    assert left_files == right_files


# -- happy paths ---------------------------------------------------------------


@pytest.mark.parametrize("kind", KINDS)
def test_resume_continues_bit_identically(tmp_path: Path, kind: str):
    split, total = 3, 6
    engine, snapshots = _run_with_snapshots(kind, seed=5, max_generations=split, tmp=tmp_path)
    assert len(snapshots) == split

    restored = _build(kind, seed=5, max_generations=total)
    restored.restore_state(snapshots[split - 1])
    resumed_result = restored.run()

    expected_engine = _build(kind, seed=5, max_generations=total)
    expected = expected_engine.run()

    _assert_same_result(resumed_result, expected)
    assert _engine_state_payload(restored) == _engine_state_payload(expected_engine)


@pytest.mark.parametrize("kind", KINDS)
def test_resume_from_the_final_snapshot_is_a_fixpoint(tmp_path: Path, kind: str):
    total = 4
    _, snapshots = _run_with_snapshots(kind, seed=7, max_generations=total, tmp=tmp_path)
    assert len(snapshots) == total
    source = snapshots[-1]

    restored = _build(kind, seed=7, max_generations=total)
    restored.restore_state(source)
    result = restored.run()
    # The restored budget is already exhausted: the run appends nothing and
    # reports the restored round-boundary state, not fresh defaults
    # (R5.5: PSO used to lose optimizer.rounds and best_fitness/stagnant).
    assert len(result.rounds) == total
    assert result.stopped_by == "max_generations"
    manifest = json.loads((source / "state.json").read_text(encoding="utf-8"))
    assert result.best_fitness == manifest["best_fitness"]
    assert result.stagnant == manifest["stagnant"]
    if kind in ("genetic", "pso"):
        assert manifest["optimizer"]["rounds"] == total

    # Re-saving must reproduce the snapshot: full state, byte-for-byte.
    resaved = tmp_path / "resaved"
    restored.write_snapshot(resaved)
    _assert_snapshot_equivalent(source, resaved)


@pytest.mark.parametrize("kind", KINDS)
def test_empty_rounds_replay_and_resume_cleanly(tmp_path: Path, kind: str):
    # Every candidate is a duplicate: zero-accept rounds everywhere, so the
    # snapshot's replay blocks are empty and neither archive ever grows.
    # R5.5: an empty block used to be restored as a 1-D matrix and crash.
    engine, snapshots = _run_with_snapshots(
        kind, seed=5, max_generations=3, tmp=tmp_path, duplicate_threshold=1e9
    )
    assert len(snapshots) == 3
    restored = _build(kind, seed=5, max_generations=6, duplicate_threshold=1e9)
    restored.restore_state(snapshots[-1])
    resumed = restored.run()
    expected = _build(kind, seed=5, max_generations=6, duplicate_threshold=1e9).run()
    _assert_same_result(resumed, expected)
    assert resumed.accepted_count == 0
    assert restored.structure_archive.size == 0
    assert restored.local_archive.size == 0


@pytest.mark.parametrize("bit_generator_name", ["MT19937", "Philox"])
def test_resume_roundtrips_non_default_bit_generators(tmp_path: Path, bit_generator_name: str):
    # R5.5: MT19937/Philox states carry ndarrays and used to die in
    # json.dumps with "Object of type ndarray is not JSON serializable".
    def build():
        engine = _build("random", seed=5, max_generations=3)
        engine.rng = np.random.Generator(getattr(np.random, bit_generator_name)(1234))
        return engine

    snapshot_dir = tmp_path / "snapshots"
    snapshots: list[Path] = []

    def on_round(record) -> None:
        target = snapshot_dir / f"round{record.generation:04d}"
        engine.write_snapshot(target)
        snapshots.append(target)

    engine = build()
    engine.run(on_round=on_round)
    assert len(snapshots) == 3

    restored = build()
    restored.restore_state(snapshots[-1])
    resumed = restored.run()
    expected_engine = build()
    expected = expected_engine.run()
    _assert_same_result(resumed, expected)
    assert _engine_state_payload(restored) == _engine_state_payload(expected_engine)


def test_screening_measurements_survive_a_resume(tmp_path: Path):
    engine, snapshots = _run_with_snapshots(
        "random", seed=5, max_generations=2, tmp=tmp_path, energy_screening=_StubScreening()
    )
    assert len(snapshots) == 2
    manifest, files = _read_snapshot(snapshots[-1])
    assert all(entry["energy"] is not None for entry in files["evaluations"])

    restored = _build("random", seed=5, max_generations=4, energy_screening=_StubScreening())
    restored.restore_state(snapshots[-1])
    resumed = restored.run()
    expected_engine = _build("random", seed=5, max_generations=4, energy_screening=_StubScreening())
    expected = expected_engine.run()
    _assert_same_result(resumed, expected)
    assert _engine_state_payload(restored) == _engine_state_payload(expected_engine)


def test_expanded_evaluation_budget_continues_history_but_is_not_a_big_budget_run(tmp_path: Path):
    # An 18-evaluation cap truncates round 2 to a 2-candidate tail batch.
    # The documented resume promise is faithfulness to the recorded
    # history, NOT equivalence to a run that always had the bigger budget:
    # the truncated rounds changed RNG consumption and optimizer feedback.
    small_engine, snapshots = _run_with_snapshots(
        "random", seed=5, max_generations=6, tmp=tmp_path, max_evaluations=18
    )
    assert [r.proposed for r in small_engine._active_result.rounds][:2] == [16, 2]
    manifest = json.loads((snapshots[1] / "state.json").read_text(encoding="utf-8"))
    assert manifest["budget"]["max_evaluations"] == 18

    restored = _build("random", seed=5, max_generations=6)
    restored.restore_state(snapshots[1])
    resumed = restored.run()
    assert [r.proposed for r in resumed.rounds[:2]] == [16, 2]
    assert resumed.rounds[2].proposed == 16  # the expanded budget no longer truncates the batch

    big = _build("random", seed=5, max_generations=6).run()
    assert [r.proposed for r in resumed.rounds] != [r.proposed for r in big.rounds]
    assert resumed.stopped_by == "max_generations"


# -- commit protocol -----------------------------------------------------------


def test_failed_same_directory_rewrite_keeps_the_previous_snapshot_readable(tmp_path: Path, monkeypatch):
    # R5.5 case 1: v1 overwrote candidates.json/evaluations.json in place,
    # so a torn rewrite destroyed the previous snapshot behind a still-valid
    # manifest. v2 writes uniquely named, hashed data files and commits by
    # replacing the manifest last.
    target = tmp_path / "snap"
    engine = _build("random", seed=5, max_generations=2)
    engine.run(on_round=lambda record: engine.write_snapshot(target))
    state_before = json.loads((target / "state.json").read_text(encoding="utf-8"))
    candidates_name = state_before["files"]["candidates"]["name"]
    candidates_before = json.loads((target / candidates_name).read_text(encoding="utf-8"))

    real_durable_write = engine_module.durable_write

    def torn_write(path, data):
        if path.name.startswith("candidates-"):
            # A torn data write: half the bytes reach the disk, then the
            # process dies — the old v1 failure mode, injected directly.
            with open(path, "wb") as handle:
                handle.write(data[: len(data) // 2])
            raise OSError("disk full")
        return real_durable_write(path, data)

    monkeypatch.setattr(engine_module, "durable_write", torn_write)
    crashing = _build("random", seed=5, max_generations=2)
    with pytest.raises(OSError):
        crashing.run(on_round=lambda record: crashing.write_snapshot(target))

    assert json.loads((target / "state.json").read_text(encoding="utf-8")) == state_before
    assert json.loads((target / candidates_name).read_text(encoding="utf-8")) == candidates_before

    # The old snapshot still restores and continues.
    monkeypatch.setattr(engine_module, "durable_write", real_durable_write)
    restored = _build("random", seed=5, max_generations=4)
    restored.restore_state(target)
    assert restored.run().stopped_by == "max_generations"

    # A later successful commit cleans the torn orphan file up.
    follower = _build("random", seed=5, max_generations=2)
    follower.run(on_round=lambda record: follower.write_snapshot(target))
    assert len(list(target.glob("candidates-*.json"))) == 1
    assert len(list(target.glob("evaluations-*.json"))) == 1


# -- integrity and validation --------------------------------------------------


def test_restore_rejects_corrupted_truncated_or_lying_data_files(tmp_path: Path):
    _, snapshots = _run_with_snapshots("random", seed=5, max_generations=2, tmp=tmp_path)
    source = snapshots[-1]
    manifest = json.loads((source / "state.json").read_text(encoding="utf-8"))
    evaluations_name = manifest["files"]["evaluations"]["name"]

    # R5.5 case 4: one flipped coordinate must not restore silently.
    corrupted = tmp_path / "corrupted"
    shutil.copytree(source, corrupted)
    payload = json.loads((corrupted / evaluations_name).read_text(encoding="utf-8"))
    payload[0]["structure_descriptor"][0] += 1e-9
    (corrupted / evaluations_name).write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256"):
        _build("random", seed=5, max_generations=4).restore_state(corrupted)

    truncated = tmp_path / "truncated"
    shutil.copytree(source, truncated)
    data = (truncated / evaluations_name).read_bytes()
    (truncated / evaluations_name).write_bytes(data[:-8])
    with pytest.raises(ValueError, match="bytes"):
        _build("random", seed=5, max_generations=4).restore_state(truncated)

    lying = tmp_path / "lying"
    shutil.copytree(source, lying)
    lying_manifest = json.loads((lying / "state.json").read_text(encoding="utf-8"))
    lying_manifest["counts"]["evaluations"] += 1
    (lying / "state.json").write_text(json.dumps(lying_manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="counts"):
        _build("random", seed=5, max_generations=4).restore_state(lying)


def test_restore_rejects_unsafe_data_file_names(tmp_path: Path):
    _, snapshots = _run_with_snapshots("random", seed=5, max_generations=2, tmp=tmp_path)
    state_file = snapshots[-1] / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["files"]["candidates"]["name"] = "../escape.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe data file name"):
        _build("random", seed=5, max_generations=4).restore_state(snapshots[-1])


@pytest.mark.parametrize(
    "kind,missing",
    [("pso", "particles"), ("genetic", "pool"), ("random-targeted", "targeted_accepted")],
)
def test_restore_rejects_missing_required_optimizer_fields(tmp_path: Path, kind: str, missing: str):
    # R5.5 case 2: load_state used to default missing state to empty and
    # restore an amnesiac optimizer whose trajectory diverged.
    _, snapshots = _run_with_snapshots(kind, seed=5, max_generations=2, tmp=tmp_path)
    state_file = snapshots[-1] / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    del state["optimizer"][missing]
    state_file.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="missing required fields"):
        _build(kind, seed=5, max_generations=4).restore_state(snapshots[-1])


def test_restore_rejects_a_foreign_optimizer_type(tmp_path: Path):
    _, snapshots = _run_with_snapshots("pso", seed=5, max_generations=2, tmp=tmp_path)
    with pytest.raises(ValueError, match="optimizer type"):
        _build("random", seed=5, max_generations=4).restore_state(snapshots[-1])


def test_restore_rejects_an_incompatible_configuration(tmp_path: Path):
    # R5.5: a changed seed pool or a re-fitted scaling used to restore
    # "successfully" and silently diverge; the fingerprint must reject it.
    _, snapshots = _run_with_snapshots("random", seed=5, max_generations=2, tmp=tmp_path)

    perturbed = _build("random", seed=5, max_generations=4)
    perturbed.seed_pool[0].positions[0, 0] += 0.05
    with pytest.raises(ValueError, match="fingerprint"):
        perturbed.restore_state(snapshots[-1])

    rescaled = _build("random", seed=5, max_generations=4)
    scaling = rescaled.structure_archive.scaling
    rescaled.structure_archive.scaling = dataclasses.replace(scaling, scale=scaling.scale * 1.01)
    with pytest.raises(ValueError, match="fingerprint"):
        rescaled.restore_state(snapshots[-1])

    local_rescaled = _build("random", seed=5, max_generations=4)
    local_scaling = local_rescaled.local_archive.scaling
    local_rescaled.local_archive.scaling = dataclasses.replace(local_scaling, scale=local_scaling.scale * 1.01)
    with pytest.raises(ValueError, match="fingerprint"):
        local_rescaled.restore_state(snapshots[-1])


def test_restore_refuses_a_non_empty_archive(tmp_path: Path):
    # R5.5: restoring twice appended the replay twice (archive 24 -> 48);
    # a restore into a used engine must be rejected outright.
    _, snapshots = _run_with_snapshots("random", seed=5, max_generations=2, tmp=tmp_path)
    restored = _build("random", seed=5, max_generations=4)
    restored.restore_state(snapshots[-1])
    assert restored.structure_archive.size > 0
    with pytest.raises(ValueError, match="non-empty archive"):
        restored.restore_state(snapshots[-1])


def test_restore_rejects_an_unknown_bit_generator(tmp_path: Path):
    _, snapshots = _run_with_snapshots("random", seed=5, max_generations=2, tmp=tmp_path)
    state_file = snapshots[-1] / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["rng"]["bit_generator"] = "NotABitGenerator"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown bit generator"):
        _build("random", seed=5, max_generations=4).restore_state(snapshots[-1])


# -- termination state ---------------------------------------------------------


def test_resume_after_a_target_novelty_stop_runs_no_extra_round(tmp_path: Path):
    # R5.5 case 6: the target stop is decided after on_round, so the round-1
    # snapshot predates it; the restored run used to add a round 2.
    _, snapshots = _run_with_snapshots(
        "random", seed=5, max_generations=6, tmp=tmp_path, target_novelty=0.0
    )
    assert len(snapshots) == 1
    manifest = json.loads((snapshots[0] / "state.json").read_text(encoding="utf-8"))

    restored = _build("random", seed=5, max_generations=6, target_novelty=0.0)
    restored.restore_state(snapshots[0])
    result = restored.run()
    assert len(result.rounds) == 1
    assert result.stopped_by == "target_novelty"
    assert result.best_fitness == manifest["best_fitness"]
    assert result.stagnant == manifest["stagnant"]


def test_resume_after_a_discovery_saturation_stop_runs_no_extra_round(tmp_path: Path):
    # R5.5 case 7: same class of bug for the discovery-rate stop.
    _, snapshots = _run_with_snapshots(
        "random",
        seed=5,
        max_generations=6,
        tmp=tmp_path,
        discovery_window=1,
        min_novel_per_100_evals=1e9,
    )
    assert len(snapshots) == 1

    restored = _build(
        "random", seed=5, max_generations=6, discovery_window=1, min_novel_per_100_evals=1e9
    )
    restored.restore_state(snapshots[0])
    result = restored.run()
    assert len(result.rounds) == 1
    assert result.stopped_by == "discovery_saturated"


# -- version gates (unchanged contracts) ----------------------------------------


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
