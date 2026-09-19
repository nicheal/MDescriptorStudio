"""Regression tests for adversarial-review fixes (red-team findings #1-#8)."""

import json
import logging
import threading
from pathlib import Path

from make_fixtures import write_extxyz

from conftest import BackendProcess, register_dataset, wait_job
from mdescriptor_studio_backend.errors import AppError, INVALID_PARAMS
from mdescriptor_studio_backend.protocol import frames
from mdescriptor_studio_backend.protocol.server import Server


def test_job_get_unknown_id_is_error(tmp_path: Path) -> None:
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        frame = bp.request(1, "job.get", {"id": "job_missing"})
        assert frame["error"]["code"] == "JOB_NOT_FOUND"
    finally:
        assert bp.close() == 0


def test_adapter_cache_invalidated_on_change(tmp_path: Path) -> None:
    """File rewritten after registration: statistics recompute must succeed
    and converge instead of failing on the stale in-memory adapter."""
    xyz = tmp_path / "d.xyz"
    write_extxyz(xyz, 3, 8, seed=71)
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        ds_id = register_dataset(bp, 10, xyz)

        # warm the adapter cache, then change the data on disk
        assert bp.request(10, "dataset.frame", {"id": ds_id, "index": 0})["result"]
        write_extxyz(xyz, 1, 8, seed=71)

        meta = bp.request(11, "dataset.get", {"id": ds_id})["result"]
        assert meta["cache_valid"] is False

        stats_resp = bp.request(12, "dataset.statistics", {"id": ds_id})
        assert stats_resp["result"]["recalculating"] is True
        done = wait_job(bp, stats_resp["result"]["job_id"])
        assert done["status"] == "COMPLETED", done

        meta = bp.request(13, "dataset.get", {"id": ds_id})["result"]
        assert meta["number_of_frames"] == 1
        assert meta["cache_valid"] is True
        assert meta["stats"]["structures"] == 1
        frames = bp.request(14, "dataset.frame", {"id": ds_id, "index": 0})["result"]
        assert frames["natoms"] == 8
    finally:
        assert bp.close() == 0


def test_remove_cleans_related_rows(tmp_path: Path) -> None:
    xyz = tmp_path / "d.xyz"
    write_extxyz(xyz, 2, 8, seed=73)
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        ds_id = register_dataset(bp, 10, xyz)
        rm = bp.request(10, "dataset.remove", {"id": ds_id})
        assert rm["result"] == {"ok": True}
        # FK + explicit cleanup: no orphan statistics/jobs rows remain visible
        orphan_jobs = bp.request(11, "job.list", {"dataset_id": ds_id})
        assert orphan_jobs["result"] == []
    finally:
        assert bp.close() == 0


def test_singular_cell_frame_is_not_an_error(tmp_path: Path) -> None:
    """A degenerate (singular) lattice must degrade to non-periodic rendering,
    not blow up with LinAlgError (red-team #7)."""
    # hand-written extxyz: nonzero but singular lattice, explicit T T T pbc
    xyz = tmp_path / "sing.xyz"
    lines = ["2", 'Lattice="1 0 0 0 1 0 0 0 0" pbc="T T T" Properties=species:S:1:pos:R:3 energy=-1.0',
             "Ga 0.0 0.0 0.0", "As 1.4 0.0 0.5"]
    xyz.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        ds_id = register_dataset(bp, 10, xyz)
        fr = bp.request(10, "dataset.frame", {"id": ds_id, "index": 0})
        payload = fr["result"]
        assert payload["natoms"] == 2
        assert payload["cell"] is None  # singular -> treated as non-periodic
        assert payload["ghost_count"] == 0
        assert payload["volume"] is None
    finally:
        assert bp.close() == 0


def test_malformed_extxyz_reports_invalid_dataset(tmp_path: Path) -> None:
    bad = tmp_path / "bad.xyz"
    bad.write_text(
        '1\nLattice="10 0 0 0 10 0 0 0 10" Properties=species:S:1:pos:R:3\n'
        "Ga 0.1 0.2\n",  # missing a coordinate
        encoding="utf-8",
    )
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        resp = bp.request(10, "dataset.register", {"path": str(bad)})
        done = wait_job(bp, resp["result"]["job_id"])
        assert done["status"] == "FAILED"
        assert done["error"]["code"] in ("INVALID_DATASET", "INTERNAL_ERROR")
        # whatever the code path, it must not be a bare crash of the backend
        assert bp.request(11, "system.info")["result"]["backend_version"]
    finally:
        assert bp.close() == 0


def test_non_finite_extxyz_values_are_rejected(tmp_path: Path) -> None:
    """NaN/Infinity must not leave the parser.

    json encodes them as bare tokens that JSON.parse rejects, so the renderer
    drops the frame and the request hangs with no error anywhere (red-team:
    protocol layer). energy=nan is an SCF that never converged: unusable data,
    not missing data."""
    xyz = tmp_path / "nan.xyz"
    xyz.write_text(
        '2\nLattice="10 0 0 0 10 0 0 0 10" Properties=species:S:1:pos:R:3 energy=nan\n'
        "Ga 0.0 0.0 0.0\nAs 1.4 0.0 0.5\n",
        encoding="utf-8",
    )
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        resp = bp.request(10, "dataset.register", {"path": str(xyz)})
        done = wait_job(bp, resp["result"]["job_id"])
        assert done["status"] == "FAILED"
        assert done["error"]["code"] == "INVALID_DATASET"
    finally:
        assert bp.close() == 0


def test_unencodable_response_is_answered_not_dropped(capsys) -> None:
    """The last line of defence: whatever produces a non-finite result, the
    request must still come back as an error frame the renderer can parse."""
    Server({})._write(frames.response_ok(7, {"energy": float("nan")}))
    frame = json.loads(capsys.readouterr().out.strip())
    assert frame["id"] == 7
    assert frame["error"]["code"] == "INTERNAL_ERROR"


def test_oversized_response_is_answered_not_dropped(capsys) -> None:
    # A frame over the bridge's line cap used to tear the whole channel down
    # and orphan the sidecar; the renderer must get a failure for its request.
    Server({})._write(frames.response_ok(7, {"blob": "x" * (frames.MAX_LINE_BYTES + 16)}))
    frame = json.loads(capsys.readouterr().out.strip())
    assert frame["id"] == 7
    assert frame["error"]["code"] == "INTERNAL_ERROR"


def test_app_error_details_reach_the_log_not_the_frame(caplog) -> None:
    """AppError.details can quote a path, so response_err() deliberately keeps
    it off the wire — but it must not vanish either, or the ~25 call sites that
    build a structured diagnosis produce something nobody can ever read."""
    error = AppError(INVALID_PARAMS, "unknown sampling block", {"blocks": ["energy", "forces"]})

    def handler(_params):
        raise error

    server = Server({"x": handler})
    with caplog.at_level(logging.WARNING, logger="mdescriptor_studio_backend.protocol.server"):
        # unbounded: _handle() only ever releases the slot the caller acquired
        server._handle(8, "x", {}, threading.Semaphore())
    assert error.error_id in caplog.text
    assert "unknown sampling block" in caplog.text
    assert "forces" in caplog.text
