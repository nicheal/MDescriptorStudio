"""Backend integration smoke test: spawn over stdio, check handshake + frames.

Covers M0 acceptance: backend.ready first frame, system.info roundtrip,
error frame for unknown method, protocol version mismatch guard.
"""

import re
from pathlib import Path

from conftest import BackendProcess


def test_handshake_system_info_and_errors(tmp_path: Path) -> None:
    bp = BackendProcess(tmp_path)
    ready = bp.read_line()
    assert ready["event"] == "backend.ready", ready
    assert ready["protocol_version"] == 1
    assert ready["data"]["backend_version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", ready["data"]["mdescriptor_version"])
    assert ready["data"]["mdescriptor_baseline_version"] == "2"
    assert ready["data"]["mdescriptor_descriptor_info_schema_version"] == 3
    assert ready["data"]["analysis_algorithm_version"] == "studio-analysis-2"

    info = bp.request(1, "system.info")
    assert info["result"]["protocol_version"] == 1
    assert info["result"]["mdescriptor_version"] == ready["data"]["mdescriptor_version"]
    assert info["result"]["mdescriptor_baseline_version"] == "2"
    assert info["result"]["mdescriptor_descriptor_info_schema_version"] == 3
    assert info["result"]["analysis_algorithm_version"] == ready["data"]["analysis_algorithm_version"]

    bad = bp.request(2, "no.such.method")
    assert bad["error"]["code"] == "INVALID_PARAMS"

    bp.send({"protocol_version": 99, "id": 3, "method": "system.info", "params": {}})
    mismatch = bp.read_line()
    assert mismatch["error"]["code"] == "PROTOCOL_VERSION_MISMATCH"
    # A malformed/incompatible request is rejected without killing the
    # long-lived backend process; subsequent valid requests still work.
    still_alive = bp.request(4, "system.info")
    assert still_alive["result"]["protocol_version"] == 1
    listing = bp.request(5, "dataset.list")
    assert listing["result"] == []
    st = bp.request(6, "settings.set", {"key": "workspace.activeDatasetId", "value": "ds_x"})
    assert st["result"] == {"ok": True}
    got = bp.request(7, "settings.get", {"key": "workspace.activeDatasetId"})
    assert got["result"]["value"] == "ds_x"
    assert bp.close() == 0
