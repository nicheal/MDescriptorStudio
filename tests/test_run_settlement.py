"""Run-row settlement: a job that fails or is cancelled must settle its linked
run rows (descriptor_runs / analysis_runs), and a backend restart must sweep
run rows left non-terminal by the previous session.

User-reported symptom: Jobs page shows FAILED while Results page keeps the run
at RUNNING forever, because only the COMPLETED path ever updated the run row.
"""

import time
from pathlib import Path

import pytest

from mdescriptor_studio_backend.services.analysis_service import AnalysisService
from mdescriptor_studio_backend.services.descriptor_service import DescriptorService
from mdescriptor_studio_backend.services.job_service import JobService
from mdescriptor_studio_backend.services.result_service import ResultService
from mdescriptor_studio_backend.storage.database import Database
from mdescriptor_studio_backend.errors import JOB_CANCELLED, RESULT_INCOMPATIBLE, AppError

_TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")


class _BuildBoomAdapter:
    """Descriptor adapter whose engine build always crashes inside the job."""

    def schema(self, name):
        return {"name": name, "parameters": {}}

    def runtime_info(self):
        return {"version": "test"}

    def build(self, name, parameters, device="cpu"):
        raise RuntimeError("engine boom")


def _frames_adapter(n=10**6):
    class _Frames:
        def __len__(self):
            return n

        def iter_frames(self):
            while True:
                time.sleep(0.01)
                yield object()

    return _Frames()


def _env(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements,"
        " properties, periodicity, fingerprint, file_size, created_at, last_scan_at)"
        " VALUES ('ds_1', 'd', 'deepmd', ?, 3, '[]', '{}', ?, 'fp', 0, '2026-01-01T00:00:00+00:00', NULL)",
        (str(tmp_path / "src.txt"), '{"isolated": true, "fully_periodic": false}'),
    )
    (tmp_path / "src.txt").write_text("x")
    jobs = JobService(db, emit=lambda *_args: None)
    svc = DescriptorService(
        db,
        adapter=_BuildBoomAdapter(),
        jobs=jobs,
        datasets=type("DS", (), {"_adapter_for": staticmethod(lambda row: _frames_adapter())})(),
        data_dir=tmp_path,
        engine_version="test",
    )
    return db, jobs, svc


def _wait_terminal(jobs: JobService, job_id: str, timeout=10.0) -> dict:
    deadline = time.monotonic() + timeout
    row = jobs.get_job(job_id)
    while time.monotonic() < deadline:
        row = jobs.get_job(job_id)
        if row and row["status"] in _TERMINAL:
            return row
        time.sleep(0.02)
    return row


def _submit_boom_run(svc: DescriptorService, jobs: JobService):
    sub = svc.submit(
        {"dataset_id": "ds_1", "descriptor_name": "ACE", "parameters": {}, "scope": "dataset"}
    )
    job_id = sub["job_id"]
    run_id = jobs.get_job(job_id)["descriptor_run_id"]
    return job_id, run_id


def test_failed_job_settles_descriptor_run(tmp_path: Path) -> None:
    db, jobs, svc = _env(tmp_path)
    job_id, run_id = _submit_boom_run(svc, jobs)

    done = _wait_terminal(jobs, job_id)
    assert done["status"] == "FAILED", done

    run = db.query_one("SELECT status, error_message FROM descriptor_runs WHERE id = ?", (run_id,))
    assert run["status"] == "FAILED", run  # user symptom: stays RUNNING without the fix
    assert run["error_message"], run
    jobs.shutdown()


def test_cancelled_job_settles_descriptor_run(tmp_path: Path) -> None:
    db, jobs, svc = _env(tmp_path)

    class _SlowBuildAdapter(_BuildBoomAdapter):
        def build(self, name, parameters, device="cpu"):
            return object()

    svc.adapter = _SlowBuildAdapter()
    job_id, run_id = _submit_boom_run(svc, jobs)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if jobs.get_job(job_id)["status"] == "RUNNING":
            break
        time.sleep(0.02)
    time.sleep(0.15)  # enter the frame-loading loop
    assert jobs.cancel(job_id)["ok"] is True

    done = _wait_terminal(jobs, job_id)
    assert done["status"] == "CANCELLED", done

    run = db.query_one("SELECT status FROM descriptor_runs WHERE id = ?", (run_id,))
    assert run["status"] == "CANCELLED", run
    jobs.shutdown()


def test_failed_analysis_settles_analysis_run(tmp_path: Path) -> None:
    db, jobs, _svc = _env(tmp_path)
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
        " parameters_json, scope, status, created_at)"
        " VALUES ('run_1', 'ds_1', 'ACE', 'test', '{}', 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00')"
    )
    analysis = AnalysisService(db, jobs, ResultService(db), datasets=None, data_dir=tmp_path)
    with pytest.raises(AppError) as exc:
        analysis.pca({"run_id": "run_1"})
    assert exc.value.code == RESULT_INCOMPATIBLE
    jobs.shutdown()


