"""IPC contract coverage for the Analysis method catalogue."""

from __future__ import annotations

from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from make_fixtures import write_deepmd  # noqa: E402

from test_backend_smoke import BackendProcess  # noqa: E402
from test_dataset_flow import wait_job  # noqa: E402


def test_analysis_method_catalog_over_ipc(tmp_path: Path) -> None:
    ds_dir = tmp_path / "gaas"
    write_deepmd(ds_dir, 8, 16, seed=51)
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        registered = bp.request(1, "dataset.register", {"path": str(ds_dir), "name": "analysis-ipc"})
        ds_done = wait_job(bp, registered["result"]["job_id"])
        dataset_id = ds_done["result"]["dataset_id"]
        submitted = bp.request(
            2,
            "descriptor.submit",
            {
                "dataset_id": dataset_id,
                "descriptor_name": "ACE",
                "parameters": {"species": [31, 33], "N": 1},
                "scope": "dataset",
            },
        )
        descriptor_done = wait_job(bp, submitted["result"]["job_id"], timeout=300)
        assert descriptor_done["status"] == "COMPLETED", descriptor_done
        run_id = descriptor_done["result"]["run_id"]

        sequence = 10

        def run(method: str, params: dict) -> str:
            nonlocal sequence
            response = bp.request(sequence, method, params)
            sequence += 1
            assert "result" in response, response
            result = response["result"]
            assert result["analysis_id"]
            assert result["job_id"]
            done = wait_job(bp, result["job_id"], timeout=300)
            assert done["status"] == "COMPLETED", done
            return done["result"]["analysis_id"]

        projection_ids = [
            run("analysis.umap", {"run_id": run_id, "n_neighbors": 3, "min_dist": 0.1, "seed": 42}),
            run("analysis.tsne", {"run_id": run_id, "perplexity": 3, "max_iter": 250, "seed": 42}),
        ]
        assert projection_ids
        run("analysis.neighbors", {"run_id": run_id, "k": 2})
        run("analysis.similarity", {"run_id": run_id, "k": 2, "query_index": 0})

        for algorithm, params in (
            ("kmeans", {"n_clusters": 2}),
            ("dbscan", {"eps": 2.0, "min_samples": 2}),
            ("hdbscan", {"min_cluster_size": 2}),
            ("agglomerative", {"n_clusters": 2}),
        ):
            run("analysis.cluster", {"run_id": run_id, "algorithm": algorithm, **params, "seed": 42})
        for algorithm, params in (
            ("knn", {"k": 2}),
            ("lof", {"k": 3, "contamination": 0.25}),
            ("isolation_forest", {"contamination": 0.25, "n_estimators": 10}),
            ("mahalanobis", {"contamination": 0.25}),
        ):
            run("analysis.outlier", {"run_id": run_id, "algorithm": algorithm, **params, "seed": 42})
        for algorithm, params in (
            ("fps", {"n_samples": 4}),
            ("random", {"n_samples": 4}),
            ("stratified", {"n_samples": 4}),
            ("cluster_representative", {"n_samples": 4, "n_clusters": 2}),
            ("per_element", {"n_samples": 4, "mode": "atom"}),
        ):
            run("analysis.sampling", {"run_id": run_id, "algorithm": algorithm, **params, "seed": 42})

        run("analysis.coverage", {"reference_run_id": run_id, "query_run_id": run_id, "chunk_size": 3, "reference_chunk_size": 3})
        run("analysis.compare", {"left_run_id": run_id, "right_run_id": run_id})
        quality_id = run("analysis.feature_variance", {"run_id": run_id, "top_k": 4})
        run("analysis.feature_correlation", {"run_id": run_id, "top_k": 4, "heatmap_features": 4})
        run("analysis.effective_dimension", {"run_id": run_id})
        run("analysis.trajectory", {"run_id": run_id, "frame_start": 0, "frame_end": 7, "frame_step": 1})
        run("analysis.drift", {"reference_run_id": run_id, "query_run_id": run_id, "chunk_size": 3, "reference_chunk_size": 3})
        run("analysis.sensitivity", {"run_ids": [run_id, run_id]})

        listed = bp.request(sequence, "analysis.list", {"run_id": run_id})
        sequence += 1
        assert listed["result"]
        got = bp.request(sequence, "analysis.get", {"analysis_id": quality_id})
        sequence += 1
        assert got["result"]["status"] == "COMPLETED"
        preview = bp.request(sequence, "analysis.preview", {"analysis_id": quality_id, "limit": 20})
        sequence += 1
        assert preview["result"]["kind"] == "feature_variance"
        chunk = bp.request(sequence, "analysis.chunk", {"analysis_id": quality_id, "array": "variance", "limit": 4})
        sequence += 1
        assert len(chunk["result"]["data"]) == 4

        export_path = tmp_path / "analysis-subset.json"
        export_id = run("analysis.export", {"run_id": run_id, "indices": [0, 2], "mode": "structure", "format": "json", "output_path": str(export_path)})
        assert export_id
        assert export_path.is_file()

        deleted = bp.request(sequence, "analysis.delete", {"analysis_id": quality_id})
        assert deleted["result"]["ok"] is True
        missing = bp.request(sequence + 1, "analysis.get", {"analysis_id": quality_id})
        assert missing["error"]["code"] == "ANALYSIS_NOT_FOUND"
    finally:
        assert bp.close() == 0
