"""M4 acceptance: cooperative cancel takes effect (ADR-10 quantification)."""

import threading
import time
from pathlib import Path

import pytest
from make_fixtures import write_deepmd

from conftest import BackendProcess, wait_job
from mdescriptor_studio_backend.errors import AppError, JOB_CANCELLED
from mdescriptor_studio_backend.services.descriptor_service import DescriptorService
from mdescriptor_studio_backend.services.job_service import JobContext, JobService
from mdescriptor_studio_backend.storage.database import Database


def _insert_run(db: Database, run_id: str, status: str) -> None:
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
        " parameters_json, scope, status, created_at)"
        " VALUES (?, 'ds_1', 'ACE', '0.3.3', '{}', 'dataset', ?, '2026-01-01T00:00:00+00:00')",
        (run_id, status),
    )


def _bare_descriptor_service(db: Database) -> DescriptorService:
    service = object.__new__(DescriptorService)
    service.db = db
    return service


class _BuildFails:
    def build(self, *args, **kwargs):
        raise AppError(JOB_CANCELLED, "stop after the status flip")


def test_cancelled_compute_cannot_resurrect_its_run_row(tmp_path: Path) -> None:
    # cancel() settles the run row to CANCELLED before a runner stuck in a long
    # native call notices. An unguarded RUNNING write flips it back, and
    # _complete_run's status CAS then commits the cancelled compute as COMPLETED.
    db = Database(tmp_path / "database.sqlite")
    _insert_run(db, "run_c", "CANCELLED")
    ctx = JobContext(None, "job_c")
    ctx.cancel()
    with pytest.raises(AppError):
        _bare_descriptor_service(db)._run_compute(
            ctx, "run_c", {}, "ACE", {}, "dataset", None, "float64"
        )
    row = db.query_one("SELECT status, started_at FROM descriptor_runs WHERE id = 'run_c'")
    assert row["status"] == "CANCELLED"
    assert row["started_at"] is None
    db.close()


def test_queued_compute_still_marks_its_run_row_running(tmp_path: Path) -> None:
    db = Database(tmp_path / "database.sqlite")
    _insert_run(db, "run_q", "QUEUED")
    service = _bare_descriptor_service(db)
    service.adapter = _BuildFails()
    with pytest.raises(AppError):
        service._run_compute(
            JobContext(None, "job_q"), "run_q", {}, "ACE", {}, "dataset", None, "float64"
        )
    row = db.query_one("SELECT status, started_at FROM descriptor_runs WHERE id = 'run_q'")
    assert row["status"] == "RUNNING"
    assert row["started_at"] is not None
    db.close()


def test_shutdown_waits_for_a_runner_that_outlived_its_context(tmp_path: Path) -> None:
    # cancel() detaches and pops the context immediately, so a wait on the
    # context map returns while the runner is still inside the compute and
    # main.py then closes the database underneath it.
    db = Database(tmp_path / "database.sqlite")
    service = JobService(db, emit=lambda name, data: None)
    entered, release = threading.Event(), threading.Event()

    def runner(ctx):
        entered.set()
        assert release.wait(timeout=10)
        return {}

    job_id = service.submit("dataset.test", runner)
    assert entered.wait(timeout=10)
    service.cancel(job_id)
    with service._lock:
        assert service._active == {job_id}, "shutdown would stop waiting too early"
    release.set()
    service.shutdown(wait_seconds=5)
    with service._lock:
        assert service._active == set()
    db.close()


def test_cancel_descriptor_compute(tmp_path: Path) -> None:
    ds_dir = tmp_path / "gaas"
    write_deepmd(ds_dir, 1200, 64, seed=51)  # big enough that compute is interruptible
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        resp = bp.request(10, "dataset.register", {"path": str(ds_dir), "name": "g"})
        done = wait_job(bp, resp["result"]["job_id"], timeout=120)
        assert done["status"] == "COMPLETED"
        ds_id = done["result"]["dataset_id"]

        sub = bp.request(
            11,
            "descriptor.submit",
            {
                "dataset_id": ds_id,
                "descriptor_name": "ACE",
                "parameters": {"species": [31, 33], "N": 2},
                "scope": "dataset",
            },
        )
        job_id = sub["result"]["job_id"]
        # let it enter RUNNING, then cancel
        deadline = time.monotonic() + 30
        started = False
        while time.monotonic() < deadline:
            row = bp.request(12, "job.get", {"id": job_id})
            if row["result"]["status"] == "RUNNING":
                started = True
                break
            time.sleep(0.05)
        assert started, "job never started"
        cancel = bp.request(13, "job.cancel", {"id": job_id})
        assert cancel["result"]["ok"] is True

        # terminal state must be CANCELLED (engine stops at next cooperative checkpoint)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            row = bp.request(14, "job.get", {"id": job_id})
            if row["result"]["status"] in ("CANCELLED", "COMPLETED", "FAILED"):
                break
            time.sleep(0.1)
        assert row["result"]["status"] == "CANCELLED", row["result"]
        run = bp.request(15, "job.get", {"id": job_id})
        assert run["result"]["status"] == "CANCELLED"
    finally:
        assert bp.close() == 0
