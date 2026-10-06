"""GenerationService submit-time gates (review §7/§8, P0):

* the descriptor run must belong to the same dataset as the seed pool;
* local-environment objectives must be rejected for structure-level runs
  at Run-click time — the objective raises at runtime, so a "degraded"
  warning would only defer the failure until after the job was queued.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from mdescriptor_studio_backend.errors import ANALYSIS_STALE, AppError, INVALID_PARAMS, RESULT_INCOMPATIBLE
from mdescriptor_studio_backend.services.generation_service import GenerationService
from mdescriptor_studio_backend.generation.models import parse_request


class _FakeDb:
    """Routes the two submit-time lookups; records writes."""

    def __init__(self, dataset_rows, run_row):
        self._dataset_rows = dataset_rows  # dict keyed by id
        self._run_row = run_row
        self.executed: list[tuple] = []

    def query_one(self, sql, params=()):
        if "FROM datasets" in sql:
            return self._dataset_rows.get(params[0])
        if "FROM descriptor_runs" in sql:
            return self._run_row if params[0] == self._run_row["id"] else None
        return None  # cache lookup: always a miss

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        return 1


class _FakeJobs:
    def __init__(self) -> None:
        self.submitted: list[tuple] = []

    def submit(self, job_type, runner, **kwargs):
        self.submitted.append((job_type, kwargs.get("generation_run_id")))
        return "job_1"


class _FakeResults:
    """feature_space_signature is the established cross-service metadata API."""

    def __init__(self, metadata):
        self._metadata = metadata

    def feature_space_signature(self, run_id):
        return "sig", self._metadata


def _service(
    tmp_path: Path,
    run_dataset_id: str = "ds_A",
    row_semantics: str | None = "atom",
    run_metadata: dict | None = None,
) -> GenerationService:
    dataset_rows = {
        "ds_A": {"id": "ds_A", "fingerprint": "fp_1", "number_of_frames": 4},
        "ds_B": {"id": "ds_B", "fingerprint": "fp_2", "number_of_frames": 4},
    }
    run_row = {
        "id": "run_1",
        "dataset_id": run_dataset_id,
        "status": "COMPLETED",
        "result_path": "run_dir",
        "device": "cpu",
        "descriptor_name": "ACE",
        "descriptor_version": "1",
        "engine_version": "1",
        "parameters_json": "{}",
        "feature_count": 4,
        "row_semantics": row_semantics,
    }
    results = _FakeResults({"dataset_fingerprint": "fp_1"} if run_metadata is None else run_metadata)
    return GenerationService(_FakeDb(dataset_rows, run_row), _FakeJobs(), results, None, tmp_path)


def _payload(dataset_id: str, objective: str = "novelty") -> dict:
    # Per-type payload keys: aggregation is a local/composite tunable, not a
    # novelty one — the parse-time objective schema rejects mismatched keys.
    objective_payload = {"type": objective}
    if objective in ("local_environment_novelty", "composite"):
        objective_payload["aggregation"] = "mean"
    return {
        "dataset_id": dataset_id,
        "descriptor_run_id": "run_1",
        "optimizer": "random",
        "optimizer_params": {"children_per_seed": 2, "batch_accept": 2, "n_seeds": 2},
        "objective": objective_payload,
        "operators": {"atomic_displacement": {"enabled": True, "max_sigma": 0.1}},
        "constraints": {"min_distance_mode": "none"},
        "budget": {"max_evaluations": 16, "max_accepted": 4, "max_generations": 4},
        "seed": 42,
    }


class TestDatasetConsistencyGate:
    def test_cross_dataset_submit_is_rejected(self, tmp_path: Path):
        # Both datasets exist; the run simply describes the other one.
        service = _service(tmp_path, run_dataset_id="ds_A", row_semantics="atom")
        with pytest.raises(AppError) as excinfo:
            service.submit(_payload("ds_B"))
        assert excinfo.value.code == RESULT_INCOMPATIBLE
        assert "different dataset" in excinfo.value.message
        assert service.db.executed == []

    def test_matching_dataset_passes_the_gate(self, tmp_path: Path):
        service = _service(tmp_path, run_dataset_id="ds_A", row_semantics="structure")
        result = service.submit(_payload("ds_A"))
        assert result["job_id"] == "job_1"
        assert result["cached"] is False
        # The run row must have been created before the job was enqueued.
        assert any("INSERT INTO generation_runs" in sql for sql, _ in service.db.executed)


class TestAtomLevelCapabilityGate:
    def test_local_objective_on_structure_level_run_is_rejected_at_submit(self, tmp_path: Path):
        service = _service(tmp_path, run_dataset_id="ds_A", row_semantics="structure")
        with pytest.raises(AppError) as excinfo:
            service.submit(_payload("ds_A", objective="local_environment_novelty"))
        assert excinfo.value.code == RESULT_INCOMPATIBLE
        assert "atom-level" in excinfo.value.message
        # Rejected before any run row is created or job enqueued.
        assert service.db.executed == []

    def test_local_objective_with_atom_level_run_passes(self, tmp_path: Path):
        service = _service(tmp_path, run_dataset_id="ds_A", row_semantics="atom")
        result = service.submit(_payload("ds_A", objective="local_environment_novelty"))
        assert result["job_id"] == "job_1"

    def test_composite_objective_on_structure_level_run_is_rejected(self, tmp_path: Path):
        service = _service(tmp_path, run_dataset_id="ds_A", row_semantics=None)
        with pytest.raises(AppError) as excinfo:
            service.submit(_payload("ds_A", objective="composite"))
        assert excinfo.value.code == RESULT_INCOMPATIBLE


class TestFreshnessGate:
    def test_stale_descriptor_run_is_rejected(self, tmp_path: Path):
        # The dataset changed after the run completed: its frozen reference
        # archive no longer describes the frames the seed pool is drawn from.
        service = _service(tmp_path, run_metadata={"dataset_fingerprint": "fp_old"})
        with pytest.raises(AppError) as excinfo:
            service.submit(_payload("ds_A"))
        assert excinfo.value.code == ANALYSIS_STALE
        assert service.db.executed == []

    def test_fresh_descriptor_run_passes(self, tmp_path: Path):
        service = _service(tmp_path, run_metadata={"dataset_fingerprint": "fp_1"})
        assert service.submit(_payload("ds_A"))["job_id"] == "job_1"

    def test_legacy_run_without_fingerprint_metadata_passes(self, tmp_path: Path):
        # Pre-fingerprinting runs cannot be checked; the worker-side
        # dataset_id revalidation still applies.
        service = _service(tmp_path, run_metadata={})
        assert service.submit(_payload("ds_A"))["job_id"] == "job_1"


class TestUnchangedValidation:
    def test_unknown_objective_is_still_invalid_params(self, tmp_path: Path):
        service = _service(tmp_path, run_dataset_id="ds_A", row_semantics="atom")
        with pytest.raises(AppError) as excinfo:
            service.submit(_payload("ds_A", objective="telepathy"))
        assert excinfo.value.code == INVALID_PARAMS


class TestReferenceFreshnessCheck:
    """P1-02: one shared fingerprint comparison for submit AND worker."""

    def test_stale_fingerprint_is_rejected(self, tmp_path: Path):
        service = _service(tmp_path, run_metadata={"dataset_fingerprint": "fp_old"})
        with pytest.raises(AppError) as excinfo:
            service._assert_reference_freshness({"id": "ds_A", "fingerprint": "fp_1"}, "run_1")
        assert excinfo.value.code == ANALYSIS_STALE

    def test_fresh_fingerprint_passes(self, tmp_path: Path):
        service = _service(tmp_path, run_metadata={"dataset_fingerprint": "fp_1"})
        service._assert_reference_freshness({"id": "ds_A", "fingerprint": "fp_1"}, "run_1")

    def test_legacy_run_without_fingerprint_passes(self, tmp_path: Path):
        # Pre-fingerprinting runs cannot be checked at either path; the
        # dataset_id revalidation still applies (submit-time policy kept).
        service = _service(tmp_path, run_metadata={})
        service._assert_reference_freshness({"id": "ds_A", "fingerprint": "fp_1"}, "run_1")


class TestLocalAnchorHelpers:
    """R3.4: the worker's atomic-space anchor selection (pure helpers)."""

    class _Frame:
        def __init__(self, numbers):
            self.numbers = np.asarray(numbers, dtype=np.int64)

    class _Source:
        def __init__(self, frames):
            self._frames = frames

        def get_frame(self, index):
            return TestLocalAnchorHelpers._Frame(self._frames[index])

    class _Seed:
        def __init__(self, parent_frame):
            self.parent_frame = parent_frame

    def _rows(self):
        # 3 frames × 2 atoms, 2-D scaled rows.
        return np.arange(12, dtype=np.float64).reshape(6, 2)

    def _offsets(self):
        return np.array([0, 2, 4, 6])

    def test_species_filter_selects_anchor_frame_atoms(self):
        from mdescriptor_studio_backend.services.generation_service import _local_anchor_rows

        source = self._Source([[6, 14], [14, 14], [6, 6]])
        anchors = _local_anchor_rows(source, self._rows(), self._offsets(), 3, [0, 2], {6})
        # Frame 0 contributes its carbon row, frame 2 both rows.
        assert anchors.shape == (3, 2)
        assert np.array_equal(anchors[0], self._rows()[0])

    def test_empty_species_keeps_every_atom(self):
        from mdescriptor_studio_backend.services.generation_service import _local_anchor_rows

        source = self._Source([[6, 14], [14, 14], [6, 6]])
        anchors = _local_anchor_rows(source, self._rows(), self._offsets(), 3, [0], set())
        assert anchors.shape == (2, 2)

    def test_species_matching_nothing_is_rejected(self):
        from mdescriptor_studio_backend.services.generation_service import _local_anchor_rows

        source = self._Source([[6, 14], [14, 14], [6, 6]])
        with pytest.raises(AppError, match="matched no atoms"):
            _local_anchor_rows(source, self._rows(), self._offsets(), 3, [1], {79})

    def test_misaligned_frame_numbers_are_rejected(self):
        from mdescriptor_studio_backend.services.generation_service import _local_anchor_rows

        source = self._Source([[6, 14, 6], [14, 14], [6, 6]])  # frame 0 declares 3 atoms
        with pytest.raises(AppError, match="align"):
            _local_anchor_rows(source, self._rows(), self._offsets(), 3, [0], set())

    def test_seed_distances_align_and_default_to_none(self):
        from mdescriptor_studio_backend.services.generation_service import _local_anchor_rows, _seed_local_distances

        source = self._Source([[6, 6], [6, 6], [6, 6]])
        rows = np.zeros((6, 2))
        anchors = _local_anchor_rows(source, rows, self._offsets(), 3, [0], set())
        seeds = [self._Seed(0), self._Seed(None), self._Seed(2)]
        distances = _seed_local_distances(seeds, rows, self._offsets(), anchors, 3)
        assert distances[0] == pytest.approx(0.0)  # frame 0 IS an anchor frame
        assert distances[1] is None
        assert distances[2] == pytest.approx(0.0)  # zeros everywhere


