"""Backend integration smoke test: spawn over stdio, check handshake + frames.

Covers M0 acceptance: backend.ready first frame, system.info roundtrip,
error frame for unknown method, protocol version mismatch guard.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"


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


def test_handshake_system_info_and_errors(tmp_path: Path) -> None:
    bp = BackendProcess(tmp_path)
    ready = bp.read_line()
    assert ready["event"] == "backend.ready", ready
    assert ready["protocol_version"] == 1
    assert ready["data"]["backend_version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", ready["data"]["mdescriptor_version"])
    assert ready["data"]["mdescriptor_baseline_version"] == "2"
    assert ready["data"]["mdescriptor_descriptor_info_schema_version"] == 3

    info = bp.request(1, "system.info")
    assert info["result"]["protocol_version"] == 1
    assert info["result"]["mdescriptor_version"] == ready["data"]["mdescriptor_version"]
    assert info["result"]["mdescriptor_baseline_version"] == "2"
    assert info["result"]["mdescriptor_descriptor_info_schema_version"] == 3

    bad = bp.request(2, "no.such.method")
    assert bad["error"]["code"] == "INVALID_PARAMS"

    bp.send({"protocol_version": 99, "id": 3, "method": "system.info", "params": {}})
    mismatch = bp.read_line()
    assert mismatch["error"]["code"] == "PROTOCOL_VERSION_MISMATCH"
    # spec: incompatible client -> error frame then exit code 2
    assert bp.proc.wait(timeout=15) == 2
    with tempfile.TemporaryDirectory() as tmp2:  # fresh backend for remaining checks
        bp2 = BackendProcess(Path(tmp2))
        try:
            assert bp2.read_line()["event"] == "backend.ready"
            listing = bp2.request(4, "dataset.list")
            assert listing["result"] == []
            st = bp2.request(5, "settings.set", {"key": "workspace.activeDatasetId", "value": "ds_x"})
            assert st["result"] == {"ok": True}
            got = bp2.request(6, "settings.get", {"key": "workspace.activeDatasetId"})
            assert got["result"]["value"] == "ds_x"
        finally:
            assert bp2.close() == 0
