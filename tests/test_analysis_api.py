"""Generic Analysis API storage, cache, and artifact lifecycle tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis import AtomDescriptorMatrix, StructureDescriptorMatrix
from mdescriptor_studio_backend.analysis.algorithms.pca import pca
from mdescriptor_studio_backend.analysis.metrics import neighbors, similarity
from mdescriptor_studio_backend.datasets.base import DatasetFrame
from mdescriptor_studio_backend.errors import (
    ANALYSIS_INPUT_INVALID,
    ANALYSIS_STALE,
    INVALID_PARAMS,
    RESULT_INCOMPATIBLE,
    AppError,
)
from mdescriptor_studio_backend.protocol import frames
from mdescriptor_studio_backend.services.analysis_service import _LIST_COLUMNS, AnalysisService
from mdescriptor_studio_backend.services.dataset_service import DatasetService
from mdescriptor_studio_backend.services.job_runner import AnalysisRunMixin
from mdescriptor_studio_backend.services.export_service import _cancellable_frames, _identity_records
from mdescriptor_studio_backend.services import preview_service
from mdescriptor_studio_backend.services.result_service import ResultService
from mdescriptor_studio_backend.storage.database import Database


class _Context:
    def check_cancelled(self):
        return None

    def progress(self, *args, **kwargs):
        return None

    def attach_control(self, _control):
        return None


class _InlineJobs:
    def __init__(self, db: Database):
        self.db = db
        self.calls = 0

    def submit(self, job_type, runner, *, dataset_id=None, descriptor_run_id=None, analysis_run_id=None):
        self.calls += 1
        job_id = f"job_{self.calls}"
        self.db.execute(
            "INSERT INTO jobs (id, job_type, dataset_id, descriptor_run_id, analysis_run_id, status, progress, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'RUNNING', 0, '2026-01-01T00:00:00+00:00')",
            (job_id, job_type, dataset_id, descriptor_run_id, analysis_run_id),
        )
        result = runner(_Context())
        self.db.execute(
            "UPDATE jobs SET status = 'COMPLETED', progress = 1, finished_at = '2026-01-01T00:00:01+00:00' WHERE id = ?",
            (job_id,),
        )
        return job_id


def _service(tmp_path: Path, datasets=None):
    db = Database(tmp_path / "database.sqlite")
    result_dir = tmp_path / "results" / "run_1"
    result_dir.mkdir(parents=True)
    values = np.arange(96, dtype=np.float32).reshape(12, 8)
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_1", "level": "structure", "row_semantics": "structure", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_1', 'ds_1', 'SOAP', 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )
    jobs = _InlineJobs(db)
    return db, jobs, AnalysisService(db, jobs, ResultService(db), datasets=datasets, data_dir=tmp_path)


def _dataset_and_view(db: Database, frame_indices: list[int], selection_hash: str = "h1") -> None:
    """A dataset row plus one saved view over it, as ``dataset.view.create`` writes them."""
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, file_size, created_at)"
        " VALUES ('ds_1', 'GaAs', 'deepmd', '/tmp/ds_1', 12, '[]', '{}', '{}', 'fp_1', 0, '2026-01-01T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO dataset_views (id, dataset_id, name, role, filter_json, frame_indices_json,"
        " selection_hash, dataset_fingerprint, created_at, updated_at)"
        " VALUES ('view_1', 'ds_1', 'Subset', 'selection', '{}', ?, 'fp_1|h1', 'fp_1',"
        " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        (json.dumps(frame_indices),),
    )


def test_export_repeats_when_the_written_file_disappears(tmp_path: Path) -> None:
    # An export is a side effect on a user path, so its COMPLETED row is only
    # evidence that the file was written once. Trusting it afterwards answered
    # "success" for a file the user had moved or deleted.
    db, jobs, service = _service(tmp_path)
    target = tmp_path / "selected.txt"

    first = service.submit_export({"run_id": "run_1", "indices": [2, 0], "format": "indices", "output_path": str(target)})
    assert first["job_id"] is not None
    assert target.read_text(encoding="utf-8").splitlines() == ["0", "2"]
    assert jobs.calls == 1

    # Unchanged request, file still present: the cache answer is truthful.
    cached = service.submit_export({"run_id": "run_1", "indices": [0, 2], "format": "indices", "output_path": str(target)})
    assert cached["job_id"] is None
    assert cached["cache"]["existing_analysis_id"] == first["analysis_id"]
    assert jobs.calls == 1

    target.unlink()
    again = service.submit_export({"run_id": "run_1", "indices": [2, 0], "format": "indices", "output_path": str(target)})
    assert again["job_id"] is not None, "a missing export must be rewritten, not reported from cache"
    assert target.exists()
    assert again["analysis_id"] != first["analysis_id"]

    # The rewrite is itself cached, and repeated deletions keep producing work
    # rather than falling back onto a stale row.
    assert service.submit_export({"run_id": "run_1", "indices": [2, 0], "format": "indices", "output_path": str(target)})["job_id"] is None
    assert jobs.calls == 2
    for _ in range(3):
        target.unlink()
        assert service.submit_export({"run_id": "run_1", "indices": [2, 0], "format": "indices", "output_path": str(target)})["job_id"] is not None
        assert target.exists()
    assert jobs.calls == 5
    db.close()


def test_export_of_the_same_selection_to_two_paths_writes_both(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    left, right = tmp_path / "a.txt", tmp_path / "b.txt"
    service.submit_export({"run_id": "run_1", "indices": [1], "format": "indices", "output_path": str(left)})
    service.submit_export({"run_id": "run_1", "indices": [1], "format": "indices", "output_path": str(right)})
    assert left.read_text(encoding="utf-8").splitlines() == ["1"]
    assert right.read_text(encoding="utf-8").splitlines() == ["1"]
    assert jobs.calls == 2
    db.close()


def _stub_datasets():
    """The slice of DatasetService an export needs: an adapter and a staleness check."""
    return SimpleNamespace(adapter_for=lambda row: list(range(12)), refresh_if_changed=lambda row: None)


def test_export_resolves_the_selection_through_the_same_view_as_the_analysis(tmp_path: Path) -> None:
    # A dataset view renumbers samples, so selection index 0 names the view's
    # first frame rather than dataset frame 0. Exporting without the view wrote
    # the frames that merely carried the same numbers in the whole run — and
    # reported success while doing it.
    db, jobs, service = _service(tmp_path, datasets=_stub_datasets())
    _dataset_and_view(db, [3, 4, 5])

    def frames_of(path: Path) -> list[int]:
        return [record["frame"] for record in json.loads(path.read_text(encoding="utf-8"))["records"]]

    scoped_path = tmp_path / "scoped.json"
    scoped = service.submit_export({
        "run_id": "run_1", "indices": [0, 1], "format": "json", "output_path": str(scoped_path), "view_id": "view_1",
    })
    assert frames_of(scoped_path) == [3, 4]

    whole_path = tmp_path / "whole.json"
    service.submit_export({"run_id": "run_1", "indices": [0, 1], "format": "json", "output_path": str(whole_path)})
    assert frames_of(whole_path) == [0, 1]

    # The scope is part of the export's identity: the same scoped request is
    # still one cached job, and a different scope is not that job.
    repeated = service.submit_export({
        "run_id": "run_1", "indices": [1, 0], "format": "json", "output_path": str(scoped_path), "view_id": "view_1",
    })
    assert repeated["job_id"] is None
    assert repeated["cache"]["existing_analysis_id"] == scoped["analysis_id"]

    resubmit = service.submit_export({
        "run_id": "run_1", "indices": [0, 1], "format": "json", "output_path": str(tmp_path / "again.json"), "view_id": "view_1",
    })
    assert resubmit["analysis_id"] != scoped["analysis_id"]
    assert jobs.calls == 3
    db.close()


def test_export_reports_progress_while_writing_frames() -> None:
    # The writer streams frames, so the only progress an export used to report
    # was 0 and then 1: a large extxyz export sat at 0% for minutes.
    reports: list[tuple[int, int]] = []

    class _Ctx:
        def check_cancelled(self) -> None:
            return None

        def progress(self, completed, total, message="") -> None:
            reports.append((completed, total))

    class _Adapter:
        def get_frame(self, index):
            return index

    frames = list(range(600))
    assert list(_cancellable_frames(_Adapter(), frames, _Ctx())) == frames
    assert reports == [(250, 600), (500, 600), (600, 600)]


def test_export_rejects_a_malformed_view_id_before_claiming_a_row(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path, datasets=_stub_datasets())
    with pytest.raises(AppError) as exc:
        service.submit_export({
            "run_id": "run_1", "indices": [0], "format": "indices",
            "output_path": str(tmp_path / "x.txt"), "view_id": "   ",
        })
    assert exc.value.code == INVALID_PARAMS
    assert jobs.calls == 0
    assert service.db.query_one("SELECT COUNT(*) AS n FROM analysis_runs")["n"] == 0
    db.close()


def test_sensitivity_requires_same_descriptor_before_enqueue(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    result_dir = tmp_path / "results" / "run_2"
    result_dir.mkdir(parents=True)
    values = np.arange(96, dtype=np.float32).reshape(12, 8)
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_2", "level": "structure", "row_semantics": "structure", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_2', 'ds_1', 'ACSF', 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )

    with pytest.raises(AppError, match="same descriptor") as exc:
        service.sensitivity({"run_ids": ["run_1", "run_2"]})

    assert exc.value.code == ANALYSIS_INPUT_INVALID
    assert jobs.calls == 0
    db.close()


def test_a_multi_run_submission_probes_its_dataset_once(tmp_path: Path) -> None:
    # Three sensitivity inputs share one dataset. Verifying the fingerprint per
    # run meant walking the source and hashing a sampled 32 MB three times on
    # the RPC thread before a single sample was loaded, and a compare of two
    # runs of the same dataset always paid for it twice.
    probes: list[str] = []
    datasets = SimpleNamespace(
        adapter_for=lambda row: list(range(12)),
        refresh_if_changed=lambda row: probes.append(str(row["id"])),
    )
    db, jobs, service = _service(tmp_path, datasets=datasets)
    _dataset_and_view(db, list(range(12)))
    _insert_compatible_run(db, tmp_path, run_id="run_2")
    _insert_compatible_run(db, tmp_path, run_id="run_3")

    service.sensitivity({"run_ids": ["run_1", "run_2", "run_3"]})

    assert jobs.calls == 1
    assert probes == ["ds_1"], f"one submission should probe one dataset once: {probes}"
    db.close()


def test_cross_dataset_analysis_rejects_incompatible_feature_space_before_enqueue(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, file_size, created_at)"
        " VALUES ('ds_2', 'Query', 'extxyz', ?, 12, '[]', '{}', '{}', 'fp_2', 0, '2026-01-01T00:00:00+00:00')",
        (str(tmp_path / "query.extxyz"),),
    )
    result_dir = tmp_path / "results" / "run_2"
    result_dir.mkdir(parents=True)
    values = np.arange(96, dtype=np.float32).reshape(12, 8)
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_2", "level": "structure", "row_semantics": "structure", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_2', 'ds_2', 'ACSF', 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )

    with pytest.raises(AppError, match="same descriptor feature space") as exc:
        service.coverage({"reference_run_id": "run_1", "query_run_id": "run_2"})

    assert exc.value.code == ANALYSIS_INPUT_INVALID
    assert jobs.calls == 0
    db.close()


def test_cross_dataset_analysis_applies_views_and_persists_selection_identity(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, file_size, created_at)"
        " VALUES ('ds_1', 'Reference', 'extxyz', ?, 12, '[]', '{}', '{}', 'fp_1', 0, '2026-01-01T00:00:00+00:00')",
        (str(tmp_path / "reference.extxyz"),),
    )
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, file_size, created_at)"
        " VALUES ('ds_2', 'Query', 'extxyz', ?, 12, '[]', '{}', '{}', 'fp_2', 0, '2026-01-01T00:00:00+00:00')",
        (str(tmp_path / "query.extxyz"),),
    )
    result_dir = tmp_path / "results" / "run_2"
    result_dir.mkdir(parents=True)
    values = (np.arange(96, dtype=np.float32).reshape(12, 8) + 0.5)
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_2", "level": "structure", "row_semantics": "structure", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_2', 'ds_2', 'SOAP', 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )
    for view_id, dataset_id, fingerprint, indices, selection_hash in (
        ("view_ref", "ds_1", "fp_1", [0, 1, 2, 3, 4, 5], "selection_ref"),
        ("view_query", "ds_2", "fp_2", [6, 7, 8, 9, 10, 11], "selection_query"),
    ):
        db.execute(
            "INSERT INTO dataset_views (id, dataset_id, name, role, filter_json, frame_indices_json, selection_hash, dataset_fingerprint, created_at, updated_at)"
            " VALUES (?, ?, ?, 'filtered', '{}', ?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (view_id, dataset_id, view_id, json.dumps(indices), selection_hash, fingerprint),
        )

    submitted = service.coverage(
        {
            "reference_run_id": "run_1",
            "query_run_id": "run_2",
            "reference_view_id": "view_ref",
            "query_view_id": "view_query",
            "mode": "structure",
        }
    )

    assert submitted["job_id"] == "job_1"
    row = db.query_one("SELECT params_json FROM analysis_runs WHERE id = ?", (submitted["analysis_id"],))
    saved = json.loads(row["params_json"])
    assert saved["reference_selection_hash"] == "selection_ref"
    assert saved["query_selection_hash"] == "selection_query"
    preview = service.preview({"analysis_id": submitted["analysis_id"], "limit": 20})
    assert {item["frame"] for item in preview["rows"]} == set(range(6, 12))
    db.close()


def test_single_run_analysis_scopes_to_dataset_view(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, file_size, created_at)"
        " VALUES ('ds_1', 'Reference', 'extxyz', ?, 12, '[]', '{}', '{}', 'fp_1', 0, '2026-01-01T00:00:00+00:00')",
        (str(tmp_path / "reference.extxyz"),),
    )
    db.execute(
        "INSERT INTO dataset_views (id, dataset_id, name, role, filter_json, frame_indices_json, selection_hash, dataset_fingerprint, created_at, updated_at)"
        " VALUES ('view_v1', 'ds_1', 'v1', 'filtered', '{}', ?, 'selection_v1', 'fp_1', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        (json.dumps([2, 4, 6, 8]),),
    )

    submitted = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42, "view_id": "view_v1"})

    row = db.query_one("SELECT params_json FROM analysis_runs WHERE id = ?", (submitted["analysis_id"],))
    saved = json.loads(row["params_json"])
    assert saved["view_id"] == "view_v1"
    assert saved["selection_hash"] == "selection_v1"
    preview = service.preview({"analysis_id": submitted["analysis_id"], "limit": 20})
    # A clustering result carries coordinates, so its table reads `points`: the
    # `rows` copy this used to assert on is the duplicate that merge removed.
    assert {item["frame"] for item in preview["points"]} == {2, 4, 6, 8}

    # The selection hash joins the cache key: the identical request reuses the
    # artifact, a different view computes fresh.
    cached = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42, "view_id": "view_v1"})
    assert cached["job_id"] is None
    assert cached["cache"]["existing_analysis_id"] == submitted["analysis_id"]
    db.execute(
        "INSERT INTO dataset_views (id, dataset_id, name, role, filter_json, frame_indices_json, selection_hash, dataset_fingerprint, created_at, updated_at)"
        " VALUES ('view_v2', 'ds_1', 'v2', 'filtered', '{}', ?, 'selection_v2', 'fp_1', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        (json.dumps([1, 3, 5]),),
    )
    other = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42, "view_id": "view_v2"})
    assert other["analysis_id"] != submitted["analysis_id"]
    assert jobs.calls == 2

    # A stale view fingerprint must be rejected before enqueue.
    db.execute(
        "INSERT INTO dataset_views (id, dataset_id, name, role, filter_json, frame_indices_json, selection_hash, dataset_fingerprint, created_at, updated_at)"
        " VALUES ('view_stale', 'ds_1', 'stale', 'filtered', '{}', ?, 'selection_stale', 'fp_old', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        (json.dumps([0, 1]),),
    )
    with pytest.raises(AppError, match="is stale"):
        service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42, "view_id": "view_stale"})
    db.close()


def test_single_run_view_id_rejected_for_two_run_analyses(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    result_dir = tmp_path / "results" / "run_2"
    result_dir.mkdir(parents=True)
    values = np.arange(96, dtype=np.float32).reshape(12, 8)
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_2", "level": "structure", "row_semantics": "structure", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_2', 'ds_1', 'ACSF', 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )

    with pytest.raises(AppError, match="view_id applies to single-run analyses only"):
        service.compare({"left_run_id": "run_1", "right_run_id": "run_2", "mode": "structure", "view_id": "view_v1"})

    assert jobs.calls == 0
    db.close()


def test_dataset_view_slice_preserves_original_frame_identity() -> None:
    samples = AtomDescriptorMatrix(
        values=np.arange(20, dtype=np.float64).reshape(5, 4),
        frame=np.asarray([0, 1, 1, 2, 4]),
        row=np.asarray([0, 0, 1, 0, 0]),
        sample_ids=["f0:r0", "f1:r0", "f1:r1", "f2:r0", "f4:r0"],
        elements=np.asarray([1, 6, 8, 14, 32]),
        properties={"energy": np.asarray([0.0, 1.0, 1.1, 2.0, 4.0])},
        positions=np.arange(15, dtype=np.float64).reshape(5, 3),
        cells=np.repeat(np.eye(3)[None, :, :], 5, axis=0),
        pbc=np.ones((5, 3), dtype=bool),
    )

    sliced = AnalysisService._slice_samples_to_frames(samples, [1, 4])

    assert sliced.frame.tolist() == [1, 1, 4]
    assert sliced.row.tolist() == [0, 1, 0]
    assert sliced.sample_ids == ["f1:r0", "f1:r1", "f4:r0"]
    assert sliced.values.shape == (3, 4)
    assert sliced.properties["energy"].tolist() == [1.0, 1.1, 4.0]
    assert sliced.positions.shape == (3, 3)


def test_generic_analysis_is_cached_and_chunked(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    first = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 3, "seed": 42})
    assert first["job_id"] == "job_1"
    assert jobs.calls == 1

    row = db.query_one("SELECT * FROM analysis_runs WHERE id = ?", (first["analysis_id"],))
    assert row["status"] == "COMPLETED"
    assert row["cache_key"]
    manifest = json.loads((Path(row["result_path"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["completed"] is True
    assert (Path(row["result_path"]) / "labels.npy").is_file()

    cached = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 3, "seed": 42})
    assert cached["job_id"] is None
    assert cached["analysis_id"] == first["analysis_id"]
    assert jobs.calls == 1

    preview = service.preview({"analysis_id": first["analysis_id"], "limit": 3})
    assert len(preview["points"]) == 3
    chunk = service.chunk({"analysis_id": first["analysis_id"], "array": "labels", "limit": 4})
    assert chunk["data"] and chunk["next_offset"] == 4
    # Four of twelve rows. A one-dimensional array cut at the row limit used to
    # answer `truncated: false`, because only the column window set the flag - so
    # a panel that fetches one chunk and stopped drew that slice as the whole
    # population (deep review pass 4, E-2). `shape` carries the total to compare.
    assert chunk["shape"] == [12] and chunk["truncated"] is True
    whole = service.chunk({"analysis_id": first["analysis_id"], "array": "labels", "limit": 12})
    assert whole["next_offset"] == 12 and whole["truncated"] is False

    listed = service.list({"analysis_type": "kmeans"})
    assert listed[0]["id"] == first["analysis_id"]
    assert listed[0]["parameters"]["n_clusters"] == 3
    assert "preview_json" not in listed[0] and "preview" not in listed[0]
    db.close()


def test_list_columns_keep_up_with_the_analysis_runs_schema(tmp_path: Path) -> None:
    """_LIST_COLUMNS exists to leave the preview_json blob behind. It must not
    also fall behind a migration that adds a column, which would drop that
    column from the history endpoint in silence."""
    db = Database(tmp_path / "database.sqlite")
    try:
        columns = {row["name"] for row in db.query("PRAGMA table_info(analysis_runs)")}
        assert columns - {"preview_json"} == set(_LIST_COLUMNS)
    finally:
        db.close()


def test_feature_variance_persists_full_schema_and_invalid_warning(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    values = np.arange(96, dtype=np.float64).reshape(12, 8)
    values[1, 0] = np.nan
    values[3, 4] = np.inf
    np.save(tmp_path / "results" / "run_1" / "values.npy", values)

    response = service.feature_variance({"run_id": "run_1", "top_k": 4, "near_zero_relative_threshold": 0.002, "low_variance_relative_threshold": 0.02})
    assert response["job_id"] == "job_1"
    analysis = service.get({"analysis_id": response["analysis_id"]})
    assert analysis["parameters"]["feature_variance_schema"] == 2
    assert analysis["parameters"]["near_zero_relative_threshold"] == 0.002

    preview = service.preview({"analysis_id": response["analysis_id"], "limit": 20})
    assert preview["schema_version"] == 2
    assert preview["settings"]["low_variance_relative_threshold"] == 0.02
    assert len(preview["features"]) == 8
    assert "rows" not in preview
    assert preview["features"][0]["invalid_count"] == 1
    assert preview["features"][4]["invalid_count"] == 1
    assert any("non-finite" in warning for warning in preview["warnings"])

    chunk = service.chunk({"analysis_id": response["analysis_id"], "array": "histogram_counts", "offset": 4, "limit": 1})
    assert chunk["data"] and len(chunk["data"][0]) == 32
    db.close()


def test_stale_artifacts_remain_auditable_but_cannot_feed_new_work(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    created = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42})
    assert jobs.calls == 1

    DatasetService(db, adapter=None, jobs=jobs)._mark_runs_stale("ds_1", "fixture source changed")
    run = db.query_one("SELECT status FROM descriptor_runs WHERE id = 'run_1'")
    analysis = db.query_one("SELECT status FROM analysis_runs WHERE id = ?", (created["analysis_id"],))
    assert run["status"] == "STALE"
    assert analysis["status"] == "STALE"

    # Historical artifacts remain readable for audit, but a stale descriptor
    # cannot be used as the input of a new analysis or cache hit.
    assert service.get({"analysis_id": created["analysis_id"]})["status"] == "STALE"
    assert service.preview({"analysis_id": created["analysis_id"], "limit": 2})["points"]
    try:
        service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42})
    except AppError as exc:
        assert exc.code == ANALYSIS_STALE
    else:
        raise AssertionError("stale descriptor run unexpectedly accepted")
    db.close()


def test_incomplete_artifact_is_not_returned_as_a_cache_hit(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    created = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42})
    row = db.query_one("SELECT result_path FROM analysis_runs WHERE id = ?", (created["analysis_id"],))
    (Path(row["result_path"]) / "manifest.json").unlink()

    recreated = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42})
    assert recreated["job_id"] == "job_2"
    assert recreated["analysis_id"] != created["analysis_id"]
    db.close()


def test_coverage_preview_and_count_follow_query_samples(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    query_dir = tmp_path / "results" / "run_2"
    query_dir.mkdir(parents=True)
    query_values = np.arange(24, dtype=np.float32).reshape(3, 8)
    np.save(query_dir / "values.npy", query_values)
    (query_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_2", "level": "structure", "row_semantics": "structure", "shape": list(query_values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_2', 'ds_1', 'SOAP', 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:01+00:00', ?)",
        (str(query_dir),),
    )

    response = service.coverage({"reference_run_id": "run_1", "query_run_id": "run_2"})
    preview = service.preview({"analysis_id": response["analysis_id"], "limit": 20})
    assert len(preview["rows"]) == 3
    assert [row["sample_id"] for row in preview["rows"]] == ["frame:0", "frame:1", "frame:2"]
    db.close()


def test_generic_pca_cache_includes_preprocessing_mode(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    centered = service.pca({"run_id": "run_1", "preprocess": "center"})
    raw = service.pca({"run_id": "run_1", "preprocess": "raw"})
    assert centered["analysis_id"] != raw["analysis_id"]
    assert jobs.calls == 2
    cache_keys = db.query("SELECT cache_key FROM analysis_runs WHERE analysis_type = 'pca' ORDER BY id")
    assert all(row["cache_key"] for row in cache_keys)
    assert len({row["cache_key"] for row in cache_keys}) == 2
    raw_row = db.query_one("SELECT result_path FROM analysis_runs WHERE id = ?", (raw["analysis_id"],))
    raw_payload = json.loads((Path(raw_row["result_path"]) / "pca.json").read_text(encoding="utf-8"))
    assert raw_payload["preprocess"] == "raw"
    db.close()


def test_generic_pca_pads_the_second_coordinate_for_one_feature(tmp_path: Path) -> None:
    db, _jobs, service = _service(tmp_path)
    values = np.arange(4, dtype=np.float64).reshape(4, 1)
    result = pca(StructureDescriptorMatrix(values, np.arange(4), sample_ids=[f"frame:{i}" for i in range(4)]), {"preprocess": "center"})
    coords = result["arrays"]["coords"]
    explained = result["arrays"]["explained_variance"]
    assert coords.shape == (4, 2)
    assert explained.shape == (1,)
    assert np.allclose(coords[:, 1], 0.0)
    db.close()


def test_projection_preview_carries_one_list_for_scatter_and_table(tmp_path: Path, monkeypatch) -> None:
    """Past the preview cap the list strides across all points, and the result
    table reads that same list. This branch used to build a second one over the
    identical samples under the arrays' plural names, which doubled a capped
    preview (1.8 MB of the 3.9 MB a 20k-point cluster result costs) and made
    "the table and the scatter describe the same samples" something a test had
    to hold in place rather than something the shape guaranteed."""
    monkeypatch.setattr(preview_service, "_MAX_PREVIEW_POINTS", 3)
    db, _jobs, service = _service(tmp_path)
    samples = StructureDescriptorMatrix(
        np.arange(20, dtype=np.float64).reshape(10, 2),
        np.arange(10),
        sample_ids=[f"frame:{i}" for i in range(10)],
    )
    result = {
        "arrays": {"coords": np.arange(20, dtype=np.float64).reshape(10, 2), "labels": np.arange(10)},
        "preview": {"kind": "projection"},
    }
    preview = service._build_preview(result, samples, "pca")
    assert len(preview["points"]) == 3
    assert preview["total_points"] == 10
    assert "rows" not in preview and "total_rows" not in preview
    # everything the table showed is on the points it now reads, strided the
    # same way the scatter is: sample identity, coordinates, and the label.
    assert [(point["i"], point["x"], point["label"]) for point in preview["points"]] == [
        (0, 0.0, 0), (4, 8.0, 4), (9, 18.0, 9)
    ]
    db.close()


def test_frame_properties_reads_only_the_frames_the_preview_names(tmp_path: Path) -> None:
    # The colour-by merge used to pass `frame.max() + 1` as a count, so a view
    # selecting the last two frames of a long source decoded every frame before
    # them - and the extXYZ reader opens the source file once per frame.
    read: list[int] = []

    class _Adapter:
        def __len__(self) -> int:
            return 12

        def get_frame(self, index: int):
            read.append(index)
            return SimpleNamespace(
                numbers=np.arange(4), energy=-8.0, forces=np.zeros((4, 3)), cell=np.eye(3) * 5.0
            )

    class _Datasets:
        def adapter_for(self, _row):
            return _Adapter()

    db, _jobs, service = _service(tmp_path, datasets=_Datasets())
    _dataset_and_view(db, [10, 11])
    row = db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_1'")

    props = service._frame_properties_by_frame(row, [11, 10, 11])

    assert read == [10, 11], "one read per distinct frame the preview actually names"
    assert sorted(props) == [10, 11]
    assert props[10] == {"energy_per_atom": -2.0, "force_max": 0.0, "volume": 125.0}
    # Out of range and unreadable frames stay absent, so the merge reports them
    # as undecorated points rather than inventing a value.
    read.clear()
    assert service._frame_properties_by_frame(row, [99]) == {}
    assert read == []


def test_a_non_finite_frame_value_leaves_the_stored_preview_readable(tmp_path: Path) -> None:
    # One NaN force component used to be enough to make a COMPLETED result
    # permanently unreadable: the colour-by merge ran after the preview was
    # sanitised, `json.dumps` wrote a bare NaN token into preview_json, and every
    # later read then died at frames.encode(allow_nan=False).
    class _Adapter:
        def __len__(self) -> int:
            return 2

        def get_frame(self, index: int):
            forces = np.zeros((4, 3))
            if index == 1:
                forces[2, 0] = np.nan
            return SimpleNamespace(
                numbers=np.arange(4), energy=float("nan") if index == 1 else -8.0,
                forces=forces, cell=np.eye(3) * 5.0,
            )

    class _Datasets:
        def adapter_for(self, _row):
            return _Adapter()

    db, _jobs, service = _service(tmp_path, datasets=_Datasets())
    _dataset_and_view(db, [0, 1])
    row = db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_1'")

    props = service._frame_properties_by_frame(row, [0, 1])

    assert props[0]["force_max"] == 0.0 and props[0]["energy_per_atom"] == -2.0
    assert props[1]["force_max"] is None, "a frame with no finite maximum reports none"
    assert props[1]["energy_per_atom"] is None
    blob = json.dumps(service._json_safe({"points": [props[0], props[1]]}), ensure_ascii=False, allow_nan=False)
    assert "NaN" not in blob and "Infinity" not in blob
    frames.encode({"id": 1, "ok": True, "result": json.loads(blob)})
    db.close()


def test_similarity_preview_resolves_neighbor_indices_to_neighbor_identity(tmp_path: Path) -> None:
    db, _jobs, service = _service(tmp_path)
    values = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 2.0], [10.0, 0.0]])
    samples = StructureDescriptorMatrix(values, np.array([10, 11, 12, 13]), sample_ids=[f"frame:{i}" for i in [10, 11, 12, 13]])
    result = similarity(samples, {"query_index": 0, "k": 2, "metric": "euclidean"})
    preview = service._build_preview(result, samples, "similarity")
    assert [row["i"] for row in preview["rows"]] == [1, 2]
    assert [row["frame"] for row in preview["rows"]] == [11, 12]
    assert len(preview["rows"]) == 2
    db.close()


def test_neighbors_preview_keeps_source_and_neighbor_identity(tmp_path: Path) -> None:
    db, _jobs, service = _service(tmp_path)
    values = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 2.0]])
    samples = StructureDescriptorMatrix(values, np.array([20, 21, 22]), sample_ids=["frame:20", "frame:21", "frame:22"])
    result = neighbors(samples, {"k": 1, "metric": "euclidean"})
    preview = service._build_preview(result, samples, "neighbors")
    assert preview["total_rows"] == 3
    assert {row["source_i"] for row in preview["rows"]} == {0, 1, 2}
    assert all("distance" in row and "frame" in row for row in preview["rows"])
    db.close()


def test_frame_scoped_atom_identity_uses_the_actual_frame_index(tmp_path: Path) -> None:
    db, _jobs, service = _service(tmp_path)
    result_dir = tmp_path / "results" / "run_frame"
    result_dir.mkdir(parents=True)
    values = np.arange(6, dtype=np.float32).reshape(2, 3)
    np.save(result_dir / "values.npy", values)
    np.save(result_dir / "row_offsets.npy", np.array([0, 2], dtype=np.int64))
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_frame", "level": "atom", "row_semantics": "atom", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, frame_index, status, created_at, result_path) VALUES ('run_frame', 'ds_1', 'SOAP', 'test', '{}',"
        " 'frame', 7, 'COMPLETED', '2026-01-01T00:00:02+00:00', ?)",
        (str(result_dir),),
    )
    row = db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_frame'")
    samples = service._load_samples(row, {"mode": "atom"}, "similarity")
    assert samples.frame.tolist() == [7, 7]
    assert samples.row.tolist() == [0, 1]
    assert samples.sample_ids == ["frame:7:row:0", "frame:7:row:1"]

    structure_samples = service._load_samples(row, {"mode": "structure"}, "pca")
    assert structure_samples.frame.tolist() == [7]
    heatmap = service.results.heatmap({"run_id": "run_frame", "frame_index": 7})
    assert heatmap["values"] == values.tolist()
    db.close()


def test_heatmap_reply_stays_inside_the_protocol_value_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Columns are capped at 256 and rows are one structure's atoms, so the
    product is what decides whether a reply can be sent at all: an oversized
    frame used to come back as an encoder error rather than as a page."""
    from mdescriptor_studio_backend.services import result_service

    monkeypatch.setattr(result_service, "_MAX_CHUNK_VALUES", 6)
    db, _jobs, service = _service(tmp_path)
    result_dir = tmp_path / "results" / "run_wide"
    result_dir.mkdir(parents=True)
    values = np.arange(12, dtype=np.float32).reshape(4, 3)
    np.save(result_dir / "values.npy", values)
    np.save(result_dir / "row_offsets.npy", np.array([0, 4], dtype=np.int64))
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_wide", "level": "atom", "row_semantics": "atom", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, frame_index, status, created_at, result_path) VALUES ('run_wide', 'ds_1', 'SOAP', 'test', '{}',"
        " 'frame', 7, 'COMPLETED', '2026-01-01T00:00:02+00:00', ?)",
        (str(result_dir),),
    )

    reply = service.results.heatmap({"run_id": "run_wide", "frame_index": 7})

    assert sum(len(row) for row in reply["values"]) <= 6
    assert reply["truncated"] is True
    # The atoms listed are exactly the rows sent, so the page is self-describing.
    assert reply["atoms"] == [0, 1]
    assert reply["values"] == values[:2].tolist()
    db.close()