class TestWorkerFreshnessGate:
    def _runnable_service(self, tmp_path: Path, metadata: dict) -> GenerationService:
        service = _service(tmp_path, run_metadata=metadata)
        values = np.array([[1.0, 2.0, 3.0], [2.0, 3.0, 4.0]])
        run_meta = {"result_path": str(tmp_path), "metadata": {"num_threads": 2}}
        service.results.load_values = lambda run_id, mmap=False: (values, run_meta)
        return service

    @staticmethod
    def _worker_request() -> "object":
        # The worker instantiates the objective; novelty takes no ctor args.
        return parse_request({**_payload("ds_A"), "objective": {"type": "novelty"}})

    def test_dataset_changed_between_queue_and_execution_is_rejected(self, tmp_path: Path):
        # P1-02: the worker must re-run the submit-time fingerprint
        # comparison at execution start — the dataset can be re-imported
        # while the job sits in the queue, and the seed pool would silently
        # read frames the frozen reference archive no longer describes.
        service = self._runnable_service(tmp_path, {"dataset_fingerprint": "fp_old"})
        with pytest.raises(AppError) as excinfo:
            service._run_generation(None, "gen_x", self._worker_request(), {"scaling": "robust"}, None)
        assert excinfo.value.code == ANALYSIS_STALE

    def test_fresh_fingerprint_reaches_the_assembly_stage(self, tmp_path: Path):
        # The worker gate itself passes; the run then stops on the missing
        # real dataset adapter (outside this unit's scope) — never with
        # ANALYSIS_STALE.
        service = self._runnable_service(tmp_path, {"dataset_fingerprint": "fp_1"})
        with pytest.raises(Exception) as excinfo:
            service._run_generation(None, "gen_x", self._worker_request(), {"scaling": "robust"}, None)
        assert not (isinstance(excinfo.value, AppError) and excinfo.value.code == ANALYSIS_STALE)


