"""Regression: DPA4C job stuck at 100% while RUNNING + run row stuck RUNNING
after cancel (user-reported 2026-08-29).

Symptom 1: the "loading frames" phase reports progress on the same 0-1 scale
the job bar uses and reaches 1.0 before compute even starts; the subsequent
"computing descriptor" reset is swallowed by the 1%/200ms throttle, so the bar
reads 100% for the whole (slow) engine compute.

Symptom 2: JobService marks the jobs row CANCELLED, but nothing updates the
descriptor_runs row on the cancel/failure path, so the Results page reads
status RUNNING forever.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from make_fixtures import write_deepmd  # noqa: E402

from test_backend_smoke import BackendProcess  # noqa: E402
from test_dataset_flow import wait_job  # noqa: E402


def _register(bp: BackendProcess, vid: int, path: Path) -> str:
    resp = bp.request(vid, "dataset.register", {"path": str(path), "name": path.name})
    # wide timeout: the suite may run alongside other heavy local jobs
    done = wait_job(bp, resp["result"]["job_id"], timeout=180)
    assert done["status"] == "COMPLETED", done
    return done["result"]["dataset_id"]


def _run_id_for_job(bp: BackendProcess, vid: int, job_id: str) -> str:
    row = bp.request(vid, "job.get", {"id": job_id})["result"]
    assert row["descriptor_run_id"], row
    return row["descriptor_run_id"]


def _run_status(bp: BackendProcess, vid: int, dataset_id: str, run_id: str) -> dict:
    rows = bp.request(vid, "result.list", {"dataset_id": dataset_id})["result"]
    return next(r for r in rows if r["id"] == run_id)


def test_cancel_marks_descriptor_run_cancelled(tmp_path: Path) -> None:
    """Cancelling a compute job must flip descriptor_runs off RUNNING
    (Results page reads this status)."""
    ds_dir = tmp_path / "gaas"
    write_deepmd(ds_dir, 1200, 64, seed=51)
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        ds_id = _register(bp, 10, ds_dir)

        sub = bp.request(
            11,
            "descriptor.submit",
            {"dataset_id": ds_id, "descriptor_name": "DPA4C", "parameters": {}, "scope": "dataset"},
        )
        job_id = sub["result"]["job_id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if bp.request(12, "job.get", {"id": job_id})["result"]["status"] == "RUNNING":
                break
            time.sleep(0.05)
        assert bp.request(13, "job.cancel", {"id": job_id})["result"]["ok"] is True

        deadline = time.monotonic() + 120
        row = None
        while time.monotonic() < deadline:
            row = bp.request(14, "job.get", {"id": job_id})["result"]
            if row["status"] in ("CANCELLED", "COMPLETED", "FAILED"):
                break
            time.sleep(0.1)
        assert row is not None and row["status"] == "CANCELLED", row

        run = _run_status(bp, 15, ds_id, _run_id_for_job(bp, 16, job_id))
        assert run["status"] == "CANCELLED", f"run row not settled after cancel: {run}"
        assert run["finished_at"], run
    finally:
        assert bp.close() == 0


def test_failed_compute_marks_run_failed(tmp_path: Path) -> None:
    """A compute job that dies (bogus model path) must flip descriptor_runs
    to FAILED with an error message."""
    ds_dir = tmp_path / "gaas"
    write_deepmd(ds_dir, 4, 8, seed=52)
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        ds_id = _register(bp, 10, ds_dir)

        sub = bp.request(
            11,
            "descriptor.submit",
            {
                "dataset_id": ds_id,
                "descriptor_name": "DPA4C",
                "parameters": {"model": str(tmp_path / "missing.pt")},
                "scope": "dataset",
            },
        )
        job_id = sub["result"]["job_id"]
        done = wait_job(bp, job_id, timeout=120)
        assert done["status"] == "FAILED", done

        run = _run_status(bp, 12, ds_id, _run_id_for_job(bp, 13, job_id))
        assert run["status"] == "FAILED", f"run row not settled after failure: {run}"
        assert run["error_message"], run
    finally:
        assert bp.close() == 0


def test_progress_does_not_claim_done_while_computing(tmp_path: Path) -> None:
    """No job.progress event may read fraction 1.0 with a non-terminal message
    while the job is still running, and the phase reset to 'computing
    descriptor' must actually be emitted (not throttled away)."""
    ds_dir = tmp_path / "gaas"
    write_deepmd(ds_dir, 12, 64, seed=53)  # loads in well under 200ms
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        ds_id = _register(bp, 10, ds_dir)

        bp.send(
            {
                "protocol_version": 1,
                "id": 11,
                "method": "descriptor.submit",
                "params": {"dataset_id": ds_id, "descriptor_name": "DPA4C", "parameters": {}, "scope": "dataset"},
            }
        )
        progress, done, deadline = [], None, time.monotonic() + 300
        while time.monotonic() < deadline:
            frame = bp.read_line()
            if frame.get("event") == "job.progress":
                progress.append(frame["data"])
            elif frame.get("event") == "job.finished":
                done = frame["data"]
                break
        assert done is not None and done["status"] == "COMPLETED", done

        messages = [e["message"] for e in progress]
        assert "computing descriptor" in messages, f"phase reset swallowed; got {messages}"
        # "computing descriptor" may legitimately read 1.0 when the engine
        # itself reports completed==total; any other pre-done phase may not
        dishonest = [
            e
            for e in progress
            if e["progress"] >= 0.999 and e["message"] not in ("done", "computing descriptor")
        ]
        assert not dishonest, f"progress claimed 100% before finishing: {dishonest}"
        assert messages[-1] == "done", messages
    finally:
        assert bp.close() == 0


