"""Generic Analysis API storage, cache, and artifact lifecycle tests."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from mdescriptor_studio_backend.errors import ANALYSIS_STALE, AppError
from mdescriptor_studio_backend.services.analysis_service import AnalysisService
from mdescriptor_studio_backend.services.result_service import ResultService
from mdescriptor_studio_backend.storage.database import Database


class _Context:
    def check_cancelled(self):
        return None

    def progress(self, *args, **kwargs):
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


def _service(tmp_path: Path):
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
    return db, jobs, AnalysisService(db, jobs, ResultService(db), datasets=None, data_dir=tmp_path)


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
    assert len(preview["rows"]) == 3
    chunk = service.chunk({"analysis_id": first["analysis_id"], "array": "labels", "limit": 4})
    assert chunk["data"] and chunk["next_offset"] == 4

    listed = service.list({"analysis_type": "kmeans"})
    assert listed[0]["id"] == first["analysis_id"]
    assert listed[0]["parameters"]["n_clusters"] == 3
    db.close()


def test_stale_artifacts_remain_auditable_but_cannot_feed_new_work(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    created = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42})
    assert jobs.calls == 1

    service._mark_stale("ds_1", "fixture source changed")
    run = db.query_one("SELECT status FROM descriptor_runs WHERE id = 'run_1'")
    analysis = db.query_one("SELECT status FROM analysis_runs WHERE id = ?", (created["analysis_id"],))
    assert run["status"] == "STALE"
    assert analysis["status"] == "STALE"

    # Historical artifacts remain readable for audit, but a stale descriptor
    # cannot be used as the input of a new analysis or cache hit.
    assert service.get({"analysis_id": created["analysis_id"]})["status"] == "STALE"
    assert service.preview({"analysis_id": created["analysis_id"], "limit": 2})["rows"]
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