class TestTargetRegionPersistence:
    def test_anchor_region_changes_cache_key_and_is_saved_for_review(self, tmp_path: Path):
        service = _service(tmp_path)
        first = parse_request({**_payload("ds_A"), "anchor_frames": [0], "region_radius": 15.0})
        second = parse_request({**_payload("ds_A"), "anchor_frames": [0], "region_radius": 16.0})
        dataset = service.db._dataset_rows["ds_A"]
        assert service._cache_key(first, dataset, {"signature": "same"}, None) != service._cache_key(
            second, dataset, {"signature": "same"}, None
        )

        service.submit({**_payload("ds_A"), "anchor_frames": [0, 2], "region_radius": 18.0})
        insert = next((sql, values) for sql, values in service.db.executed if "INSERT INTO generation_runs" in sql)
        saved_params = json.loads(insert[1][5])
        assert saved_params["anchor_frames"] == [0, 2]
        assert saved_params["region_radius"] == 18.0


class TestScreeningModelIdentity:
    def _screening_request(self, checkpoint: str):
        return parse_request(
            {
                **_payload("ds_A"),
                "constraints": {
                    "min_distance_mode": "none",
                    "energy_screening": {"enabled": True, "model": "DPA4C", "checkpoint": checkpoint},
                },
            }
        )

    def test_checkpoint_content_changes_the_cache_key(self, tmp_path: Path):
        # 2026-10-02 audit P1: the key carried the checkpoint PATH (and the
        # installed wheel version) but not the file content, so a model
        # replaced in place replayed cached runs computed with other weights.
        service = _service(tmp_path)
        dataset = service.db._dataset_rows["ds_A"]
        checkpoint = tmp_path / "dpa4c.pt"
        checkpoint.write_bytes(b"model weights v1")
        first = service._cache_key(self._screening_request(str(checkpoint)), dataset, {"signature": "same"}, None)

        checkpoint.write_bytes(b"model weights v2 (fine-tuned)")
        second = service._cache_key(self._screening_request(str(checkpoint)), dataset, {"signature": "same"}, None)
        assert first != second

    def test_unreadable_checkpoint_fails_instead_of_keying_a_run(self, tmp_path: Path):
        service = _service(tmp_path)
        dataset = service.db._dataset_rows["ds_A"]
        request = self._screening_request(str(tmp_path / "missing.pt"))
        with pytest.raises(AppError, match="not readable"):
            service._cache_key(request, dataset, {"signature": "same"}, None)