def test_frame_scoped_export_resolves_selected_sample_to_actual_frame(tmp_path: Path) -> None:
    db, _jobs, service = _service(tmp_path)
    result_dir = tmp_path / "results" / "run_export_frame"
    result_dir.mkdir(parents=True)
    values = np.array([[1.0, 2.0]])
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_export_frame", "level": "structure", "row_semantics": "structure", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, created_at)"
        " VALUES ('ds_export', 'export', 'deepmd', 'source', 8, '[]', '{}', '{}', 'fingerprint', '2026-01-01T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, frame_index, status, created_at, result_path) VALUES ('run_export_frame', 'ds_export', 'SOAP', 'test', '{}',"
        " 'frame', 7, 'COMPLETED', '2026-01-01T00:00:03+00:00', ?)",
        (str(result_dir),),
    )

    class _Adapter:
        def __len__(self):
            return 8

    class _Datasets:
        def adapter_for(self, _dataset):
            return _Adapter()

    service.datasets = _Datasets()
    run = db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_export_frame'")
    output, written = service._write_export(run, [0], "json", "structure", tmp_path / "export.json", _Context())
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert [record["frame"] for record in payload["records"]] == [7]
    assert [record["sample_index"] for record in payload["records"]] == [0]
    assert written == len(payload["records"]), "the count must be the file's, not the request's"
    db.close()


