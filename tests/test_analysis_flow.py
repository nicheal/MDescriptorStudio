"""PCA + heatmap flow over IPC (M5 backend acceptance)."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from make_fixtures import write_deepmd  # noqa: E402

from test_backend_smoke import BackendProcess  # noqa: E402
from test_dataset_flow import wait_job  # noqa: E402


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
        assert pts[0]["energy"] is not None  # color-by data attached

        # heatmap for frame 2 (atom level: 16 atoms x <=256 features)
        hm = bp.request(14, "result.heatmap", {"run_id": run_id, "frame_index": 2})
        h = hm["result"]
        assert len(h["atoms"]) == 16
        assert len(h["values"]) == 16 and len(h["values"][0]) == 16
        assert h["atomOffset"] == 2 * 16
    finally:
        assert bp.close() == 0
