from __future__ import annotations

import time
from pathlib import Path

from mdescriptor_studio_backend.services.job_service import JobService
from mdescriptor_studio_backend.storage.database import Database


def test_completed_job_result_is_available_after_finished_event(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    jobs = JobService(db, emit=lambda *_args: None)
    job_id = jobs.submit("dataset.test", lambda _ctx: {"path": "saved.extxyz", "count": 3})

    deadline = time.monotonic() + 5
    row = jobs.get_job(job_id)
    while row and row["status"] not in {"COMPLETED", "FAILED", "CANCELLED"} and time.monotonic() < deadline:
        time.sleep(0.01)
        row = jobs.get_job(job_id)

    assert row is not None
    assert row["status"] == "COMPLETED"
    assert row["result"] == {"path": "saved.extxyz", "count": 3}
    assert "result_json" not in row
    jobs.shutdown()
