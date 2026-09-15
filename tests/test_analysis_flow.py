"""PCA + heatmap flow over IPC (M5 backend acceptance)."""

from pathlib import Path

from make_fixtures import write_deepmd

from conftest import BackendProcess, wait_job


def test_pca_and_heatmap(tmp_path: Path) -> None:
    ds_dir = tmp_path / "gaas"
    write_deepmd(ds_dir, 8, 16, seed=41)
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"

        resp = bp.request(10, "dataset.register", {"path": str(ds_dir), "name": "g"})
        done = wait_job(bp, resp["result"]["job_id"])
        ds_id = done["result"]["dataset_id"]

        sub = bp.request(
            11,
            "descriptor.submit",
            {
                "dataset_id": ds_id,
                "descriptor_name": "ACE",
                "parameters": {"species": [31, 33], "N": 1},
                "scope": "dataset",
            },
        )
        done = wait_job(bp, sub["result"]["job_id"], timeout=300)
        assert done["status"] == "COMPLETED", done
        run_id = done["result"]["run_id"]

        # PCA
        pca = bp.request(12, "analysis.pca", {"run_id": run_id})
        done = wait_job(bp, pca["result"]["job_id"], timeout=120)
        assert done["status"] == "COMPLETED", done
        analysis_id = done["result"]["analysis_id"]
        assert done["result"]["n_points"] == 8

        payload = bp.request(13, "result.get_pca", {"analysis_id": analysis_id})
        pts = payload["result"]["points"]
        assert len(pts) == 8
        assert pts[0]["frame"] == 0 and pts[7]["frame"] == 7
        assert payload["result"]["explained_variance"][0] > 0
        assert pts[0]["energy_per_atom"] is not None  # color-by data attached

        # repeated request reuses the persisted PCA artifact instead of
        # creating another analysis job
        cached = bp.request(18, "analysis.pca", {"run_id": run_id})["result"]
        assert cached["job_id"] is None
        assert cached["analysis_id"] == analysis_id
        assert cached["cache"]["existing_analysis_id"] == analysis_id

        # PCA atom mode: one point per atom row (8 frames x 16 atoms = 128)
        pca_atom = bp.request(15, "analysis.pca", {"run_id": run_id, "mode": "atom"})
        done = wait_job(bp, pca_atom["result"]["job_id"], timeout=120)
        assert done["status"] == "COMPLETED", done
        a_id = done["result"]["analysis_id"]
        assert done["result"]["n_points"] == 8 * 16
        a_payload = bp.request(16, "result.get_pca", {"analysis_id": a_id})
        a_pts = a_payload["result"]["points"]
        assert a_payload["result"]["mode"] == "atom"
        assert len(a_pts) == 8 * 16
        assert a_pts[0]["atom"] == 0 and a_pts[0]["frame"] == 0
        assert a_pts[20]["frame"] == 1 and a_pts[20]["atom"] == 4
        assert a_pts[127]["frame"] == 7 and a_pts[127]["atom"] == 15

        # Property correlation must use the same verified atom rows and finish
        # as a real analysis job; force_magnitude is only available at atom
        # granularity.
        property_atom = bp.request(
            19,
            "analysis.property_correlation",
            {"run_id": run_id, "mode": "atom", "property": "force_magnitude", "folds": 3},
        )
        property_done = wait_job(bp, property_atom["result"]["job_id"], timeout=120)
        assert property_done["status"] == "COMPLETED", property_done
        property_preview = bp.request(20, "analysis.get", {"analysis_id": property_done["result"]["analysis_id"]})
        assert property_preview["result"]["preview"]["property"] == "force_magnitude"
        assert property_preview["result"]["preview"]["sample_count"] == 8 * 16

        # bad mode rejected before any job is created
        bad = bp.request(17, "analysis.pca", {"run_id": run_id, "mode": "bogus"})
        assert bad["error"]["code"] == "INVALID_PARAMS", bad

        # heatmap for frame 2 (atom level: 16 atoms x <=256 features)
        hm = bp.request(14, "result.heatmap", {"run_id": run_id, "frame_index": 2})
        h = hm["result"]
        assert len(h["atoms"]) == 16
        assert len(h["values"]) == 16 and len(h["values"][0]) == 16
        assert h["atomOffset"] == 2 * 16
    finally:
        assert bp.close() == 0