def test_export_reports_the_entries_it_wrote_not_the_selection_it_was_given(tmp_path: Path) -> None:
    # A selection can repeat an index, ask for samples outside the run, or name
    # several samples of one frame; every format folds those differently, and the
    # row used to record `len(selected)` regardless - so an export could claim
    # "500 selected" beside a file with 312 lines (deep review pass 4, C-11).
    db, jobs, service = _service(tmp_path)
    target = tmp_path / "indices.txt"

    path, written = service._write_export(
        db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_1'"), [2, 2, 7, 7, 7], "indices", "structure", target, _Context()
    )
    lines = path.read_text(encoding="utf-8").split()

    assert lines == ["2", "7"]
    assert written == len(lines), "the recorded count is what the file holds"
    db.close()


def test_identity_records_are_keyed_on_the_sample_not_frame_order() -> None:
    """`indices` exports sample indices, so the JSON/CSV sample_index must mean
    the same thing. Enumerating the deduped frame list relabelled every record
    and collapsed atom-level samples that share a frame."""
    samples = StructureDescriptorMatrix(
        np.zeros((4, 2)),
        np.array([10, 11, 12, 13]),
        sample_ids=[f"frame:{frame}" for frame in (10, 11, 12, 13)],
    )
    records = _identity_records(samples, [2, 3])
    assert [record["sample_index"] for record in records] == [2, 3]
    assert [record["frame"] for record in records] == [12, 13]

    atoms = StructureDescriptorMatrix(
        np.zeros((2, 2)),
        np.array([7, 7]),
        sample_ids=["frame:7:row:0", "frame:7:row:1"],
    )
    atoms_out = _identity_records(atoms, [0, 1])
    assert [record["sample_id"] for record in atoms_out] == ["frame:7:row:0", "frame:7:row:1"]
    assert [record["frame"] for record in atoms_out] == [7, 7]


