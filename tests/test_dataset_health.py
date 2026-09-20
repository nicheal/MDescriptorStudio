"""Data health metrics (health panel): missing values / invalid cell /
duplicate structures / energy anomalies / extreme force / non-physical
structures (short contact) / net force, plus dataset.rescan and the
one-scan-per-dataset dedupe, unit- and IPC-level."""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest

from make_fixtures import write_deepmd

from mdescriptor_studio_backend.datasets import compute_statistics, create_adapter

from conftest import BackendProcess, wait_job


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
    assert health["energy_anomaly"] == 0
    assert health["invalid_cell"] == 1
    assert health["duplicate_structures"] == 1
    assert health["extreme_force"] == 1
    assert health["extreme_force_threshold"] == 50.0
    # the fixture's 1.5 Å Si–Si contacts are themselves non-physical (below
    # 0.7 × 2 × r_Si = 1.554 Å), and its unbalanced forces give |ΣF| ≈ 0.14
    # eV/Å — every frame trips both NepTrainKit-style checks
    assert health["nonphysical_structures"] == 5
    assert health["short_contact_coefficient"] == 0.7
    assert health["net_force"] == 5
    assert health["net_force_threshold"] == 0.001
    assert structures == 5


def test_health_statistics_extxyz(tmp_path: Path) -> None:
    p = tmp_path / "bad.xyz"
    write_pathological_xyz(p)
    stats = compute_statistics(create_adapter(p))
    assert_health(stats["health"], stats["structures"])


def test_duplicate_structures_of_mapping(tmp_path: Path) -> None:
    """health_findings.duplicate_structures_of runs parallel to
    duplicate_structures: every flagged copy maps to the first frame with
    that content, no matter how many copies follow it."""
    p = tmp_path / "dups.xyz"
    lat = "4.5 0.0 0.0 0.0 4.5 0.0 0.0 0.0 4.5"
    header = 'Properties=species:S:1:pos:R:3 pbc="T T T" '
    frame = f'2\nLattice="{lat}" {header}energy=-10.0\nSi 0.0 0.0 0.0\nSi 2.35 0.0 0.0\n'
    other = f'2\nLattice="{lat}" {header}energy=-11.0\nSi 0.1 0.0 0.0\nSi 2.35 0.1 0.0\n'
    p.write_text(frame + other + frame + frame, encoding="utf-8")
    stats = compute_statistics(create_adapter(p))
    findings = stats["health_findings"]
    assert findings["duplicate_structures"] == [2, 3]
    assert findings["duplicate_structures_of"] == [0, 0]


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


def test_health_survives_non_finite_forces(tmp_path: Path) -> None:
    """A single NaN force used to fail the whole statistics job: np.histogram
    refuses to pick a range over NaN, so the dataset came back as "invalid or
    unavailable" with no health panel at all. Array sources (DeepMD, native)
    have no text-parser finite check in front of them."""
    d = tmp_path / "nanforce"
    (d / "set.000").mkdir(parents=True)
    (d / "type_map.raw").write_text("Ga As", encoding="utf-8")
    (d / "type.raw").write_text("0 1", encoding="utf-8")
    forces = np.zeros((3, 2, 3))
    forces[0, 0, 0] = np.nan
    np.save(d / "set.000" / "coord.npy", np.zeros((3, 2, 3)))
    np.save(d / "set.000" / "box.npy", np.stack([np.eye(3) * 5.0] * 3))
    np.save(d / "set.000" / "energy.npy", np.array([-1.0, -1.0, -1.0]))
    np.save(d / "set.000" / "force.npy", forces)
    stats = compute_statistics(create_adapter(d))
    assert stats["structures"] == 3
    assert stats["force_magnitude_summary"]["max"] == 0.0
    # the NaN magnitude and the NaN per-frame max drop out, the rest survives:
    # 6 atom magnitudes minus 1, and 3 frame maxima minus 1
    assert sum(stats["force_magnitude"]["counts"]) == forces.shape[0] * forces.shape[1] - 1
    assert sum(stats["max_force"]["counts"]) == forces.shape[0] - 1


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


