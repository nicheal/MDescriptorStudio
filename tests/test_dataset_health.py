"""Data health metrics (health panel): missing values / invalid cell /
duplicate structures / extreme force, plus dataset.rescan and the one-scan-
per-dataset dedupe, unit- and IPC-level."""

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from make_fixtures import write_deepmd  # noqa: E402

from mdescriptor_studio_backend.datasets import compute_statistics, create_adapter  # noqa: E402

from test_backend_smoke import BackendProcess  # noqa: E402
from test_dataset_flow import wait_job  # noqa: E402


def write_pathological_xyz(path: Path) -> None:
    """5 frames, 2 atoms each, one engineered finding per health check:
    f1 duplicates f0's content, f2 lacks the (elsewhere declared) energy,
    f3 carries an 80 eV/Å atom force, f4 has a degenerate (det=0) lattice."""
    lat = "5.0 0.0 0.0 0.0 5.0 0.0 0.0 0.0 5.0"
    header = f'Properties=species:S:1:pos:R:3:forces:R:3 pbc="T T T" '
    rows = [
        f'2\nLattice="{lat}" {header}energy=-10.0\n'
        "Si 0.0 0.0 0.0 0.10 0.00 0.00\nSi 1.5 0.0 0.0 0.00 0.10 0.00\n",
        # exact duplicate of f0 (same species/positions/cell)
        f'2\nLattice="{lat}" {header}energy=-10.0\n'
        "Si 0.0 0.0 0.0 0.10 0.00 0.00\nSi 1.5 0.0 0.0 0.00 0.10 0.00\n",
        # missing energy (declared by f0/f1/f3/f4) + shifted positions (unique)
        f'2\nLattice="{lat}" {header}\n'
        "Si 0.2 0.0 0.0 0.10 0.00 0.00\nSi 1.5 0.1 0.0 0.00 0.10 0.00\n",
        # extreme force: |F| = 80 eV/Å on atom 0 (distinct positions: same
        # geometry as f0 would count as a duplicate structure despite labels)
        f'2\nLattice="{lat}" {header}energy=-10.5\n'
        "Si 0.3 0.0 0.0 80.0 0.00 0.00\nSi 1.7 0.0 0.0 0.00 0.10 0.00\n",
        # degenerate lattice: non-zero but det = 0
        f'2\nLattice="5.0 0.0 0.0 0.0 5.0 0.0 0.0 0.0 0.0" {header}energy=-9.0\n'
        "Si 0.0 0.0 0.0 0.10 0.00 0.00\nSi 1.5 0.0 0.0 0.00 0.10 0.00\n",
    ]
    path.write_text("".join(rows), encoding="utf-8")


def assert_health(health: dict, structures: int) -> None:
    assert health["missing_values"] == 1
    assert health["invalid_cell"] == 1
    assert health["duplicate_structures"] == 1
    assert health["extreme_force"] == 1
    assert health["extreme_force_threshold"] == 50.0
    assert structures == 5


def test_health_statistics_extxyz(tmp_path: Path) -> None:
    p = tmp_path / "bad.xyz"
    write_pathological_xyz(p)
    stats = compute_statistics(create_adapter(p))
    assert_health(stats["health"], stats["structures"])


def write_deepmd_degenerate_box(d: Path) -> None:
    """3 frames, 2 atoms; frame 1 has an all-zero box inside a periodic set."""
    d.mkdir(parents=True)
    (d / "type_map.raw").write_text("Ga As", encoding="utf-8")
    (d / "type.raw").write_text("0 1", encoding="utf-8")
    set_dir = d / "set.000"
    set_dir.mkdir()
    np.save(set_dir / "coord.npy", np.zeros((3, 2, 3)))
    boxes = np.stack([np.eye(3) * 5.0, np.zeros((3, 3)), np.eye(3) * 5.0])
    np.save(set_dir / "box.npy", boxes)
    np.save(set_dir / "energy.npy", np.array([-1.0, -1.0, -1.0]))
    np.save(set_dir / "force.npy", np.zeros((3, 2, 3)))