def test_structural_perturbation_service_recomputes_descriptor_sweep(tmp_path: Path) -> None:
    db = Database(tmp_path / "database.sqlite")
    source_path = tmp_path / "source.xyz"
    source_path.write_text("fixture", encoding="utf-8")
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, created_at)"
        " VALUES ('ds_perturb', 'perturb', 'extxyz', ?, 4, '[\"H\"]', '{}', '{\"isolated\": true}', 'fixture', '2026-01-01T00:00:00+00:00')",
        (str(source_path),),
    )

    frames = []
    baseline_values = []
    for index in range(4):
        positions = np.array(
            [[float(index), 0.1 * index, 0.2], [float(index) + 0.5, 0.1 * index + 0.2, 0.4]],
            dtype=np.float64,
        )
        frames.append(
            DatasetFrame(
                numbers=np.array([1, 1], dtype=np.int64),
                positions=positions,
                cell=np.zeros((3, 3), dtype=np.float64),
                pbc=np.zeros(3, dtype=bool),
                index=index,
                id=f"frame_{index}",
            )
        )
        baseline_values.append([positions[:, 0].mean(), positions[:, 1].mean() + 2.0 * positions[:, 2].mean()])
    baseline_values = np.asarray(baseline_values, dtype=np.float64)
    result_dir = tmp_path / "results" / "run_perturb"
    result_dir.mkdir(parents=True)
    np.save(result_dir / "values.npy", baseline_values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_perturb", "level": "structure", "row_semantics": "structure", "shape": list(baseline_values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json, scope, status, created_at, result_path)"
        " VALUES ('run_perturb', 'ds_perturb', 'FAKE', 'test', '{}', 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )

    class _SourceAdapter:
        def __len__(self):
            return len(frames)

        def get_frame(self, index):
            return frames[index]

    class _ComputeAdapter:
        def build(self, _name, parameters, device="cpu"):
            return parameters

        @staticmethod
        def make_control():
            return object()

        @staticmethod
        def to_structure_batch(batch):
            return batch

        @staticmethod
        def compute(_descriptor, batch, _control=None):
            values = [
                [frame.positions[:, 0].mean(), frame.positions[:, 1].mean() + 2.0 * frame.positions[:, 2].mean()]
                for frame in batch
            ]
            return SimpleNamespace(values=np.asarray(values, dtype=np.float64), row_offsets=None)

    source_adapter = _SourceAdapter()

    class _Datasets:
        adapter = _ComputeAdapter()

        @staticmethod
        def adapter_for(_dataset):
            return source_adapter

    jobs = _InlineJobs(db)
    service = AnalysisService(db, jobs, ResultService(db), datasets=_Datasets(), data_dir=tmp_path)
    service._assert_dataset_current = lambda *_: None

    response = service.perturbation_sensitivity(
        {
            "run_id": "run_perturb",
            "perturbation": "jitter",
            "amplitudes": [0.0, 0.05, 0.1],
            "max_structures": 4,
            "seed": 42,
        }
    )
    assert response["job_id"] == "job_1"
    analysis = service.get({"analysis_id": response["analysis_id"]})
    assert analysis["status"] == "COMPLETED"
    assert analysis["preview"]["kind"] == "perturbation_sensitivity"
    assert analysis["preview"]["curve_count"] == 3
    curve = service.chunk({"analysis_id": response["analysis_id"], "array": "mean_response", "limit": 10})
    assert len(curve["data"]) == 3
    assert curve["data"][0] == pytest.approx(0.0)
    db.close()


def _insert_compatible_run(db: Database, tmp_path: Path, run_id: str = "run_2", descriptor_name: str = "SOAP") -> None:
    result_dir = tmp_path / "results" / run_id
    result_dir.mkdir(parents=True)
    values = (np.arange(96, dtype=np.float32).reshape(12, 8) + 0.5)
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": run_id, "level": "structure", "row_semantics": "structure", "shape": [12, 8]}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES (?, 'ds_1', ?, 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (run_id, descriptor_name, str(result_dir)),
    )