def test_restart_settles_zombie_runs(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
        " parameters_json, scope, status, created_at)"
        " VALUES ('run_z', 'ds_1', 'ACE', 'test', '{}', 'dataset', 'RUNNING', '2026-01-01T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO analysis_runs (id, descriptor_run_id, analysis_type, status, created_at)"
        " VALUES ('ana_z', 'run_z', 'pca', 'RUNNING', '2026-01-01T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO jobs (id, job_type, descriptor_run_id, analysis_run_id, status, progress, created_at)"
        " VALUES ('job_z', 'descriptor.compute', 'run_z', 'ana_z', 'RUNNING', 0, '2026-01-01T00:00:00+00:00')"
    )

    jobs = JobService(db, emit=lambda *_args: None)  # restart: constructor sweeps zombies
    assert jobs.get_job("job_z")["status"] == "CANCELLED"
    run = db.query_one("SELECT status FROM descriptor_runs WHERE id = 'run_z'")
    assert run["status"] == "CANCELLED", run
    ana = db.query_one("SELECT status FROM analysis_runs WHERE id = 'ana_z'")
    assert ana["status"] == "CANCELLED", ana
    jobs.shutdown()


def test_abandoned_run_directories_are_reclaimed_on_startup(tmp_path: Path) -> None:
    """A run that dies while writing leaves result_path NULL, so neither
    result.remove nor dataset.remove can ever reach the directory it created,
    and a hard exit mid-commit leaves an analysis staging directory behind."""
    db = Database(tmp_path / "db.sqlite3")
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
        " parameters_json, scope, status, created_at)"
        " VALUES ('run_dead', 'ds_1', 'ACE', 'test', '{}', 'dataset', 'FAILED', '2026-01-01T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
        " parameters_json, scope, status, created_at, result_path)"
        " VALUES ('run_live', 'ds_1', 'ACE', 'test', '{}', 'dataset', 'COMPLETED',"
        " '2026-01-01T00:00:00+00:00', ?)",
        (str(tmp_path / "results" / "run_live"),),
    )
    abandoned = tmp_path / "results" / "run_dead"
    abandoned.mkdir(parents=True)
    (abandoned / "values.npy").write_bytes(b"half a matrix")
    completed = tmp_path / "results" / "run_live"
    completed.mkdir(parents=True)
    (completed / "values.npy").write_bytes(b"whole matrix")
    staging = tmp_path / "analysis" / ".ana_x.tmp-01234567"
    staging.mkdir(parents=True)

    ResultService(db, tmp_path).sweep_abandoned()

    assert not abandoned.exists()
    assert not staging.exists()
    assert completed.exists() and (completed / "values.npy").is_file()
    db.close()


def test_cancelled_descriptor_cannot_complete(tmp_path: Path) -> None:
    db, jobs, svc = _env(tmp_path)
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
        " parameters_json, scope, status, created_at)"
        " VALUES ('run_cancelled', 'ds_1', 'ACE', 'test', '{}', 'dataset', 'CANCELLED', '2026-01-01T00:00:00+00:00')"
    )
    artifact = tmp_path / "results" / "run_cancelled"
    artifact.mkdir(parents=True)
    (artifact / "values.npy").write_bytes(b"stale")

    with pytest.raises(AppError) as exc:
        svc._complete_run("run_cancelled", artifact, None, {"shape": [1, 2], "feature_count": 2, "row_semantics": "structure"})

    assert exc.value.code == JOB_CANCELLED
    assert db.query_one("SELECT status FROM descriptor_runs WHERE id = 'run_cancelled'")["status"] == "CANCELLED"
    assert not artifact.exists()
    jobs.shutdown()


def test_cancelled_analysis_cannot_complete(tmp_path: Path) -> None:
    db, jobs, _svc = _env(tmp_path)
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
        " parameters_json, scope, status, created_at)"
        " VALUES ('run_analysis', 'ds_1', 'ACE', 'test', '{}', 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00')"
    )
    artifact = tmp_path / "analysis" / "ana_cancelled"
    artifact.mkdir(parents=True)
    db.execute(
        "INSERT INTO analysis_runs (id, descriptor_run_id, analysis_type, status, created_at, result_path)"
        " VALUES ('ana_cancelled', 'run_analysis', 'pca', 'CANCELLED', '2026-01-01T00:00:00+00:00', ?)",
        (str(artifact),),
    )
    analysis = AnalysisService(db, jobs, ResultService(db), datasets=None, data_dir=tmp_path)

    with pytest.raises(AppError) as exc:
        analysis._complete_analysis_run("ana_cancelled", {"result_path": str(artifact)}, artifact)

    assert exc.value.code == JOB_CANCELLED
    assert db.query_one("SELECT status FROM analysis_runs WHERE id = 'ana_cancelled'")["status"] == "CANCELLED"
    assert not artifact.exists()
    jobs.shutdown()