def test_backend_restart_closes_zombie_runs(tmp_path: Path) -> None:
    """A backend restart (or shutdown) must also settle descriptor_runs rows
    left non-terminal by jobs that can no longer finish."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    from mdescriptor_studio_backend.services.job_service import JobService
    from mdescriptor_studio_backend.storage.database import Database

    db = Database(tmp_path / "db.sqlite")
    now = "2026-08-29T00:00:00+00:00"
    db.execute(
        "INSERT INTO jobs (id, job_type, dataset_id, descriptor_run_id, status, created_at)"
        " VALUES ('job_z', 'descriptor.compute', 'ds_x', 'run_z', 'RUNNING', ?)",
        (now,),
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
        " parameters_json, scope, status, created_at)"
        " VALUES ('run_z', 'ds_x', 'DPA4C', '0.2.5', '{}', 'dataset', 'RUNNING', ?)",
        (now,),
    )
    JobService(db, lambda *a: None)  # restart closure runs in __init__

    run = db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_z'")
    assert run["status"] == "CANCELLED", run
    assert run["finished_at"], run


def test_backend_shutdown_closes_zombie_runs(tmp_path: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    from mdescriptor_studio_backend.services.job_service import JobService
    from mdescriptor_studio_backend.storage.database import Database

    db = Database(tmp_path / "db.sqlite")
    now = "2026-08-29T00:00:00+00:00"
    release = threading.Event()

    def runner(ctx):
        release.wait(10)

    jobs = JobService(db, lambda *a: None)
    job_id = jobs.submit("descriptor.compute", runner, descriptor_run_id="run_z")
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
        " parameters_json, scope, status, created_at)"
        " VALUES ('run_z', 'ds_x', 'DPA4C', '0.2.5', '{}', 'dataset', 'RUNNING', ?)",
        (now,),
    )
    time.sleep(0.3)  # let the job enter RUNNING
    jobs.shutdown(wait_seconds=0.5)

    job = db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    assert job["status"] == "CANCELLED", job
    run = db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_z'")
    assert run["status"] == "CANCELLED", run
    release.set()