def test_fps_warm_start_rejects_mismatched_feature_space(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    _insert_compatible_run(db, tmp_path, descriptor_name="ACSF")

    with pytest.raises(AppError, match="same descriptor feature space") as exc:
        service.sampling({"run_id": "run_1", "algorithm": "fps", "existing_run_id": "run_2", "n_samples": 5})
    assert exc.value.code == ANALYSIS_INPUT_INVALID
    assert jobs.calls == 0
    db.close()


def test_warm_start_fps_scopes_its_candidate_view_before_enqueue(tmp_path: Path) -> None:
    # Warm-start FPS takes the cross-run branch, so a view_id used to skip both
    # the staleness check and the selection hash while the runner still sliced
    # the candidate set by that view: two views of the same length shared one
    # cache identity, and a stale view only failed minutes later inside the job.
    db, jobs, service = _service(tmp_path)
    _insert_compatible_run(db, tmp_path)
    _dataset_and_view(db, [2, 3, 4, 5, 6, 7])
    request = {
        "run_id": "run_1", "algorithm": "fps", "n_samples": 3, "scaling": "raw",
        "existing_run_id": "run_2", "mode": "structure", "view_id": "view_1",
    }

    submitted = service.sampling(dict(request))
    saved = json.loads(
        db.query_one("SELECT params_json FROM analysis_runs WHERE id = ?", (submitted["analysis_id"],))["params_json"]
    )
    assert saved["selection_hash"] == "fp_1|h1", "the view's content must decide cache reuse"
    assert jobs.calls == 1

    db.execute("UPDATE dataset_views SET dataset_fingerprint = 'fp_old' WHERE id = 'view_1'")
    with pytest.raises(AppError) as exc:
        service.sampling(dict(request))
    assert exc.value.code == ANALYSIS_STALE
    assert jobs.calls == 1, "a stale scope must be refused before a job exists"
    db.close()


def test_fps_warm_start_runs_and_exports_report_and_indices(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    _insert_compatible_run(db, tmp_path)

    submitted = service.sampling(
        {"run_id": "run_1", "algorithm": "fps", "n_samples": 5, "scaling": "raw", "existing_run_id": "run_2", "mode": "structure"}
    )
    assert submitted["job_id"] == "job_1"
    analysis_id = submitted["analysis_id"]

    preview = service.preview({"analysis_id": analysis_id, "limit": 20})
    assert preview["warm_start"] is True
    assert preview["scaling"] == "raw"
    saved_params = json.loads(db.query_one("SELECT params_json FROM analysis_runs WHERE id = ?", (analysis_id,))["params_json"])
    assert saved_params["existing_run_id"] == "run_2"
    assert preview["stop_reason"] == "target"
    assert preview["selected_count"] == 5
    assert preview["coverage_radius"] >= 0.0
    assert preview["sampling_dimension"] == 8

    curve = service.chunk({"analysis_id": analysis_id, "array": "coverage_radius_curve", "limit": 10})
    assert curve["shape"] == [5]
    assert len(curve["data"]) == 5

    report_path = tmp_path / "sampling_report.json"
    service.submit_export(
        {
            "run_id": "run_1",
            "indices": [0, 2, 4],
            "format": "report",
            "output_path": str(report_path),
            "analysis_id": analysis_id,
        }
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["algorithm"] == "farthest_point_sampling"
    assert report["strategy"] == "global"
    assert report["descriptor"] == "SOAP"
    assert report["scaling"] == "raw"
    assert report["warm_start"] is True
    assert report["existing_descriptor_run_id"] == "run_2"
    assert report["selected_samples"] == 3
    assert report["selected_sample_indices"] == [0, 2, 4]
    assert len(report["coverage_curve"]["coverage_radius"]) == 5

    indices_path = tmp_path / "selected_indices.txt"
    service.submit_export({"run_id": "run_1", "indices": [4, 0, 2], "format": "indices", "output_path": str(indices_path)})
    assert indices_path.read_text(encoding="utf-8").splitlines() == ["0", "2", "4"]
    db.close()


def _grouped_service(tmp_path: Path, frames: list[DatasetFrame]):
    """Service whose run_1 has 4 structure samples with distinct compositions."""
    db, jobs, service = _service(tmp_path)
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, file_size, created_at)"
        " VALUES ('ds_1', 'Grouped', 'deepmd', ?, ?, '[\"C\", \"Si\", \"O\"]', '{}', '{}', 'fp_1', 0, '2026-01-01T00:00:00+00:00')",
        (str(tmp_path / "grouped"), len(frames)),
    )
    result_dir = tmp_path / "results" / "run_grouped"
    result_dir.mkdir(parents=True)
    values = np.arange(32, dtype=np.float32).reshape(4, 8)
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_grouped", "level": "structure", "row_semantics": "structure", "shape": [4, 8]}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_grouped', 'ds_1', 'SOAP', 'test',"
        " '{\"species\": [6, 14]}', 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )

    class _Adapter:
        def __len__(self):
            return len(frames)

        def get_frame(self, index):
            return frames[index]

    class _Datasets:
        def adapter_for(self, _dataset):
            return _Adapter()

    service.datasets = _Datasets()
    # The fixture dataset has no real file behind it; skip the freshness probe.
    service._assert_dataset_current = lambda *_: None
    return db, jobs, service


def test_element_group_labels_use_shared_element_sets(tmp_path: Path) -> None:
    frames = [
        DatasetFrame(numbers=np.array([6, 6, 14, 14]), positions=np.zeros((4, 3)), cell=np.zeros((3, 3)), pbc=np.zeros(3, dtype=bool)),
        DatasetFrame(numbers=np.array([6, 6, 6]), positions=np.zeros((3, 3)), cell=np.zeros((3, 3)), pbc=np.zeros(3, dtype=bool)),
        DatasetFrame(numbers=np.array([14, 14]), positions=np.zeros((2, 3)), cell=np.zeros((3, 3)), pbc=np.zeros(3, dtype=bool)),
        DatasetFrame(numbers=np.array([6, 8, 14]), positions=np.zeros((3, 3)), cell=np.zeros((3, 3)), pbc=np.zeros(3, dtype=bool)),
    ]
    db, _jobs, service = _grouped_service(tmp_path, frames)
    run = db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_grouped'")
    samples = service._load_samples(run, {"mode": "structure"}, "fps")
    labels = service._element_group_labels(run, samples, ("", samples.n_samples))
    assert labels.tolist() == ["C-Si", "C", "Si", "C-O-Si"]

    quota = service.fps_quota({"run_id": "run_grouped", "mode": "structure", "n_samples": 4})
    assert [row["group"] for row in quota["groups"]] == ["C", "C-O-Si", "C-Si", "Si"]
    assert all(row["structures"] == 1 for row in quota["groups"])
    assert all(row["quota"] == 1 for row in quota["groups"])
    assert quota["n_candidates"] == 4
    db.close()


def test_the_group_label_cache_evicts_the_least_recently_used(tmp_path: Path) -> None:
    # The comment calls it a tiny LRU and the eviction popped `next(iter(...))`,
    # which is insertion order - so nine scopes later the labels a tenth call had
    # just reused were the first ones dropped. A hit now moves its entry to the
    # back, which is what makes dictionary order the recency order.
    frames = [
        DatasetFrame(numbers=np.array([6, 6, 14, 14]), positions=np.zeros((4, 3)), cell=np.zeros((3, 3)), pbc=np.zeros(3, dtype=bool)),
        DatasetFrame(numbers=np.array([6, 6, 6]), positions=np.zeros((3, 3)), cell=np.zeros((3, 3)), pbc=np.zeros(3, dtype=bool)),
        DatasetFrame(numbers=np.array([14, 14]), positions=np.zeros((2, 3)), cell=np.zeros((3, 3)), pbc=np.zeros(3, dtype=bool)),
        DatasetFrame(numbers=np.array([6, 8, 14]), positions=np.zeros((3, 3)), cell=np.zeros((3, 3)), pbc=np.zeros(3, dtype=bool)),
    ]
    db, _jobs, service = _grouped_service(tmp_path, frames)
    run = db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_grouped'")
    samples = service._load_samples(run, {"mode": "structure"}, "fps")
    cached_scopes = lambda: {entry[2] for entry in service._group_labels_cache}

    service._element_group_labels(run, samples, ("kept", samples.n_samples))
    for name in ("s0", "s1", "s2", "s3", "s4", "s5", "s6"):
        service._element_group_labels(run, samples, (name, samples.n_samples))
    assert len(cached_scopes()) == 8
    service._element_group_labels(run, samples, ("kept", samples.n_samples))
    service._element_group_labels(run, samples, ("s7", samples.n_samples))

    assert cached_scopes() == {"kept", "s1", "s2", "s3", "s4", "s5", "s6", "s7"}, cached_scopes()
    db.close()


def test_grouped_fps_run_persists_allocation_and_rejects_missing_metadata(tmp_path: Path) -> None:
    frames = [
        DatasetFrame(numbers=np.array([6, 6, 14, 14]), positions=np.zeros((4, 3)), cell=np.zeros((3, 3)), pbc=np.zeros(3, dtype=bool)),
        DatasetFrame(numbers=np.array([6, 6, 6]), positions=np.zeros((3, 3)), cell=np.zeros((3, 3)), pbc=np.zeros(3, dtype=bool)),
        DatasetFrame(numbers=np.array([14, 14]), positions=np.zeros((2, 3)), cell=np.zeros((3, 3)), pbc=np.zeros(3, dtype=bool)),
        DatasetFrame(numbers=np.array([6, 8, 14]), positions=np.zeros((3, 3)), cell=np.zeros((3, 3)), pbc=np.zeros(3, dtype=bool)),
    ]
    db, jobs, service = _grouped_service(tmp_path, frames)
    submitted = service.sampling(
        {"run_id": "run_grouped", "algorithm": "fps", "strategy": "grouped", "n_samples": 4, "scaling": "raw"}
    )
    assert submitted["job_id"] == "job_1"
    preview = service.preview({"analysis_id": submitted["analysis_id"], "limit": 20})
    assert preview["strategy"] == "grouped"
    allocation = preview["allocation"]
    assert {row["group"] for row in allocation} == {"C", "C-Si", "Si", "C-O-Si"}
    assert sum(row["quota"] for row in allocation) == 4
    assert preview["selected_count"] == 4

    # Without dataset access the groups cannot be resolved: the run must fail
    # loudly instead of silently degrading to a global selection.
    db2, jobs2, service2 = _service(tmp_path / "no-datasets")
    with pytest.raises(AppError, match="dataset access") as exc:
        service2.sampling({"run_id": "run_1", "algorithm": "fps", "strategy": "grouped", "n_samples": 3})
    assert exc.value.code == ANALYSIS_INPUT_INVALID
    # The failed submission leaves no analysis row behind.
    assert db2.query_one("SELECT id FROM analysis_runs WHERE analysis_type = 'fps'") is None
    assert jobs2.calls == 1
    db.close()
    db2.close()


def test_element_group_labels_use_central_atom_in_atom_mode(tmp_path: Path) -> None:
    db, _jobs, service = _service(tmp_path)
    samples = AtomDescriptorMatrix(
        np.arange(7 * 4, dtype=np.float64).reshape(7, 4),
        np.zeros(7, dtype=np.int64),
        elements=np.array([6, 6, 14, 14, 6, 8, 14], dtype=np.int64),
    )
    # Atom rows group by their central element, not by the frame composition.
    labels = service._element_group_labels({"id": "run_atoms", "dataset_id": "ds_1"}, samples, ("", 7))
    assert labels.tolist() == ["C", "C", "Si", "Si", "C", "O", "Si"]
    db.close()


def _composite_service(tmp_path: Path):
    """Service whose run_composite has lattice/energy/force metadata available."""
    db, jobs, service = _service(tmp_path)
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, file_size, created_at)"
        " VALUES ('ds_1', 'Composite', 'deepmd', ?, 3, '[\"C\", \"Si\"]', '{}', '{}', 'fp_1', 0, '2026-01-01T00:00:00+00:00')",
        (str(tmp_path / "composite"),),
    )
    result_dir = tmp_path / "results" / "run_composite"
    result_dir.mkdir(parents=True)
    values = np.arange(3 * 4, dtype=np.float32).reshape(3, 4)
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_composite", "level": "structure", "row_semantics": "structure", "shape": [3, 4]}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_composite', 'ds_1', 'ACE', 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )
    frames = [
        DatasetFrame(
            numbers=np.array([6, 6, 14]),
            positions=np.zeros((3, 3)),
            cell=np.diag([3.0 + index, 4.0, 5.0]),
            pbc=np.ones(3, dtype=bool),
            energy=-1.0 * index - 3.0,
            forces=np.full((3, 3), 0.1 * (index + 1)),
        )
        for index in range(3)
    ]

    class _Adapter:
        def __len__(self):
            return len(frames)

        def get_frame(self, index):
            return frames[index]

    class _Datasets:
        def adapter_for(self, _dataset):
            return _Adapter()

    service.datasets = _Datasets()
    service._assert_dataset_current = lambda *_: None
    return db, jobs, service


