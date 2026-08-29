"""Full IPC flow: register (async job) -> statistics -> frame, over stdio."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from make_fixtures import write_deepmd, write_extxyz  # noqa: E402

from test_backend_smoke import BackendProcess  # noqa: E402


def wait_job(bp: BackendProcess, job_id: str, timeout: float = 60.0) -> dict:
    """Wait for the job.finished event (carries the runner result payload)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = bp.read_line()
        if frame.get("event") == "job.finished" and frame["data"]["job_id"] == job_id:
            return frame["data"]
        # ignore job.progress events
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def test_register_statistics_frame_flow(tmp_path: Path) -> None:
    ds_dir = tmp_path / "gaas"
    write_deepmd(ds_dir, 10, 64, seed=21)
    xyz = tmp_path / "small.xyz"
    write_extxyz(xyz, 10, 64, seed=22)

    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"

        # extxyz register (async)
        started = time.monotonic()
        resp = bp.request(10, "dataset.register", {"path": str(xyz)})
        job_id = resp["result"]["job_id"]
        done = wait_job(bp, job_id)
        assert done["status"] == "COMPLETED", done
        elapsed = time.monotonic() - started
        ds_id = done["result"]["dataset_id"]
        print(f"\nregister extxyz(10 frames): {elapsed:.2f}s")
        assert elapsed < 2.0, f"registration took {elapsed:.2f}s (acceptance: <2s)"

        listing = bp.request(11, "dataset.list")
        assert len(listing["result"]) == 1
        meta = listing["result"][0]
        assert meta["format"] == "extxyz"
        assert meta["number_of_frames"] == 10
        assert set(meta["elements"]) == {"Ga", "As"}
        assert meta["cache_valid"] is True

        stats_resp = bp.request(12, "dataset.statistics", {"id": ds_id})
        stats = stats_resp["result"]["stats"]
        assert stats["structures"] == 10
        assert stats["elements"][0]["symbol"] in ("Ga", "As")
        assert stats["energy_per_atom"]["counts"]

        # deepmd register
        resp = bp.request(13, "dataset.register", {"path": str(ds_dir), "name": "GaAs DeepMD"})
        done = wait_job(bp, resp["result"]["job_id"])
        assert done["status"] == "COMPLETED", done
        listing = bp.request(14, "dataset.list")
        assert len(listing["result"]) == 2
        deepmd_meta = next(m for m in listing["result"] if m["format"] == "deepmd")
        assert deepmd_meta["name"] == "GaAs DeepMD"
        assert deepmd_meta["periodicity"]["fully_periodic"] is True

        # frame access
        fr = bp.request(15, "dataset.frame", {"id": deepmd_meta["id"], "index": 3})
        payload = fr["result"]
        assert payload["natoms"] == 64
        assert payload["formula"] in ("Ga32As32", "As32Ga32")
        assert len(payload["atom_rows"]) == 64
        # periodic frame: lattice in xyz header + boundary image atoms for bonds
        assert payload["cell"] is not None and len(payload["cell"]) == 9
        assert 'Lattice="' in payload["xyz"].splitlines()[1]
        assert payload["ghost_count"] > 0
        assert int(payload["xyz"].splitlines()[0]) == 64 + payload["ghost_count"]
        assert payload["energy_per_atom"] is not None

        # duplicate registration rejected
        dup = bp.request(16, "dataset.register", {"path": str(xyz)})
        assert dup["error"]["code"] == "INVALID_DATASET"

        # rename: returns updated meta, trims whitespace, persists to dataset.list
        rn = bp.request(17, "dataset.rename", {"id": ds_id, "name": "  Renamed XYZ  "})
        assert rn["result"]["name"] == "Renamed XYZ"
        listing = bp.request(18, "dataset.list")
        assert next(m for m in listing["result"] if m["id"] == ds_id)["name"] == "Renamed XYZ"
        # blank name rejected, unknown id rejected
        blank = bp.request(19, "dataset.rename", {"id": ds_id, "name": "   "})
        assert blank["error"]["code"] == "INVALID_PARAMS"
        ghost = bp.request(20, "dataset.rename", {"id": "ds_missing", "name": "x"})
        assert ghost["error"]["code"] == "DATASET_NOT_FOUND"

        # remove
        rm = bp.request(21, "dataset.remove", {"id": ds_id})
        assert rm["result"] == {"ok": True}
        listing = bp.request(22, "dataset.list")
        assert len(listing["result"]) == 1
        # the surviving dataset keeps its (renamed) sibling untouched by id
        assert all(m["id"] != ds_id for m in listing["result"])
    finally:
        assert bp.close() == 0