def write_physics_xyz(path: Path) -> None:
    """3 frames engineering the NepTrainKit-style checks in isolation:
    f0 is clean (real 2.35 Å Si–Si bond, nearest image 2.15 Å, forces cancel
    pairwise), f1 has a short contact (1.2 Å < 0.7 × 2 × r_Si = 1.554 Å) with
    balanced forces, f2 keeps f0's geometry but |ΣF| = 0.6 eV/Å."""
    lat = "4.5 0.0 0.0 0.0 4.5 0.0 0.0 0.0 4.5"
    header = 'Properties=species:S:1:pos:R:3:forces:R:3 pbc="T T T" '
    rows = [
        f'2\nLattice="{lat}" {header}energy=-10.0\n'
        "Si 0.0 0.0 0.0 0.50 0.00 0.00\nSi 2.35 0.0 0.0 -0.50 0.00 0.00\n",
        f'2\nLattice="{lat}" {header}energy=0.1\n'
        "Si 0.0 0.0 0.0 0.00 0.30 0.00\nSi 1.2 0.0 0.0 0.00 -0.30 0.00\n",
        f'2\nLattice="{lat}" {header}energy=0.0\n'
        "Si 0.0 0.0 0.0 0.60 0.00 0.00\nSi 2.35 0.0 0.0 0.00 0.00 0.00\n",
    ]
    path.write_text("".join(rows), encoding="utf-8")


def test_health_nonphysical_and_net_force(tmp_path: Path) -> None:
    p = tmp_path / "physics.xyz"
    write_physics_xyz(p)
    stats = compute_statistics(create_adapter(p))
    health = stats["health"]
    assert health["nonphysical_structures"] == 1
    assert health["energy_anomaly"] == 2
    assert health["short_contact_coefficient"] == 0.7
    assert health["net_force"] == 1
    assert health["net_force_threshold"] == 0.001
    # findings carry the original file positions behind the counts
    assert stats["health_findings"]["nonphysical_structures"] == [1]
    # ...and the distance that got each one flagged, parallel to the indices:
    # the drawer reads this instead of running the neighbour search per row.
    assert stats["health_findings"]["nonphysical_distances"] == [pytest.approx(1.2)]
    assert stats["health_findings"]["net_force"] == [2]
    assert stats["health_findings"]["energy_anomaly"] == [1, 2]
    assert stats["health_findings"]["invalid_cell"] == []
    # f2 repeats f0's geometry with different labels: flagged as a copy of
    # f0 — exactly the same-geometry/different-label case the "Duplicate of"
    # column exists to explain
    assert stats["health_findings"]["duplicate_structures"] == [2]
    assert stats["health_findings"]["duplicate_structures_of"] == [0]
    assert stats["health_findings"]["cap"] >= 1