def test_composite_sampling_builds_requested_blocks_and_reports_layout(tmp_path: Path) -> None:
    db, jobs, service = _composite_service(tmp_path)
    submitted = service.sampling({
        "run_id": "run_composite",
        "algorithm": "fps",
        "n_samples": 3,
        "scaling": "robust",
        "blocks": ["descriptor", "lattice", "composition", "energy", "force"],
    })
    assert submitted["job_id"] == "job_1"
    preview = service.preview({"analysis_id": submitted["analysis_id"], "limit": 20})
    assert preview["sampling_space"] == "composite"
    blocks = {item["name"]: item for item in preview["blocks"]}
    assert blocks["descriptor"]["dimension"] == 4
    assert blocks["lattice"]["dimension"] == 6  # a, b, c, α, β, γ
    assert blocks["composition"]["dimension"] == 2  # C and Si fractions
    assert blocks["energy"]["dimension"] == 1
    assert blocks["force"]["dimension"] == 3  # mean, max, std
    assert preview["sampling_dimension"] == 16
    assert preview["selected_count"] == 3

    # The report carries the same provenance for later reproduction.
    report_path = tmp_path / "composite_report.json"
    service.submit_export({
        "run_id": "run_composite",
        "indices": [0, 1, 2],
        "format": "report",
        "output_path": str(report_path),
        "analysis_id": submitted["analysis_id"],
    })
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["sampling_space"] == "composite"
    assert {item["name"] for item in report["feature_blocks"]} == {"descriptor", "lattice", "composition", "energy", "force"}
    assert report["coverage"]["r2"] is not None
    db.close()


