"""M4 acceptance: cooperative cancel takes effect (ADR-10 quantification)."""

import json
import threading
import time
from pathlib import Path

import pytest
from make_fixtures import write_deepmd

from conftest import BackendProcess, wait_job
from mdescriptor_studio_backend.errors import AppError, JOB_CANCELLED
from mdescriptor_studio_backend.datasets.fingerprint import FINGERPRINT_VERSION
from mdescriptor_studio_backend.services.dataset_service import DatasetService
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


class _ScanJobs:
    """JobService stand-in: reports job rows, records submissions, runs nothing."""

    def __init__(self, rows: dict[str, dict]) -> None:
        self.rows = rows
        self.submitted: list[str] = []

    def get_job(self, job_id: str) -> dict | None:
        return self.rows.get(job_id)

    def submit(self, job_type: str, runner, **kwargs) -> str:
        self.submitted.append(job_type)
        return "job_fresh"


def _scan_service(db: Database, jobs) -> "DatasetService":
    service = object.__new__(DatasetService)
    service.db = db
    service.jobs = jobs
    service._scan_lock = threading.Lock()
    service._active_scans = {}
    service._adapters = {}
    return service


def _insert_dataset(db: Database, ds_id: str = "ds_scan") -> str:
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements,"
        " properties, periodicity, fingerprint, created_at)"
        " VALUES (?, ?, 'extxyz', ?, 1, '[]', '[]', '[]', ?, '2026-01-01T00:00:00+00:00')",
        (ds_id, ds_id, f"D:/data/{ds_id}.xyz", f"{FINGERPRINT_VERSION}:placeholder"),
    )
    return ds_id


def test_recompute_does_not_rediscover_a_settled_scan(tmp_path: Path) -> None:
    # _active_scans is cleared by the scan runner's own finally. A scan
    # cancelled while queued never reaches that runner (JobService._run skips
    # it), so the cached id can name a settled job: trusting it makes
    # statistics/findings/rescan wait on a dead job until the backend restarts.
    db = Database(tmp_path / "database.sqlite")
    ds_id = _insert_dataset(db)
    for status in ("CANCELLED", "FAILED", "COMPLETED"):
        jobs = _ScanJobs({f"job_{status}": {"id": f"job_{status}", "status": status}})
        service = _scan_service(db, jobs)
        service._active_scans[ds_id] = f"job_{status}"
        assert service._submit_recompute(ds_id) == "job_fresh", status
        assert service._active_scans[ds_id] == "job_fresh"
        assert jobs.submitted == ["dataset.statistics"]
    # A job row that is gone entirely (pruned) is equally untrustworthy.
    jobs = _ScanJobs({})
    service = _scan_service(db, jobs)
    service._active_scans[ds_id] = "job_gone"
    assert service._submit_recompute(ds_id) == "job_fresh"
    # ... while a scan still in flight must be reused, not resubmitted.
    for live in ("QUEUED", "RUNNING"):
        jobs = _ScanJobs({"job_live": {"id": "job_live", "status": live}})
        service = _scan_service(db, jobs)
        service._active_scans[ds_id] = "job_live"
        assert service._submit_recompute(ds_id) == "job_live"
        assert jobs.submitted == []
    db.close()


def test_scan_cancelled_while_queued_leaves_the_dataset_recomputable(tmp_path: Path) -> None:
    # The same failure through the real JobService: two blockers occupy the
    # 2-wide dataset pool so the scan is genuinely queued when it is cancelled.
    db = Database(tmp_path / "database.sqlite")
    ds_id = _insert_dataset(db)
    jobs = JobService(db, lambda name, data: None)
    release = threading.Event()
    try:
        blockers = [jobs.submit("dataset.statistics", lambda ctx: release.wait(10)) for _ in range(2)]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            states = [db.query_one("SELECT status FROM jobs WHERE id = ?", (jid,))["status"] for jid in blockers]
            if states == ["RUNNING", "RUNNING"]:
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"blockers never started: {states}")

        service = _scan_service(db, jobs)
        queued = service._submit_recompute(ds_id)
        assert db.query_one("SELECT status FROM jobs WHERE id = ?", (queued,))["status"] == "QUEUED"
        assert service._active_scans[ds_id] == queued
        jobs.cancel(queued)
        release.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if db.query_one("SELECT status FROM jobs WHERE id = ?", (blockers[0],))["status"] == "COMPLETED":
                break
            time.sleep(0.02)
        # The user-visible recovery: the health panel asks for statistics and
        # must not be handed the cancelled job id to wait on forever. (The
        # placeholder source path makes the stats cache miss, which is what
        # routes statistics() back through _submit_recompute.)
        result = service.statistics({"id": ds_id})
        assert result["recalculating"] is True
        assert result["job_id"] != queued
    finally:
        release.set()
        jobs.shutdown()
        db.close()


