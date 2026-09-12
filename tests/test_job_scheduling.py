"""Job scheduling: category pools, engine/update mutual exclusion, cooperative
shutdown, queue positions, and descriptor in-flight dedup (scheduling-fix-plan).

The engine pool is single-worker and shared by descriptor.compute and
engine.update; analysis and dataset pools are isolated so a blocked compute
cannot starve scans, and shutdown cancels live jobs before settling rows.
"""

import threading
import time
from pathlib import Path

from mdescriptor_studio_backend.errors import JOB_CANCELLED, AppError
from mdescriptor_studio_backend.services.descriptor_service import DescriptorService
from mdescriptor_studio_backend.services.job_service import JobService
from mdescriptor_studio_backend.storage.database import Database

_TERMINAL = ("COMPLETED", "FAILED", "CANCELLED")


def _env(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite3")
    jobs = JobService(db, emit=lambda *_args: None)
    return db, jobs


def _wait_status(jobs: JobService, job_id: str, status: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    row = jobs.get_job(job_id)
    while time.monotonic() < deadline:
        row = jobs.get_job(job_id)
        if row and row["status"] == status:
            return row
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached {status}: {row}")


def _wait_terminal(jobs: JobService, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    row = jobs.get_job(job_id)
    while time.monotonic() < deadline:
        row = jobs.get_job(job_id)
        if row and row["status"] in _TERMINAL:
            return row
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached a terminal state: {row}")


def test_engine_pool_does_not_block_dataset_jobs(tmp_path) -> None:
    db, jobs = _env(tmp_path)
    release = threading.Event()

    def blocker(ctx):
        release.wait(10)

    def quick(ctx):
        return "ok"

    heavy = jobs.submit("descriptor.compute", blocker)
    _wait_status(jobs, heavy, "RUNNING")

    started = time.monotonic()
    light = jobs.submit("dataset.statistics", quick)
    row = _wait_terminal(jobs, light, timeout=5)
    assert row["status"] == "COMPLETED"
    # the dataset pool is isolated: the light job must not wait for the engine
    assert time.monotonic() - started < 5

    release.set()
    _wait_terminal(jobs, heavy)
    jobs.shutdown()


def test_engine_update_waits_for_running_compute(tmp_path) -> None:
    db, jobs = _env(tmp_path)
    release = threading.Event()
    compute_started = threading.Event()

    def compute(ctx):
        compute_started.set()
        release.wait(10)

    compute_id = jobs.submit("descriptor.compute", compute)
    assert compute_started.wait(10)

    update_id = jobs.submit("engine.update", lambda ctx: "updated")
    time.sleep(0.2)
    # both job types share the single-worker engine pool: the pip update must
    # never run while a compute holds the engine's native extensions open
    assert jobs.get_job(update_id)["status"] == "QUEUED"

    release.set()
    _wait_terminal(jobs, compute_id)
    row = _wait_terminal(jobs, update_id)
    assert row["status"] == "COMPLETED"
    jobs.shutdown()


def test_shutdown_cancels_running_jobs(tmp_path) -> None:
    db, jobs = _env(tmp_path)
    saw_cancel = threading.Event()

    def runner(ctx):
        try:
            for _ in range(500):
                ctx.check_cancelled()
                time.sleep(0.01)
            return "done"
        except AppError as exc:
            if exc.code == JOB_CANCELLED:
                saw_cancel.set()
            raise

    job_id = jobs.submit("analysis.pca", runner)
    _wait_status(jobs, job_id, "RUNNING")

    jobs.shutdown(wait_seconds=5)
    # cooperative cancel reached the runner (not just a row sweep) — the
    # runner checks the flag at its next checkpoint (≤10ms later); shutdown()
    # must not close the db itself
    assert saw_cancel.wait(5.0)
    assert jobs.get_job(job_id)["status"] == "CANCELLED"


def test_queue_positions_per_category(tmp_path) -> None:
    db, jobs = _env(tmp_path)
    release = threading.Event()

    def blocker(ctx):
        release.wait(10)

    def noop(ctx):
        return "ok"

    running = jobs.submit("descriptor.compute", blocker)
    _wait_status(jobs, running, "RUNNING")
    first = jobs.submit("descriptor.compute", noop)
    second = jobs.submit("descriptor.compute", noop)
    other_pool = jobs.submit("dataset.export", noop)

    time.sleep(0.2)  # let the pool slots settle
    row_first = jobs.get_job(first)
    row_second = jobs.get_job(second)
    assert row_first["status"] == "QUEUED"
    assert row_first["queue_position"] == 1
    assert row_second["status"] == "QUEUED"
    assert row_second["queue_position"] == 2

    listed = {row["id"]: row for row in jobs.list_jobs({})}
    assert listed[first]["queue_position"] == 1
    assert listed[second]["queue_position"] == 2
    # running jobs carry no queue position
    assert "queue_position" not in listed[running]

    release.set()
    _wait_terminal(jobs, second)
    jobs.shutdown()


def _descriptor_env(tmp_path: Path, adapter) -> tuple[Database, JobService, DescriptorService]:
    db = Database(tmp_path / "db.sqlite3")
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements,"
        " properties, periodicity, fingerprint, file_size, created_at, last_scan_at)"
        " VALUES ('ds_1', 'd', 'deepmd', ?, 3, '[]', '{}', ?, 'fp', 0, '2026-01-01T00:00:00+00:00', NULL)",
        (str(tmp_path / "src.txt"), '{"isolated": true, "fully_periodic": false}'),
    )
    (tmp_path / "src.txt").write_text("x")
    jobs = JobService(db, emit=lambda *_args: None)

    class _Frames:
        def __len__(self):
            return 3

        def iter_frames(self):
            return iter([object(), object(), object()])

    svc = DescriptorService(
        db,
        adapter=adapter,
        jobs=jobs,
        datasets=type("DS", (), {"_adapter_for": staticmethod(lambda row: _Frames())})(),
        data_dir=tmp_path,
        engine_version="test",
    )
    return db, jobs, svc


def test_descriptor_submit_dedups_in_flight(tmp_path) -> None:
    release = threading.Event()
    build_started = threading.Event()

    class _SlowBuildAdapter:
        def schema(self, name):
            return {"name": name, "parameters": {}}

        def runtime_info(self):
            return {"version": "test"}

        def build(self, name, parameters, device="cpu"):
            build_started.set()
            release.wait(10)
            return object()

    _db, jobs, svc = _descriptor_env(tmp_path, _SlowBuildAdapter())
    params = {"dataset_id": "ds_1", "descriptor_name": "ACE", "parameters": {}, "scope": "dataset"}

    first = svc.submit(params)
    assert build_started.wait(10)

    second = svc.submit(params)
    # same configuration while the first compute is live: reuse its job, do
    # not run the engine twice
    assert second["job_id"] == first["job_id"]
    assert second["cache"]["existing_run_id"] == jobs.get_job(first["job_id"])["descriptor_run_id"]
    assert second["cache"]["in_flight"] is True

    forced = svc.submit({**params, "force": True})
    assert forced["job_id"] != first["job_id"]
    assert forced["cache"] is None

    release.set()
    jobs.shutdown()


def test_descriptor_submit_reuses_completed_cache(tmp_path) -> None:
    class _BoomAdapter:
        def schema(self, name):
            return {"name": name, "parameters": {}}

        def runtime_info(self):
            return {"version": "test"}

        def build(self, name, parameters, device="cpu"):
            raise RuntimeError("engine boom")

    db, jobs, svc = _descriptor_env(tmp_path, _BoomAdapter())
    params = {"dataset_id": "ds_1", "descriptor_name": "ACE", "parameters": {}, "scope": "dataset"}

    first = svc.submit(params)
    done = _wait_terminal(jobs, first["job_id"])
    assert done["status"] == "FAILED"  # boom adapter: run settles FAILED

    # only COMPLETED runs feed the completed-cache; a failed run must not
    # short-circuit a retry
    retry = svc.submit(params)
    assert retry["cache"] is None
    assert retry["job_id"] != first["job_id"]
    jobs.shutdown()