def test_a_settled_analysis_row_keeps_the_scale_it_was_claimed_with(tmp_path: Path) -> None:
    # analysis_runs.preprocessing_json is written twice: when the row is claimed
    # and when it settles. The settlement wrote a subset, so the row that outlives
    # the job said less than the row the job started from - and for composite FPS
    # `scaling` is not decoration, it is the space the samples were drawn from.
    db, jobs, service = _composite_service(tmp_path)
    submitted = service.sampling({
        "run_id": "run_composite",
        "algorithm": "fps",
        "n_samples": 3,
        "scaling": "robust",
        "blocks": ["descriptor", "lattice", "composition", "energy", "force"],
    })
    row = db.query_one("SELECT preprocessing_json FROM analysis_runs WHERE id = ?", (submitted["analysis_id"],))

    recorded = json.loads(row["preprocessing_json"])
    assert set(recorded) == {"preprocess", "scaling"}, recorded
    assert recorded["scaling"] == "robust", recorded
    db.close()


def test_composite_sampling_rejects_unknown_blocks_before_enqueue(tmp_path: Path) -> None:
    db, jobs, service = _composite_service(tmp_path)
    with pytest.raises(AppError, match="unknown sampling block") as exc:
        service.sampling({"run_id": "run_composite", "algorithm": "fps", "blocks": ["magic"]})
    assert exc.value.code == ANALYSIS_INPUT_INVALID
    # A physics-only space is a legitimate explicit choice, not an error.
    physics_only = service.sampling({"run_id": "run_composite", "algorithm": "fps", "n_samples": 2, "blocks": ["lattice"]})
    assert service.preview({"analysis_id": physics_only["analysis_id"], "limit": 5})["sampling_dimension"] == 6
    with pytest.raises(AppError, match="unknown sampling block"):
        service.sampling({"run_id": "run_composite", "algorithm": "fps", "blocks": ["lattice", "bogus"]})
    db.close()


def test_composite_sampling_requires_the_metadata_it_promises(tmp_path: Path) -> None:
    db, jobs, service = _composite_service(tmp_path)
    # An isolated frame carries no periodic cell: a requested lattice block
    # must fail loudly rather than being silently dropped.
    frames = [
        DatasetFrame(
            numbers=np.array([6]),
            positions=np.zeros((1, 3)),
            cell=np.zeros((3, 3)),
            pbc=np.zeros(3, dtype=bool),
            energy=-1.0,
        )
        for _ in range(3)
    ]

    class _Adapter:
        def __len__(self):
            return len(frames)

        def get_frame(self, index):
            return frames[index]

    class _Datasets:
        def adapter_for(self, _dataset):
            return _Adapter()

    service.datasets = _Datasets()
    with pytest.raises(AppError, match="lattice parameters") as exc:
        service.sampling({"run_id": "run_composite", "algorithm": "fps", "blocks": ["descriptor", "lattice"]})
    assert exc.value.code == ANALYSIS_INPUT_INVALID
    db.close()


def test_fps_quota_reports_composite_dimension(tmp_path: Path) -> None:
    db, _jobs, service = _composite_service(tmp_path)
    quota = service.fps_quota({"run_id": "run_composite", "mode": "structure", "n_samples": 3, "blocks": ["descriptor", "lattice"]})
    assert quota["requested_samples"] == 3
    assert sum(row["quota"] for row in quota["groups"]) == 3
    assert [item["name"] for item in quota["blocks"]] == ["descriptor", "lattice"]
    assert quota["sampling_dimension"] == 10
    db.close()


