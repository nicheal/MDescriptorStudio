"""Regression tests for adversarial-review fixes (red-team findings #1-#8)."""

from pathlib import Path

from make_fixtures import write_extxyz

from conftest import BackendProcess, register_dataset, wait_job


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