class TestGenerationResumeRpc:
    """generation.resume guards (productized R5.5, 2026-10-02)."""

    class _ResumeDb:
        """Routes the four lookups resume() makes; records writes."""

        def __init__(self, generation_row, dataset_rows, descriptor_rows):
            self._generation_row = generation_row
            self._dataset_rows = dataset_rows
            self._descriptor_rows = descriptor_rows
            self.executed: list[tuple] = []

        def query_one(self, sql, params=()):
            if "FROM generation_runs" in sql:
                return self._generation_row if params[0] == self._generation_row["id"] else None
            if "FROM datasets" in sql:
                return self._dataset_rows.get(params[0])
            if "FROM descriptor_runs" in sql:
                return self._descriptor_rows.get(params[0])
            return None  # dataset_views: no seed view selected

        def execute(self, sql, params=()):
            self.executed.append((sql, params))
            return 1

    def _resume_service(self, tmp_path: Path, generation_row: dict):
        dataset_rows = {"ds_A": {"id": "ds_A", "fingerprint": "fp_1", "number_of_frames": 4}}
        descriptor_rows = {
            "run_1": {
                "id": "run_1",
                "dataset_id": "ds_A",
                "status": "COMPLETED",
                "result_path": "run_dir",
                "device": "cpu",
                "descriptor_name": "ACE",
                "descriptor_version": "1",
                "engine_version": "1",
                "parameters_json": "{}",
                "feature_count": 4,
                "row_semantics": "atom",
            }
        }
        db = self._ResumeDb(generation_row, dataset_rows, descriptor_rows)
        results = _FakeResults({"dataset_fingerprint": "fp_1"})
        jobs = _FakeJobs()
        service = GenerationService(db, jobs, results, None, tmp_path)
        return service, jobs

    @staticmethod
    def _interrupted_row(tmp_path: Path, *, status="INTERRUPTED", resumable=1, snapshot=True, version=3):
        snapshot_dir = tmp_path / "generation_snapshots" / "gen_1"
        row = {
            "id": "gen_1",
            "dataset_id": "ds_A",
            "descriptor_run_id": "run_1",
            "status": status,
            "resumable": resumable,
            "snapshot_path": str(snapshot_dir) if snapshot else None,
            "params_json": json.dumps(_payload("ds_A")),
        }
        if snapshot:
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            (snapshot_dir / "state.json").write_text(json.dumps({"version": version}), encoding="utf-8")
        return row

    def test_resume_requeues_and_submits(self, tmp_path: Path):
        service, jobs = self._resume_service(tmp_path, self._interrupted_row(tmp_path))
        result = service.resume({"id": "gen_1"})
        assert result == {"generation_id": "gen_1", "job_id": "job_1"}
        assert jobs.submitted == [("generation.run", "gen_1")]
        update = next((sql for sql, _ in service.db.executed if "status = 'QUEUED'" in sql), None)
        assert update is not None and "resumable = 0" in update

    def test_resume_rejects_non_interrupted_rows(self, tmp_path: Path):
        service, _ = self._resume_service(tmp_path, self._interrupted_row(tmp_path, status="COMPLETED"))
        with pytest.raises(AppError, match="not resumable"):
            service.resume({"id": "gen_1"})

    def test_resume_rejects_a_non_resumable_row(self, tmp_path: Path):
        service, _ = self._resume_service(tmp_path, self._interrupted_row(tmp_path, resumable=0))
        with pytest.raises(AppError, match="not resumable"):
            service.resume({"id": "gen_1"})

    def test_resume_rejects_a_missing_snapshot(self, tmp_path: Path):
        service, _ = self._resume_service(tmp_path, self._interrupted_row(tmp_path, snapshot=False))
        with pytest.raises(AppError, match="not resumable"):
            service.resume({"id": "gen_1"})

    def test_resume_rejects_a_foreign_snapshot_version(self, tmp_path: Path):
        service, _ = self._resume_service(tmp_path, self._interrupted_row(tmp_path, version=99))
        with pytest.raises(AppError, match="unsupported engine snapshot version"):
            service.resume({"id": "gen_1"})

    def test_delete_removes_the_snapshot_directory(self, tmp_path: Path):
        from mdescriptor_studio_backend.errors import AppError as _AppError

        service, _ = self._resume_service(tmp_path, self._interrupted_row(tmp_path, status="INTERRUPTED"))
        snapshot_dir = tmp_path / "generation_snapshots" / "gen_1"
        assert snapshot_dir.is_dir()
        # delete() refuses a non-terminal row first; mark it terminal.
        service.db._generation_row["status"] = "COMPLETED"
        service.delete({"id": "gen_1"})
        assert not snapshot_dir.exists()