def test_coverage_target_run_reports_stop_reason(tmp_path: Path) -> None:
    db, _jobs, service = _service(tmp_path)
    submitted = service.sampling({
        "run_id": "run_1",
        "algorithm": "fps",
        "n_samples": 12,
        "scaling": "raw",
        "target_coverage": 0.9,
    })
    preview = service.preview({"analysis_id": submitted["analysis_id"], "limit": 20})
    assert preview["stop_reason"] in ("coverage", "target")
    assert preview["target_coverage"] == pytest.approx(0.9)
    assert 0.0 <= preview["coverage_r2"] <= 1.0
    curve = service.chunk({"analysis_id": submitted["analysis_id"], "array": "coverage_r2_curve", "limit": 20})
    assert curve["shape"][0] == preview["selected_count"]
    db.close()

def test_failed_settlement_cleans_committed_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure after commit must not leave an orphaned partial run."""
    db, _jobs, service = _service(tmp_path)

    def fail_settlement(*_args, **_kwargs):
        raise AppError("JOB_CANCELLED", "settlement interrupted for test")

    monkeypatch.setattr(service, "_complete_analysis_run", fail_settlement)
    with pytest.raises(AppError):
        service.pca({"run_id": "run_1", "mode": "structure", "preprocess": "center"})

    analysis_root = tmp_path / "analysis"
    assert analysis_root.is_dir()
    assert not list(analysis_root.glob("ana_*"))
    assert not list(analysis_root.glob(".ana_*.tmp-*"))
    db.close()


def test_artifact_rows_page_memory_mapped_arrays(tmp_path: Path) -> None:
    # The fallback row table pages over np.load(mmap_mode="r") arrays. It used to
    # index them element by element from Python, which turns every value into its
    # own disk-backed scalar read; the slicing version must keep the paging and
    # the ragged-length behaviour exactly.
    db, _jobs, service = _service(tmp_path)
    analysis_id = "ana_paging0001"
    root = tmp_path / "analysis" / analysis_id
    root.mkdir(parents=True)
    arrays = {
        "coords": np.arange(12, dtype=np.float32).reshape(6, 2),
        "labels": np.arange(6, dtype=np.int64),
        "scores": np.arange(4, dtype=np.float64),  # shorter on purpose
    }
    for name, array in arrays.items():
        np.save(root / f"{name}.npy", array, allow_pickle=False)
    row = {
        "id": analysis_id,
        "result_path": str(root),
        "analysis_type": "unbuilt_shape",
        "params_json": "{}",
        "input_run_ids_json": json.dumps(["run_missing"]),
        "descriptor_run_id": "run_missing",
        "artifact_manifest_json": json.dumps({"files": {name: {"path": f"{name}.npy"} for name in arrays}}),
    }

    page = service._rows_from_artifact(row, 2, 3)
    assert [entry["i"] for entry in page] == [2, 3, 4]
    assert page[0]["coords"] == [4.0, 5.0]
    assert page[0]["labels"] == 2
    assert all(isinstance(value, (int, float)) for value in page[0]["coords"])
    # rows past the shortest array omit it rather than padding a fake value
    assert "scores" in page[1] and "scores" not in page[2]
    assert service._rows_from_artifact(row, 5, 10) == [{"i": 5, "coords": [10.0, 11.0], "labels": 5}]
    db.close()


def test_column_window_keeps_a_chunk_encodable() -> None:
    from mdescriptor_studio_backend.services.analysis_helpers import _MAX_CHUNK_VALUES
    from mdescriptor_studio_backend.services.analysis_service import _column_window

    # The width a request asked for is kept while it fits one frame.
    assert _column_window(100, 0, 2_000, 400) == (0, 400, False)
    # A wide array with many rows cannot: the answer is a narrower window that
    # says so, not an unserialisable frame that fails the whole request.
    start, end, truncated = _column_window(20_000, 0, 2_000, 2_000)
    assert truncated is True
    assert 0 < end - start <= _MAX_CHUNK_VALUES // 20_000
    # Column paging continues from where the last window stopped.
    start, end, truncated = _column_window(10, 500, 1_000, 2_000)
    assert (start, end, truncated) == (500, 1_000, False)
    # Degenerate widths still return at least one column, so a page never
    # becomes empty and indistinguishable from "nothing left to read".
    assert _column_window(1_000_000, 0, 10, 10)[1] == 1


def test_an_unpageable_artifact_does_not_reload_the_descriptor_matrix(tmp_path: Path) -> None:
    # An artifact that stores no pageable array has no rows to page: every branch
    # of the preview builder is gated on one being present. Rebuilding anyway
    # read the whole descriptor matrix off disk to return [] — once per click on
    # a history row.
    db, _jobs, service = _service(tmp_path)
    analysis_id = "ana_unpageable1"
    root = tmp_path / "analysis" / analysis_id
    root.mkdir(parents=True)
    np.save(root / "correlation_matrix.npy", np.zeros((2, 2), dtype=np.float64))
    row = {
        "id": analysis_id,
        "result_path": str(root),
        "analysis_type": "feature_correlation",
        "params_json": "{}",
        "input_run_ids_json": json.dumps(["run_1"]),
        "descriptor_run_id": "run_1",
        "artifact_manifest_json": json.dumps({"files": {"correlation_matrix": {"path": "correlation_matrix.npy"}}}),
    }
    loads: list = []
    service._load_samples = lambda *args, **kwargs: loads.append(args)  # type: ignore[method-assign]

    assert service._rows_from_artifact(row, 0, 10) == []
    assert loads == []
    db.close()


def test_atom_level_run_without_offsets_refuses_structure_mode(tmp_path: Path) -> None:
    # Numbering an atom-level run's rows 0..n-1 as frames made every
    # structure-mode consumer — the reverse jump, the trajectory series, colour-by
    # — describe the wrong structure, silently. Atom mode already refuses the same
    # data, so structure mode must guess instead of reporting.
    db, _jobs, service = _service(tmp_path)
    result_dir = tmp_path / "results" / "run_atomish"
    result_dir.mkdir(parents=True)
    np.save(result_dir / "values.npy", np.arange(96, dtype=np.float32).reshape(12, 8))
    (result_dir / "metadata.json").write_text(
        json.dumps({
            "run_id": "run_atomish", "level": "atom", "row_semantics": "atom",
            "shape": [12, 8], "row_offsets_verified": False,
        }),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_atomish', 'ds_1', 'SOAP', 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )
    atom_run = db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_atomish'")

    with pytest.raises(AppError) as exc:
        service._load_samples(atom_run, {"mode": "structure"}, "fps")
    assert exc.value.code == RESULT_INCOMPATIBLE
    assert exc.value.details["row_offsets_verified"] is False

    # A genuinely structure-level run has nothing to fold and keeps its reading.
    flat = db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_1'")
    assert service._load_samples(flat, {"mode": "structure"}, "fps").n_samples == 12
    db.close()


def test_strain_perturbation_is_affine_about_the_cell_origin() -> None:
    """A strain must not slide a structure inside its own box.

    Cell and positions have to take the same affine map: that is what keeps
    every fractional coordinate -- and therefore the structure's place in the
    periodic box, which the descriptor sees -- unchanged by the perturbation.
    Scaling the positions about their centroid while the cell grew about the
    origin added a rigid translation that depended on where the origin sat.
    """
    cell = np.diag([10.0, 10.0, 10.0])
    positions = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]])
    frame = DatasetFrame(
        numbers=np.array([14, 14, 14]),
        positions=positions,
        cell=cell,
        pbc=np.ones(3, dtype=bool),
    )

    perturbed = AnalysisRunMixin._perturb_frame(frame, 0.1, "strain", np.zeros_like(positions))

    assert np.allclose(perturbed.cell, cell * 1.1)
    assert np.allclose(perturbed.positions, positions * 1.1)

    # Straining must preserve where each atom sits *in the box*, whatever that
    # box happens to contain: two copies that differ only by a translation
    # inside the cell strain into the same fractional arrangement they started
    # with, so the response curve measures the deformation and not the choice
    # of origin.
    shifted = DatasetFrame(
        numbers=frame.numbers,
        positions=positions + np.array([3.0, -2.0, 0.5]),
        cell=cell,
        pbc=frame.pbc,
    )
    for source in (frame, shifted):
        strained = AnalysisRunMixin._perturb_frame(source, 0.1, "strain", np.zeros_like(positions))
        assert np.allclose(
            strained.positions @ np.linalg.inv(strained.cell),
            source.positions @ np.linalg.inv(source.cell),
        )