def test_cancelled_materialize_stops_mid_write_and_leaves_nothing_behind(tmp_path: Path) -> None:
    # Materialize writes through a lazily consumed generator. Without a
    # cooperative checkpoint in that generator the "cancelled" copy runs to
    # completion, and the half-written destination then blocks every retry:
    # _export_destination refuses a path that already exists.
    from make_fixtures import write_extxyz

    from mdescriptor_studio_backend.datasets import compute_fingerprint, create_adapter
    from mdescriptor_studio_backend.services.dataset_view_service import (
        _INSERT_VIEW,
        DatasetViewService,
        _discard_partial_output,
    )

    db = Database(tmp_path / "database.sqlite")
    source = tmp_path / "source.xyz"
    write_extxyz(source, n_frames=600, natoms=4)
    now = "2026-01-01T00:00:00+00:00"
    fingerprint = compute_fingerprint(source, 600, use_cache=False)
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements,"
        " properties, periodicity, fingerprint, created_at)"
        " VALUES ('ds_m', 'src', 'extxyz', ?, 600, '[]', '[]', '[]', ?, ?)",
        (str(source), fingerprint, now),
    )
    db.execute(
        _INSERT_VIEW,
        ("view_m", "ds_m", "all", "selection", "[]", json.dumps(list(range(600))), "h", fingerprint, now, now),
    )

    slow = _SlowAdapter(create_adapter(source, "extxyz"))
    jobs = JobService(db, lambda name, data: None)
    datasets = _ViewDatasets(db, jobs, slow)
    views = DatasetViewService(datasets)
    dest = tmp_path / "copy.xyz"
    try:
        submitted = views.materialize({"view_id": "view_m", "dest_path": str(dest)})
        job_id = submitted["job_id"]
        _wait_for(
            lambda: dest.exists() and jobs.get_job(job_id)["status"] == "RUNNING",
            "materialize never started copying",
        )
        jobs.cancel(job_id)
        _wait_for(lambda: jobs.get_job(job_id)["status"] in ("CANCELLED", "COMPLETED", "FAILED"), "job never settled")
        assert jobs.get_job(job_id)["status"] == "CANCELLED", "600 slow frames must not finish before the cancel"
        # cancel() settles the row up front; the runner notices at its next
        # frame and only then unwinds the writer, so the removal is what proves
        # the checkpoint exists rather than a race with this assert.
        _wait_for(lambda: not dest.exists(), "cancelled copy left its partial destination behind")

        # The same request now works instead of failing on the leftover path.
        again = views.materialize({"view_id": "view_m", "dest_path": str(dest)})
        _wait_for(lambda: jobs.get_job(again["job_id"])["status"] == "COMPLETED", "retry never completed")
        assert dest.exists()
    finally:
        jobs.shutdown()
        db.close()


def test_a_refused_materialize_destination_survives_the_failed_job(tmp_path: Path) -> None:
    """The refusal exists so a writer never truncates somebody else's file - and
    it must not delete that file either (pass 5, A-1).

    ``_refuse_filled_destination`` used to sit inside the ``try`` whose handler
    removes the destination, so the one case it was written for ran straight into
    the unlink/rmtree it was refusing for.
    """
    from make_fixtures import write_extxyz

    from mdescriptor_studio_backend.datasets import compute_fingerprint, create_adapter
    from mdescriptor_studio_backend.services.dataset_view_service import (
        _INSERT_VIEW,
        DatasetViewService,
    )

    class _CapturingJobs:
        def __init__(self) -> None:
            self.runner = None

        def submit(self, name, runner, **kwargs):
            self.runner = runner
            return "job_captured"

    class _Ctx:
        def progress(self, *args, **kwargs):
            pass

        def check_cancelled(self):
            pass

    db = Database(tmp_path / "database.sqlite")
    source = tmp_path / "source.xyz"
    write_extxyz(source, n_frames=3, natoms=4)
    now = "2026-01-01T00:00:00+00:00"
    fingerprint = compute_fingerprint(source, 3, use_cache=False)
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements,"
        " properties, periodicity, fingerprint, created_at)"
        " VALUES ('ds_m', 'src', 'extxyz', ?, 3, '[]', '[]', '[]', ?, ?)",
        (str(source), fingerprint, now),
    )
    db.execute(
        _INSERT_VIEW,
        ("view_m", "ds_m", "all", "selection", "[]", json.dumps([0, 1, 2]), "h", fingerprint, now, now),
    )
    jobs = _CapturingJobs()
    views = DatasetViewService(_ViewDatasets(db, jobs, create_adapter(source, "extxyz")))
    dest = tmp_path / "copy.xyz"
    try:
        views.materialize({"view_id": "view_m", "dest_path": str(dest)})
        assert dest.is_file() and dest.stat().st_size == 0, "the claim leaves an empty placeholder"
        # Between claim and write, the path stopped being ours.
        dest.write_text("user notes", encoding="utf-8")
        with pytest.raises(AppError, match="after it was claimed"):
            jobs.runner(_Ctx())
        assert dest.read_text(encoding="utf-8") == "user notes", "refusing must not delete what it refused"
    finally:
        db.close()


