"""Shared test scaffolding: import paths plus the IPC process and job helpers.

Conftest runs before test module imports, so backend/ and scripts/ are already
on sys.path and no test file needs its own sys.path boilerplate.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _entry in (ROOT / "backend", ROOT / "scripts"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

BACKEND_DIR = ROOT / "backend"


class BackendProcess:
    def __init__(self, tmp: Path):
        env = {**os.environ, "MDS_DATA_DIR": str(tmp)}
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "mdescriptor_studio_backend"],
            cwd=BACKEND_DIR,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )

    def send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def read_line(self, timeout: float = 30.0) -> dict:
        # readline blocks; rely on process health + test-level timeout
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        assert line, f"backend closed stdout: {self._stderr()}"
        return json.loads(line)

    def request(self, vid: int, method: str, params: dict | None = None) -> dict:
        self.send({"protocol_version": 1, "id": vid, "method": method, "params": params or {}})
        while True:
            frame = self.read_line()
            if frame.get("id") == vid:
                return frame
            # skip events interleaved by concurrent traffic

    def _stderr(self) -> str:
        return ""

    def close(self) -> int:
        if self.proc.stdin:
            self.proc.stdin.close()
        return self.proc.wait(timeout=15)


def wait_job(bp: BackendProcess, job_id: str, timeout: float = 60.0) -> dict:
    """Wait for the job.finished event (carries the runner result payload)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = bp.read_line()
        if frame.get("event") == "job.finished" and frame["data"]["job_id"] == job_id:
            return frame["data"]
        # ignore job.progress events
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def register_dataset(bp: BackendProcess, vid: int, path: Path, timeout: float = 180.0) -> str:
    """Register a dataset and wait for its scan job; returns the dataset id."""
    resp = bp.request(vid, "dataset.register", {"path": str(path), "name": path.name})
    done = wait_job(bp, resp["result"]["job_id"], timeout=timeout)
    assert done["status"] == "COMPLETED", done
    return done["result"]["dataset_id"]
