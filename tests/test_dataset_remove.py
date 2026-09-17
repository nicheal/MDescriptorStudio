"""Dataset deletion must respect active work and remove managed artifacts."""

import json
from pathlib import Path

import pytest

from mdescriptor_studio_backend.errors import AppError, DATASET_BUSY
from mdescriptor_studio_backend.services.dataset_service import DatasetService
from mdescriptor_studio_backend.services.job_service import JobService
from mdescriptor_studio_backend.storage.database import Database


def _dataset(db: Database, tmp_path: Path, dataset_id: str) -> None:
    source = tmp_path / f"{dataset_id}.xyz"
    source.write_text("source", encoding="utf-8")
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements,"
        " properties, periodicity, fingerprint, file_size, created_at, last_scan_at)"
        " VALUES (?, ?, 'extxyz', ?, 1, '[]', '{}', ?, 'fp', 0,"
        " '2026-01-01T00:00:00+00:00', NULL)",
        (dataset_id, dataset_id, str(source), '{"isolated": true, "fully_periodic": false}'),
    )


def test_dataset_remove_rejects_active_job(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    jobs = JobService(db, emit=lambda *_args: None)
    try:
        _dataset(db, tmp_path, "ds_busy")
        db.execute(
            "INSERT INTO jobs (id, job_type, dataset_id, status, progress, created_at)"
            " VALUES ('job_busy', 'dataset.statistics', 'ds_busy', 'RUNNING', 0, '2026-01-01T00:00:00+00:00')"
        )
        service = DatasetService(db, adapter=None, jobs=jobs, data_dir=tmp_path)

        with pytest.raises(AppError) as exc:
            service.remove({"id": "ds_busy"})

        assert exc.value.code == DATASET_BUSY
        assert db.query_one("SELECT id FROM datasets WHERE id = 'ds_busy'") is not None
    finally:
        jobs.shutdown()


def test_dataset_remove_cascades_secondary_input_and_artifacts(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    jobs = JobService(db, emit=lambda *_args: None)
    try:
        _dataset(db, tmp_path, "ds_one")
        _dataset(db, tmp_path, "ds_two")
        run_one = tmp_path / "results" / "run_one"
        run_two = tmp_path / "results" / "run_two"
        analysis = tmp_path / "analysis" / "ana_cross"
        for path in (run_one, run_two, analysis):
            path.mkdir(parents=True)
            (path / "artifact").write_text("x", encoding="utf-8")
        db.execute(
            "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
            " parameters_json, scope, status, created_at, result_path)"
            " VALUES ('run_one', 'ds_one', 'ACE', 'test', '{}', 'dataset', 'COMPLETED',"
            " '2026-01-01T00:00:00+00:00', ?)",
            (str(run_one),),
        )
        db.execute(
            "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
            " parameters_json, scope, status, created_at, result_path)"
            " VALUES ('run_two', 'ds_two', 'ACE', 'test', '{}', 'dataset', 'COMPLETED',"
            " '2026-01-01T00:00:00+00:00', ?)",
            (str(run_two),),
        )
        db.execute(
            "INSERT INTO analysis_runs (id, descriptor_run_id, analysis_type, status, created_at,"
            " input_run_ids_json, dataset_ids_json, result_path)"
            " VALUES ('ana_cross', 'run_two', 'coverage', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?, ?, ?)",
            (json.dumps(["run_two", "run_one"]), json.dumps(["ds_two", "ds_one"]), str(analysis)),
        )
        service = DatasetService(db, adapter=None, jobs=jobs, data_dir=tmp_path)

        assert service.remove({"id": "ds_one"}) == {"ok": True}
        assert db.query_one("SELECT id FROM datasets WHERE id = 'ds_one'") is None
        assert db.query_one("SELECT id FROM descriptor_runs WHERE id = 'run_one'") is None
        assert db.query_one("SELECT id FROM datasets WHERE id = 'ds_two'") is not None
        assert db.query_one("SELECT id FROM descriptor_runs WHERE id = 'run_two'") is not None
        assert db.query_one("SELECT id FROM analysis_runs WHERE id = 'ana_cross'") is None
        assert not run_one.exists()
        assert run_two.exists()
        assert not analysis.exists()
    finally:
        jobs.shutdown()