def test_partial_output_cleanup_covers_both_writers(tmp_path: Path) -> None:
    # extXYZ leaves a file, DeepMD a directory; either would poison a retry.
    from mdescriptor_studio_backend.services.dataset_view_service import _discard_partial_output

    leftover = tmp_path / "out.xyz"
    leftover.write_text("partial", encoding="utf-8")
    _discard_partial_output(leftover)
    assert not leftover.exists()
    directory = tmp_path / "deepmd"
    (directory / "set.000").mkdir(parents=True)
    (directory / "type.raw").write_text("H", encoding="utf-8")
    _discard_partial_output(directory)
    assert not directory.exists()
    _discard_partial_output(tmp_path / "never-created")  # must not raise


class _ViewDatasets:
    """The slice of DatasetService that DatasetViewService reaches into."""

    def __init__(self, db, jobs, adapter) -> None:
        self.db = db
        self.jobs = jobs
        self._adapter = adapter
        self._row_cache = db.query_one("SELECT * FROM datasets WHERE id = 'ds_m'")
        self.meta_calls = 0

    def row_or_raise(self, dataset_id):
        return self._row_cache

    def meta(self, row):
        self.meta_calls += 1
        return {"cache_valid": True}

    def adapter_for(self, row):
        return self._adapter


def test_view_list_resolves_each_dataset_once(tmp_path: Path) -> None:
    """DatasetViewService.meta() reaches through to the dataset fingerprint, which
    is a directory walk plus a 32 MB sample: listing views used to pay for it
    once per view, so the registry cost grew with how many views a dataset had."""
    from mdescriptor_studio_backend.services.dataset_view_service import _INSERT_VIEW, DatasetViewService

    db = Database(tmp_path / "database.sqlite")
    now = "2026-01-01T00:00:00+00:00"
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements,"
        " properties, periodicity, fingerprint, created_at)"
        " VALUES ('ds_m', 'src', 'extxyz', ?, 600, '[]', '[]', '[]', 'fp', ?)",
        (str(tmp_path / "source.xyz"), now),
    )
    for index in range(3):
        db.execute(
            _INSERT_VIEW,
            (f"view_{index}", "ds_m", f"v{index}", "selection", "[]", json.dumps([0, 1, 2]), "h", "fp", now, now),
        )

    datasets = _ViewDatasets(db, None, None)
    metas = DatasetViewService(datasets).list({})

    assert [meta["id"] for meta in metas] == ["view_0", "view_1", "view_2"]
    assert datasets.meta_calls == 1
    assert all(meta["stale"] is False for meta in metas)
    db.close()


class _SlowAdapter:
    """Delegates to a real adapter, slowly, so a cancel lands mid-copy."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def __len__(self) -> int:
        return len(self._inner)

    def get_frame(self, index):
        time.sleep(0.005)
        return self._inner.get_frame(index)

    def iter_frames(self):
        return self._inner.iter_frames()


def _wait_for(condition, message: str):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(0.02)
    pytest.fail(message)


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


def test_a_materialize_destination_is_claimed_not_checked(tmp_path: Path) -> None:
    # "Already exists is refused" was a check on the RPC thread and an O_TRUNC in
    # the job minutes later, so whatever the user - or a second materialization of
    # a different selection - put at that path in between was truncated anyway.
    # Claiming is now one atomic act, and the writer re-checks the placeholder
    # before it opens.
    from mdescriptor_studio_backend.services.dataset_view_service import (
        _refuse_filled_destination,
        _reserve_destination,
    )

    taken = tmp_path / "copy.xyz"
    taken.write_text("user notes", encoding="utf-8")
    with pytest.raises(AppError, match="already exists"):
        _reserve_destination(taken, False, "materialize")
    assert taken.read_text(encoding="utf-8") == "user notes", "refusing must not cost the user their file"

    fresh_file = tmp_path / "fresh.xyz"
    _reserve_destination(fresh_file, False, "materialize")
    assert fresh_file.is_file() and fresh_file.stat().st_size == 0, "an empty placeholder, not a truncation"
    _refuse_filled_destination(fresh_file, "materialize")  # still ours

    directory = tmp_path / "deepmd_out"
    _reserve_destination(directory, True, "materialize")
    _refuse_filled_destination(directory, "materialize")
    # Re-claiming our own empty placeholder is allowed: a job cancelled while it
    # was still queued never got to write, and its 0-byte claim must not poison
    # the path for every later attempt (pass 5, A-3).
    _reserve_destination(fresh_file, False, "materialize")
    _reserve_destination(directory, True, "materialize")
    (directory / "type.raw").write_text("H", encoding="utf-8")
    with pytest.raises(AppError, match="already exists"):
        _reserve_destination(directory, True, "materialize")

    # Between claim and write, the path stopped being ours: filled is a refusal,
    # while a deletion stays writable because no user data is at stake.
    fresh_file.write_text("someone else's export", encoding="utf-8")
    with pytest.raises(AppError, match="after it was claimed"):
        _refuse_filled_destination(fresh_file, "materialize")
    (directory / "type.raw").write_text("H", encoding="utf-8")
    with pytest.raises(AppError, match="after it was claimed"):
        _refuse_filled_destination(directory, "materialize")
    fresh_file.unlink()
    _refuse_filled_destination(fresh_file, "materialize")
