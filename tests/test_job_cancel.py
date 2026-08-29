"""M4 acceptance: cooperative cancel takes effect (ADR-10 quantification)."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from make_fixtures import write_deepmd  # noqa: E402

from test_backend_smoke import BackendProcess  # noqa: E402
from test_dataset_flow import wait_job  # noqa: E402


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
