"""Full IPC flow: register (async job) -> statistics -> frame, over stdio."""

import time
from pathlib import Path

from make_fixtures import write_deepmd, write_extxyz

from conftest import BackendProcess, wait_job


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

        # Dataset views are immutable index selections under a real dataset.
        view = bp.request(
            121,
            "dataset.view.create",
            {"dataset_id": ds_id, "name": "Flagged subset", "role": "filtered", "indices": [1, 3, 5]},
        )["result"]
        assert view["frame_indices"] == [1, 3, 5]
        assert view["number_of_frames"] == 3
        assert view["stale"] is False
        listed_views = bp.request(122, "dataset.view.list", {"dataset_id": ds_id})["result"]
        assert [item["id"] for item in listed_views] == [view["id"]]

        split = bp.request(
            123,
            "dataset.view.split",
            {"dataset_id": ds_id, "seed": 7, "train_ratio": 0.6, "validation_ratio": 0.2},
        )["result"]["views"]
        split_indices = [set(item["frame_indices"]) for item in split]
        assert [item["role"] for item in split] == ["train", "validation", "test"]
        assert set.union(*split_indices) == set(range(10))
        assert not (split_indices[0] & split_indices[1] or split_indices[0] & split_indices[2] or split_indices[1] & split_indices[2])

        # Materializing is explicit: it writes a new source, then normal
        # registration records parent/view/hash lineage on the new dataset.
        materialized_path = tmp_path / "flagged.extxyz"
        submitted = bp.request(
            124,
            "dataset.view.materialize",
            {"view_id": view["id"], "dest_path": str(materialized_path)},
        )["result"]
        materialized = wait_job(bp, submitted["job_id"])
        assert materialized["status"] == "COMPLETED", materialized
        assert materialized["result"]["frames_written"] == 3
        registered = bp.request(
            125,
            "dataset.register",
            {
                "path": materialized["result"]["path"],
                "name": "Flagged materialized",
                "lineage": materialized["result"]["lineage"],
            },
        )["result"]
        registered_done = wait_job(bp, registered["job_id"])
        assert registered_done["status"] == "COMPLETED", registered_done
        materialized_id = registered_done["result"]["dataset_id"]
        materialized_meta = next(item for item in bp.request(126, "dataset.list")["result"] if item["id"] == materialized_id)
        assert materialized_meta["number_of_frames"] == 3
        assert materialized_meta["lineage"]["parent_dataset_id"] == ds_id
        assert materialized_meta["lineage"]["source_view_id"] == view["id"]

        # deepmd register
        resp = bp.request(13, "dataset.register", {"path": str(ds_dir), "name": "GaAs DeepMD"})
        done = wait_job(bp, resp["result"]["job_id"])
        assert done["status"] == "COMPLETED", done
        listing = bp.request(14, "dataset.list")
        assert len(listing["result"]) == 3
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
        # each boundary image carries its parent real-atom index for click-to-select
        assert len(payload["ghost_parents"]) == payload["ghost_count"]
        assert all(0 <= p < 64 for p in payload["ghost_parents"])
        assert payload["energy_per_atom"] is not None
        assert payload["bond_cutoff"] == 2.4

        # Explore can narrow the displayed bond range without changing the
        # underlying dataset; the value also controls periodic image padding.
        tuned = bp.request(
            151,
            "dataset.frame",
            {"id": deepmd_meta["id"], "index": 3, "bond_cutoff": 1.2},
        )
        assert tuned["result"]["bond_cutoff"] == 1.2
        invalid_cutoff = bp.request(
            152,
            "dataset.frame",
            {"id": deepmd_meta["id"], "index": 3, "bond_cutoff": 0},
        )
        assert invalid_cutoff["error"]["code"] == "INVALID_PARAMS"

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

        # Keep the original removal assertion focused by removing the
        # explicitly materialized child first; source bytes remain untouched.
        assert bp.request(201, "dataset.remove", {"id": materialized_id})["result"] == {"ok": True}

        # remove
        rm = bp.request(21, "dataset.remove", {"id": ds_id})
        assert rm["result"] == {"ok": True}
        listing = bp.request(22, "dataset.list")
        assert len(listing["result"]) == 1
        # the surviving dataset keeps its (renamed) sibling untouched by id
        assert all(m["id"] != ds_id for m in listing["result"])
    finally:
        assert bp.close() == 0
