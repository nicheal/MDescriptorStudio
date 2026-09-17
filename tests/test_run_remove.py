"""result.remove: deleting ONE run must cascade to its analysis runs and linked
jobs, remove the on-disk result/analysis dirs, and refuse non-terminal runs
(a live runner would keep writing into them — cancel the job first).
"""

import json
from pathlib import Path

import pytest

from mdescriptor_studio_backend.errors import AppError, INVALID_PARAMS, RESULT_INCOMPATIBLE
from mdescriptor_studio_backend.services.result_service import ResultService
from mdescriptor_studio_backend.storage.database import Database


def _env(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements,"
        " properties, periodicity, fingerprint, file_size, created_at, last_scan_at)"
        " VALUES ('ds_1', 'd', 'deepmd', ?, 3, '[]', '{}', ?, 'fp', 0, '2026-01-01T00:00:00+00:00', NULL)",
        (str(tmp_path / "src.txt"), '{"isolated": true, "fully_periodic": false}'),
    )
    (tmp_path / "src.txt").write_text("x")
    return db, ResultService(db)


def _insert_run(db: Database, tmp_path: Path, run_id: str, status: str) -> Path:
    run_dir = tmp_path / "results" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "values.npy").write_bytes(b"\x00\x01")
    (run_dir / "metadata.json").write_text("{}", encoding="utf-8")
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
        " parameters_json, scope, status, created_at, result_path)"
        " VALUES (?, 'ds_1', 'ACE', 'test', '{}', 'dataset', ?, '2026-01-01T00:00:00+00:00', ?)",
        (run_id, status, str(run_dir)),
    )
    return run_dir


def _insert_analysis(
    db: Database,
    tmp_path: Path,
    run_id: str,
    ana_id: str,
    status: str,
    input_run_ids: list[str] | None = None,
) -> Path:
    ana_dir = tmp_path / "analysis" / ana_id
    ana_dir.mkdir(parents=True)
    (ana_dir / "pca.json").write_text("{}", encoding="utf-8")
    db.execute(
        "INSERT INTO analysis_runs (id, descriptor_run_id, analysis_type, status, created_at, result_path)"
        " VALUES (?, ?, 'pca', ?, '2026-01-01T00:00:00+00:00', ?)",
        (ana_id, run_id, status, str(ana_dir)),
    )
    if input_run_ids is not None:
        db.execute(
            "UPDATE analysis_runs SET input_run_ids_json = ? WHERE id = ?",
            (json.dumps(input_run_ids), ana_id),
        )
    return ana_dir


def _insert_job(db: Database, job_id: str, *, run_id: str | None, ana_id: str | None) -> None:
    db.execute(
        "INSERT INTO jobs (id, job_type, descriptor_run_id, analysis_run_id, status, progress, created_at)"
        " VALUES (?, 'descriptor.compute', ?, ?, 'COMPLETED', 1, '2026-01-01T00:00:00+00:00')",
        (job_id, run_id, ana_id),
    )


def test_remove_completed_run_cascades_rows_and_dirs(tmp_path: Path) -> None:
    db, results = _env(tmp_path)
    run_dir = _insert_run(db, tmp_path, "run_1", "COMPLETED")
    ana_dir = _insert_analysis(db, tmp_path, "run_1", "ana_1", "COMPLETED")
    _insert_job(db, "job_run", run_id="run_1", ana_id=None)
    _insert_job(db, "job_ana", run_id=None, ana_id="ana_1")

    assert results.remove({"run_id": "run_1"}) == {"ok": True}

    assert db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_1'") is None
    assert db.query_one("SELECT * FROM analysis_runs WHERE descriptor_run_id = 'run_1'") is None
    assert db.query_one("SELECT * FROM jobs WHERE id IN ('job_run', 'job_ana')") is None
    assert not run_dir.exists()
    assert not ana_dir.exists()


def test_remove_refuses_non_terminal_run(tmp_path: Path) -> None:
    db, results = _env(tmp_path)
    run_dir = _insert_run(db, tmp_path, "run_r", "RUNNING")
    _insert_analysis(db, tmp_path, "run_r", "ana_r", "COMPLETED")
    _insert_job(db, "job_r", run_id="run_r", ana_id=None)

    with pytest.raises(AppError) as exc:
        results.remove({"run_id": "run_r"})
    assert exc.value.code == RESULT_INCOMPATIBLE

    # refusal is atomic: nothing was deleted
    assert db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_r'") is not None
    assert db.query_one("SELECT * FROM analysis_runs WHERE id = 'ana_r'") is not None
    assert db.query_one("SELECT * FROM jobs WHERE id = 'job_r'") is not None
    assert run_dir.exists()


def test_remove_unknown_run_raises(tmp_path: Path) -> None:
    _db, results = _env(tmp_path)
    with pytest.raises(AppError) as exc:
        results.remove({"run_id": "run_nope"})
    assert exc.value.code == INVALID_PARAMS


def test_remove_run_without_results_or_analyses(tmp_path: Path) -> None:
    """A FAILED run has no result_path and no analyses — delete must still work."""
    db, results = _env(tmp_path)
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
        " parameters_json, scope, status, created_at)"
        " VALUES ('run_f', 'ds_1', 'ACE', 'test', '{}', 'dataset', 'FAILED', '2026-01-01T00:00:00+00:00')"
    )

    assert results.remove({"run_id": "run_f"}) == {"ok": True}
    assert db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_f'") is None


def test_remove_secondary_input_run_cascades_analysis(tmp_path: Path) -> None:
    db, results = _env(tmp_path)
    _insert_run(db, tmp_path, "run_1", "COMPLETED")
    secondary_dir = _insert_run(db, tmp_path, "run_2", "COMPLETED")
    analysis_dir = _insert_analysis(
        db, tmp_path, "run_1", "ana_pair", "COMPLETED", ["run_1", "run_2"]
    )

    assert results.remove({"run_id": "run_2"}) == {"ok": True}
    assert db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_1'") is not None
    assert db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_2'") is None
    assert db.query_one("SELECT * FROM analysis_runs WHERE id = 'ana_pair'") is None
    assert not secondary_dir.exists()
    assert not analysis_dir.exists()