def test_health_nonphysical_own_image(tmp_path: Path) -> None:
    """An atom overlapping its own periodic image is non-physical; the same
    atom in a comfortably sized cell is not."""
    header = 'Properties=species:S:1:pos:R:3 pbc="T T T"'
    rows = [
        '1\nLattice="1.0 0.0 0.0 0.0 1.0 0.0 0.0 0.0 1.0" '
        f"{header}\nSi 0.0 0.0 0.0\n",
        '1\nLattice="4.0 0.0 0.0 0.0 4.0 0.0 0.0 0.0 4.0" '
        f"{header}\nSi 0.0 0.0 0.0\n",
    ]
    p = tmp_path / "image.xyz"
    p.write_text("".join(rows), encoding="utf-8")
    stats = compute_statistics(create_adapter(p))
    assert stats["health"]["nonphysical_structures"] == 1
    assert stats["health_findings"]["nonphysical_structures"] == [0]


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
        bp.send_request(4, "dataset.rescan", {"id": ds_id})
        bp.send_request(5, "dataset.rescan", {"id": ds_id})
        replies = bp.responses({4, 5})
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
        # Both callers must be admitted before the recompute finishes, so the
        # requests go out together: awaiting id 7 first lets a small dataset
        # finish and the second caller then reads the fresh cache.
        bp.send_request(7, "dataset.statistics", {"id": ds_id})
        bp.send_request(8, "dataset.statistics", {"id": ds_id})
        replies = bp.responses({7, 8})
        stale, again = replies[7], replies[8]
        assert stale["result"]["recalculating"] is True
        assert stale["result"]["stats"] is None
        assert stale["result"]["job_id"]
        # a second caller during the recompute shares the same job
        assert again["result"]["recalculating"] is True
        assert again["result"]["job_id"] == stale["result"]["job_id"]
        done = wait_job(bp, stale["result"]["job_id"])
        assert done["status"] == "COMPLETED", done
        fresh = bp.request(9, "dataset.statistics", {"id": ds_id})
        assert fresh["result"]["recalculating"] is False
        assert_health(fresh["result"]["stats"]["health"], fresh["result"]["stats"]["structures"])

        # a cache from before the min-distance pass upgrades the same way
        con = sqlite3.connect(db_path)
        try:
            (raw,) = con.execute("SELECT stats_json FROM dataset_statistics").fetchone()
            legacy = json.loads(raw)
            legacy.pop("min_distance", None)
            con.execute(
                "UPDATE dataset_statistics SET stats_json = ?", (json.dumps(legacy),)
            )
            con.commit()
        finally:
            con.close()
        stale_md = bp.request(10, "dataset.statistics", {"id": ds_id})
        assert stale_md["result"]["recalculating"] is True
        assert stale_md["result"]["stats"] is None
        done = wait_job(bp, stale_md["result"]["job_id"])
        assert done["status"] == "COMPLETED", done
        fresh_md = bp.request(11, "dataset.statistics", {"id": ds_id})
        assert fresh_md["result"]["recalculating"] is False
        assert fresh_md["result"]["stats"]["min_distance_summary"] is not None

        # a cache from before the nonphysical/net-force checks upgrades too
        con = sqlite3.connect(db_path)
        try:
            (raw,) = con.execute("SELECT stats_json FROM dataset_statistics").fetchone()
            legacy = json.loads(raw)
            legacy["health"].pop("nonphysical_structures", None)
            legacy["health"].pop("net_force", None)
            con.execute(
                "UPDATE dataset_statistics SET stats_json = ?", (json.dumps(legacy),)
            )
            con.commit()
        finally:
            con.close()
        stale_nf = bp.request(12, "dataset.statistics", {"id": ds_id})
        assert stale_nf["result"]["recalculating"] is True
        assert stale_nf["result"]["stats"] is None
        done = wait_job(bp, stale_nf["result"]["job_id"])
        assert done["status"] == "COMPLETED", done
        fresh_nf = bp.request(13, "dataset.statistics", {"id": ds_id})
        assert fresh_nf["result"]["recalculating"] is False
        assert_health(
            fresh_nf["result"]["stats"]["health"],
            fresh_nf["result"]["stats"]["structures"],
        )

        # a cache from before the duplicate-origin mapping upgrades too
        con = sqlite3.connect(db_path)
        try:
            (raw,) = con.execute("SELECT stats_json FROM dataset_statistics").fetchone()
            legacy = json.loads(raw)
            legacy["health_findings"].pop("duplicate_structures_of", None)
            con.execute(
                "UPDATE dataset_statistics SET stats_json = ?", (json.dumps(legacy),)
            )
            con.commit()
        finally:
            con.close()
        stale_dup = bp.request(14, "dataset.statistics", {"id": ds_id})
        assert stale_dup["result"]["recalculating"] is True
        done = wait_job(bp, stale_dup["result"]["job_id"])
        assert done["status"] == "COMPLETED", done
        fresh_dup = bp.request(15, "dataset.statistics", {"id": ds_id})
        assert fresh_dup["result"]["recalculating"] is False
        dup_findings = fresh_dup["result"]["stats"]["health_findings"]
        assert dup_findings["duplicate_structures"] == [1]
        assert dup_findings["duplicate_structures_of"] == [0]
    finally:
        assert bp.close() == 0


def test_health_findings_rows(tmp_path: Path) -> None:
    """Findings expose preview-grade rows for saving frame selections as views."""
    xyz = tmp_path / "physics.xyz"
    write_physics_xyz(xyz)
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        resp = bp.request(1, "dataset.register", {"path": str(xyz)})
        done = wait_job(bp, resp["result"]["job_id"])
        ds_id = done["result"]["dataset_id"]

        stats = bp.request(2, "dataset.statistics", {"id": ds_id})["result"]["stats"]
        assert stats["health_findings"]["nonphysical_structures"] == [1]
        assert stats["health_findings"]["net_force"] == [2]
        assert stats["health_findings"]["energy_anomaly"] == [1, 2]
        assert "excluded_frames" not in stats

        rows = bp.request(3, "dataset.findings", {"id": ds_id, "check": "nonphysical_structures"})["result"]
        assert rows["recalculating"] is False
        assert rows["total"] == 1
        assert rows["rows"][0]["index"] == 1
        assert rows["rows"][0]["formula"] == "Si2"
        # the non-physical tab's metric: the 1.2 Å short contact behind the flag
        assert rows["rows"][0]["min_distance"] == 1.2
        assert "excluded" not in rows["rows"][0]

        energy_rows = bp.request(4, "dataset.findings", {"id": ds_id, "check": "energy_anomaly"})["result"]
        assert energy_rows["total"] == 2
        assert [(r["index"], r["energy_per_atom"]) for r in energy_rows["rows"]] == [(1, 0.05), (2, 0.0)]

        # findings now require a health-check key; frame selections are created
        # through dataset.view.create.
        invalid = bp.request(5, "dataset.findings", {"id": ds_id})
        assert invalid["error"]["code"] == "INVALID_PARAMS"
    finally:
        assert bp.close() == 0