def test_health_deepmd_degenerate_box(tmp_path: Path) -> None:
    d = tmp_path / "d"
    write_deepmd_degenerate_box(d)
    a = create_adapter(d)
    # corrupt box keeps the periodic claim instead of being masked as isolated
    assert a.get_frame(1).pbc.tolist() == [True, True, True]
    assert a.scan().periodicity["fully_periodic"] is True
    stats = compute_statistics(a)
    assert stats["health"]["invalid_cell"] == 1
    assert stats["periodicity"]["fully_periodic"] is True


def test_health_nopbc_not_flagged(tmp_path: Path) -> None:
    """Zero boxes throughout (nopbc set) are a legit isolated system, not an
    invalid cell."""
    d = tmp_path / "gas"
    write_deepmd(d, 3, 8, seed=31)
    (d / "set.000" / "box.npy").unlink()
    (d / "nopbc").write_text("", encoding="utf-8")
    a = create_adapter(d)
    stats = compute_statistics(a)
    assert stats["health"]["invalid_cell"] == 0
    assert stats["periodicity"]["isolated"] is True
    assert stats["health"]["duplicate_structures"] == 0


def test_health_ipc_rescan_and_dedupe(tmp_path: Path) -> None:
    xyz = tmp_path / "bad.xyz"
    write_pathological_xyz(xyz)
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        resp = bp.request(1, "dataset.register", {"path": str(xyz)})
        done = wait_job(bp, resp["result"]["job_id"])
        assert done["status"] == "COMPLETED", done
        ds_id = done["result"]["dataset_id"]

        stats_resp = bp.request(2, "dataset.statistics", {"id": ds_id})
        assert stats_resp["result"]["recalculating"] is False
        stats = stats_resp["result"]["stats"]
        assert_health(stats["health"], stats["structures"])

        meta = bp.request(3, "dataset.list")["result"][0]
        assert meta["last_scan_at"]

        # two rescans fired back-to-back share one scan job
        bp.send({"protocol_version": 1, "id": 4, "method": "dataset.rescan", "params": {"id": ds_id}})
        bp.send({"protocol_version": 1, "id": 5, "method": "dataset.rescan", "params": {"id": ds_id}})
        replies = {}
        while len(replies) < 2:
            frame = bp.read_line()
            if frame.get("id") in (4, 5):
                replies[frame["id"]] = frame
        job_a = replies[4]["result"]["job_id"]
        job_b = replies[5]["result"]["job_id"]
        assert job_a == job_b, "concurrent rescans must dedupe to one job"
        done = wait_job(bp, job_a)
        assert done["status"] == "COMPLETED", done

        stats2 = bp.request(6, "dataset.statistics", {"id": ds_id})
        assert stats2["result"]["recalculating"] is False
        assert stats2["result"]["stats"]["health"]["duplicate_structures"] == 1

        # a pre-health (legacy) cache triggers exactly one recompute on demand
        db_path = tmp_path / "database.sqlite"
        con = sqlite3.connect(db_path)
        try:
            (raw,) = con.execute("SELECT stats_json FROM dataset_statistics").fetchone()
            legacy = json.loads(raw)
            legacy.pop("health", None)
            con.execute(
                "UPDATE dataset_statistics SET stats_json = ?", (json.dumps(legacy),)
            )
            con.commit()
        finally:
            con.close()
        stale = bp.request(7, "dataset.statistics", {"id": ds_id})
        assert stale["result"]["recalculating"] is True
        assert stale["result"]["stats"] is None
        assert stale["result"]["job_id"]
        # a second caller during the recompute shares the same job
        again = bp.request(8, "dataset.statistics", {"id": ds_id})
        assert again["result"]["recalculating"] is True
        assert again["result"]["job_id"] == stale["result"]["job_id"]
        done = wait_job(bp, stale["result"]["job_id"])
        assert done["status"] == "COMPLETED", done
        fresh = bp.request(9, "dataset.statistics", {"id": ds_id})
        assert fresh["result"]["recalculating"] is False
        assert_health(fresh["result"]["stats"]["health"], fresh["result"]["stats"]["structures"])
    finally:
        assert bp.close() == 0
