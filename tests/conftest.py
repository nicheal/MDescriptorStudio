"""Shared test scaffolding: import paths plus the IPC process and job helpers.

Conftest runs before test module imports, so backend/ and scripts/ are already
on sys.path and no test file needs its own sys.path boilerplate.

The backend is a child process, so every wait here is bounded and reports the
child's stderr: a hung or crashed backend must fail one test with a diagnosis
instead of the whole session with a stack frame in ``readline``.
"""

import json
import os
import queue
import subprocess
import sys
import threading
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
        self._lines: queue.Queue[str] = queue.Queue()
        self._stderr: list[str] = []
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    def _pump_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self._lines.put(line)
        self._lines.put("")  # EOF sentinel: the backend is gone

    def _pump_stderr(self) -> None:
        assert self.proc.stderr is not None
        self._stderr.extend(self.proc.stderr)

    def send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def read_line(self, timeout: float = 30.0) -> dict:
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            raise self._failed(f"no stdout within {timeout}s") from None
        if not line:
            raise self._failed("backend closed stdout")
        return json.loads(line)

    def _failed(self, why: str) -> AssertionError:
        return AssertionError(
            f"{why} (returncode={self.proc.poll()})\nbackend stderr:\n{''.join(self._stderr[-40:])}"
        )

    def request(self, vid: int, method: str, params: dict | None = None) -> dict:
        self.send_request(vid, method, params)
        return self.responses({vid})[vid]

    def send_request(self, vid: int, method: str, params: dict | None = None) -> None:
        """Queue a request without waiting for its reply.

        Pair with responses() to exercise dedupe paths: waiting for the first
        reply gives the server time to finish the job before the second request
        is even admitted.
        """
        self.send({"protocol_version": 1, "id": vid, "method": method, "params": params or {}})

    def responses(self, ids: set[int]) -> dict[int, dict]:
        """Collect one reply per id, skipping events and other traffic."""
        replies: dict[int, dict] = {}
        while len(replies) < len(ids):
            frame = self.read_line()
            if frame.get("id") in ids:
                replies[frame["id"]] = frame
        return replies

    def close(self) -> int:
        if self.proc.stdin:
            self.proc.stdin.close()
        return self.proc.wait(timeout=15)


def wait_job(bp: BackendProcess, job_id: str, timeout: float = 60.0) -> dict:
    """Wait for the job.finished event (carries the runner result payload)."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"job {job_id} did not finish in {timeout}s")
        frame = bp.read_line(timeout=remaining)
        if frame.get("event") == "job.finished" and frame["data"]["job_id"] == job_id:
            return frame["data"]
        # ignore job.progress events


def register_dataset(bp: BackendProcess, vid: int, path: Path, timeout: float = 180.0) -> str:
    """Register a dataset and wait for its scan job; returns the dataset id."""
    resp = bp.request(vid, "dataset.register", {"path": str(path), "name": path.name})
    done = wait_job(bp, resp["result"]["job_id"], timeout=timeout)
    assert done["status"] == "COMPLETED", done
    return done["result"]["dataset_id"]
