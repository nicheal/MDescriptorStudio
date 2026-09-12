"""Engine update check over IPC (PyPI; tolerant to offline environments)."""

import time
from pathlib import Path

from conftest import BackendProcess


def test_check_update_snapshot(tmp_path: Path) -> None:
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"

        # snapshot immediately: check runs in a background thread
        snap = bp.request(1, "engine.check_update")["result"]
        assert snap["installed"]
        assert snap["status"] in ("checking", "up_to_date", "available", "error")

        # wait for the background check to land an event with a final status
        deadline = time.monotonic() + 20
        final = None
        while time.monotonic() < deadline:
            frame = bp.read_line()
            if frame.get("event") == "engine.update.state":
                final = frame["data"]
                if final["status"] in ("up_to_date", "available", "error"):
                    break
        if final is not None and final["status"] != "error":
            # network available: version fields must be consistent
            assert final["latest"]
            if final["status"] == "up_to_date":
                assert final["latest"] == final["installed"]
                assert final["has_update"] is False

        # second call returns the cached snapshot without re-blocking
        again = bp.request(2, "engine.check_update")["result"]
        assert again["installed"] == snap["installed"]

        # engine.update without any version uses latest (docs/plan/02 §5):
        # submit then cancel immediately so pip never completes an install
        upd = bp.request(3, "engine.update", {})
        if "error" in upd:
            # only acceptable failure: no target known (offline)
            assert upd["error"]["code"] == "INVALID_PARAMS"
        else:
            job_id = upd["result"]["job_id"]
            assert upd["result"]["target_version"]
            cancel = bp.request(4, "job.cancel", {"id": job_id})
            assert cancel["result"]["ok"] is True
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                row = bp.request(5, "job.get", {"id": job_id})
                if row["result"]["status"] in ("CANCELLED", "COMPLETED", "FAILED"):
                    break
                time.sleep(0.1)
            assert row["result"]["status"] == "CANCELLED", row["result"]
    finally:
        assert bp.close() == 0