class TestGeometryConstraintsMetadata:
    """metadata.json carries the EFFECTIVE constraints (P1): geometry verdicts
    must be recomputable from the artifact alone, defaults filled in."""

    def test_defaults_are_filled_when_the_payload_omits_keys(self):
        from mdescriptor_studio_backend.generation.constraints import build_constraints
        from mdescriptor_studio_backend.services.generation_service import _geometry_constraints_metadata

        payload = _geometry_constraints_metadata(build_constraints({}), {})
        assert payload["min_distance_mode"] == "none"
        assert payload["min_distance"] is None
        assert payload["min_distance_factor"] == 0.7
        assert payload["min_distance_pairs"] is None
        assert payload["max_displacement"] is None and payload["max_volume_change"] is None
        assert payload["min_volume_per_atom"] is None and payload["max_volume_per_atom"] is None
        assert payload["composition_locked"] is True
        assert payload["atom_count_locked"] is True

    def test_explicit_values_are_carried_verbatim(self):
        from mdescriptor_studio_backend.generation.constraints import build_constraints
        from mdescriptor_studio_backend.services.generation_service import _geometry_constraints_metadata

        request_constraints = {
            "min_distance_mode": "absolute",
            "min_distance": 1.2,
            "min_distance_pairs": {"Si-Si": 2.0},
            "max_displacement": 0.3,
            "composition_locked": False,
        }
        payload = _geometry_constraints_metadata(build_constraints(request_constraints), request_constraints)
        assert payload["min_distance_mode"] == "absolute"
        assert payload["min_distance"] == 1.2
        assert payload["min_distance_factor"] == 0.7
        assert payload["min_distance_pairs"] == {"Si-Si": 2.0}
        assert payload["max_displacement"] == 0.3
        assert payload["composition_locked"] is False
        assert payload["atom_count_locked"] is True
