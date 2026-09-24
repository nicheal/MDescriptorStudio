"""Generation artifact tests: atomic publish, completeness, provenance."""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.datasets.base import DatasetFrame
from mdescriptor_studio_backend.generation.artifacts import GenerationArtifactReader, GenerationArtifactWriter
from mdescriptor_studio_backend.generation.engine import EvaluatedRecord, GenerationRunResult, RoundRecord
from mdescriptor_studio_backend.generation.models import CandidateEvaluation, StructureCandidate


def _run_result(n: int = 4) -> GenerationRunResult:
    result = GenerationRunResult(stopped_by="max_accepted")
    for i in range(n):
        frame = DatasetFrame(
            numbers=np.array([14, 14]),
            positions=np.array([[float(i), 0.0, 0.0], [float(i) + 2.0, 1.0, 0.5]]),
            cell=np.eye(3) * 8.0,
            pbc=np.ones(3, dtype=bool),
        )
        candidate = StructureCandidate.from_frame(frame, candidate_id=f"cand_{i}", parent_frame=i)
        candidate.generation = 1
        candidate.operator = "atomic_displacement"
        result.accepted.append(candidate)
        result.evaluations.append(
            CandidateEvaluation(
                candidate_id=candidate.candidate_id,
                valid=True,
                rejection_reason=None,
                structure_descriptor=np.array([float(i), 0.5 * i]),
                atomic_descriptors=np.array([[float(i), 0.1], [float(i) + 2.0, 0.2]]),
                novelty=0.5 + i,
                local_diversity=0.3 + i,
                penalty=0.0,
                fitness=1.0 + i,
                novel_environment_count=1,
                atom_count=2,
            )
        )
        result.evaluated.append(
            EvaluatedRecord(
                candidate_id=candidate.candidate_id,
                generation=1,
                structure_descriptor=np.array([float(i), 0.5 * i], dtype=np.float32),
                novelty=0.5 + i,
                fitness=1.0 + i,
                accepted=True,
            )
        )
        result.evaluated.append(
            EvaluatedRecord(
                candidate_id=f"{candidate.candidate_id}_rejected",
                generation=1,
                structure_descriptor=np.array([float(i) + 0.5, 0.5 * i], dtype=np.float32),
                novelty=0.1,
                fitness=0.2,
                accepted=False,
            )
        )
    result.rounds.append(
        RoundRecord(
            generation=1,
            evaluations=16,
            proposed=16,
            rejected_geometry=2,
            rejected_duplicate=1,
            accepted=n,
            best_fitness=1.0 + (n - 1),
            best_novelty=0.5 + (n - 1),
            mean_novelty=1.0,
            coverage_radius=2.5,
            novel_environments=n,
        )
    )
    return result


def _commit(tmp_path, run, generation_id="gen_testrun1"):
    writer = GenerationArtifactWriter(tmp_path, algorithm_version="gen-test")
    final, manifest = writer.commit(
        generation_id,
        request={
            "optimizer": "random",
            "objective": {"type": "composite"},
            "operators": [{"name": "atomic_displacement", "params": {"max_sigma": 0.1}}],
            "constraints": {"min_distance_mode": "covalent"},
            "budget": {"max_evaluations": 64},
            "seed": 42,
        },
        descriptor_signature={"descriptor_name": "ACE", "scaling": "robust"},
        run=run,
    )
    return writer, final, manifest


def test_commit_publishes_complete_artifact(tmp_path):
    writer, final, manifest = _commit(tmp_path, _run_result())
    assert final.is_dir()
    assert manifest["completed"] is True
    assert writer.is_complete({"id": "gen_testrun1", "result_path": str(final)})
    reader = GenerationArtifactReader(final)
    metadata = reader.metadata()
    assert metadata["descriptor_signature"]["descriptor_name"] == "ACE"
    assert metadata["accepted_count"] == 4
    assert metadata["stopped_by"] == "max_accepted"
    convergence = reader.convergence()
    assert len(convergence["rounds"]) == 1
    assert convergence["rounds"][0]["rejected_geometry"] == 2
    np.testing.assert_allclose(reader.array("fitness"), [1.0, 2.0, 3.0, 4.0])
    np.testing.assert_allclose(reader.array("novelty"), [0.5, 1.5, 2.5, 3.5])
    assert reader.array("structure_descriptors").shape == (4, 2)
    assert reader.array("local_environment_descriptors").shape == (8, 2)
    np.testing.assert_array_equal(reader.array("local_row_offsets"), [0, 2, 4, 6, 8])
    assert reader.array("evaluated_structure_descriptors").shape == (8, 2)
    assert reader.array("evaluated_structure_descriptors").dtype == np.float32
    np.testing.assert_array_equal(reader.array("evaluated_generation"), [1] * 8)
    np.testing.assert_array_equal(reader.array("evaluated_accepted"), [1, 0, 1, 0, 1, 0, 1, 0])
    candidates = reader.candidates()
    assert len(candidates) == 4
    assert candidates[0]["candidate_id"] == "cand_0"
    assert candidates[0]["operator"] == "atomic_displacement"


def test_accepted_extxyz_carries_provenance(tmp_path):
    _, final, _ = _commit(tmp_path, _run_result(2))
    text = (final / "accepted.extxyz").read_text(encoding="utf-8")
    first_header = [line for line in text.splitlines() if "Lattice" in line][0]
    for token in (
        'generation_id="gen_testrun1"',
        'candidate_id="cand_0"',
        "parent_frame=0",
        "generation=1",
        "operator=atomic_displacement",
        "fitness=",
        "novelty=",
        "local_novelty=",
    ):
        assert token in first_header, token
    # The written file reloads through the standard extxyz reader.
    from mdescriptor_studio_backend.datasets.extxyz import ExtXYZAdapter

    adapter = ExtXYZAdapter(final / "accepted.extxyz")
    assert len(adapter) == 2
    frame = adapter.get_frame(0)
    assert frame.numbers.tolist() == [14, 14]
    assert np.isfinite(frame.positions).all()


def test_incomplete_artifact_is_not_complete(tmp_path):
    writer, final, _ = _commit(tmp_path, _run_result(1))
    (final / "fitness.npy").unlink()
    assert not writer.is_complete({"id": "gen_testrun1", "result_path": str(final)})


def test_cancelled_commit_leaves_no_partial_artifact(tmp_path):
    class _Cancel(Exception):
        pass

    class _Ctx:
        calls = 0

        def check_cancelled(self):
            _Ctx.calls += 1
            if _Ctx.calls > 2:
                raise _Cancel()

    writer = GenerationArtifactWriter(tmp_path, algorithm_version="gen-test")
    with pytest.raises(_Cancel):
        writer.commit(
            "gen_cancelled1",
            request={"optimizer": "random", "objective": {"type": "novelty"}},
            descriptor_signature={},
            run=_run_result(6),
            ctx=_Ctx(),
        )
    root = tmp_path / "generation"
    leftovers = [p for p in root.glob(".gen_cancelled1.tmp-*")]
    assert leftovers == [], leftovers
    assert not (root / "gen_cancelled1").exists()


def test_managed_path_rejects_bad_ids(tmp_path):
    writer = GenerationArtifactWriter(tmp_path, algorithm_version="gen-test")
    for bad in ("../escape", "ana_thing", "gen_"):
        with pytest.raises(Exception):
            writer.managed_path(bad, bad)
