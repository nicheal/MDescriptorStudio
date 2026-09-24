"""IPC contract coverage for the generation.* method family."""

from __future__ import annotations

import time
from pathlib import Path

from make_fixtures import write_deepmd

from conftest import BackendProcess, register_dataset, wait_job


def _submit_generation(bp: BackendProcess, vid: int, ds_id: str, run_id: str, budget: dict | None = None, seed=42) -> dict:
    return bp.request(
        vid,
        "generation.submit",
        {
            "dataset_id": ds_id,
            "descriptor_run_id": run_id,
            "optimizer": "random",
            "optimizer_params": {"children_per_seed": 4, "batch_accept": 4, "n_seeds": 4},
            "objective": {
                "type": "local_environment_novelty",
                "aggregation": "top_fraction_mean",
                "top_fraction": 0.2,
                "novelty_threshold": 0.25,
            },
            "operators": {
                "atomic_displacement": {"enabled": True, "max_sigma": 0.1},
                "isotropic_strain": {"enabled": True, "max_strain": 0.03},
            },
            "constraints": {"min_distance_mode": "none"},
            "budget": budget or {"max_evaluations": 64, "max_accepted": 8, "max_generations": 10},
            "seed": seed,
        },
    )["result"]


def test_generation_lifecycle_catalog_submit_get_materialize(tmp_path: Path) -> None:
    ds_dir = tmp_path / "gaas"
    write_deepmd(ds_dir, 12, 64, seed=31)
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        ds_id = register_dataset(bp, 100, ds_dir)

        sub = bp.request(
            101,
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

        catalog = bp.request(102, "generation.catalog")["result"]
        assert {o["name"] for o in catalog["objectives"]} >= {"novelty", "local_environment_novelty", "composite"}
        assert {o["name"] for o in catalog["operators"]} >= {
            "atomic_displacement",
            "isotropic_strain",
            "anisotropic_strain",
            "cell_shear",
        }
        assert catalog["optimizers"][0]["name"] == "random"

        submitted = _submit_generation(bp, 103, ds_id, run_id)
        assert submitted["cached"] is False
        generation_id = submitted["generation_id"]
        finished = wait_job(bp, submitted["job_id"], timeout=600)
        assert finished["status"] == "COMPLETED", finished
        assert finished["result"]["accepted"] >= 1

        got = bp.request(104, "generation.get", {"id": generation_id})["result"]
        assert got["status"] == "COMPLETED"
        assert got["artifact_complete"] is True
        assert got["evaluations"] >= 1
        assert got["accepted_count"] >= 1
        preview = got["preview"]
        assert preview["stopped_by"] in {"max_evaluations", "max_accepted", "max_generations", "no_improvement", "target_novelty"}
        assert len(preview["rounds"]) >= 1
        record = preview["rounds"][-1]
        for key in ("evaluations", "accepted", "rejected_geometry", "rejected_duplicate", "coverage_radius"):
            assert key in record

        # Fixed seed: an identical request resolves to the completed run.
        cached = _submit_generation(bp, 105, ds_id, run_id)
        assert cached["cached"] is True
        assert cached["generation_id"] == generation_id

        # Random seed never caches.
        uncached = _submit_generation(bp, 106, ds_id, run_id, seed="random")
        assert uncached["cached"] is False
        assert uncached["generation_id"] != generation_id
        wait_job(bp, uncached["job_id"], timeout=600)

        listing = bp.request(107, "generation.list", {"dataset_id": ds_id})["result"]
        assert {row["id"] for row in listing} >= {generation_id, uncached["generation_id"]}
        assert all("preview_json" not in row for row in listing)

        # Descriptor-space map: original + evaluated + accepted, with discovery counts.
        pca = bp.request(111, "generation.pca", {"id": generation_id})["result"]
        assert len(pca["original"]) >= 1
        assert len(pca["evaluated"]) == got["evaluations"]
        assert len(pca["evaluated_accepted"]) == len(pca["evaluated"])
        assert pca["evaluated_accepted"].count(True) == got["accepted_count"]
        assert set(pca["discovery"]) == {
            "original_structures",
            "accepted_structures",
            "original_environments",
            "generated_environments",
            "novel_environments",
        }
        assert pca["discovery"]["original_structures"] == 12
        assert pca["discovery"]["generated_environments"] > 0
        assert pca["discovery"]["novel_environments"] > 0

        # Materialize accepted structures into a new, lineage-traceable dataset.
        dest = tmp_path / "gaas_expanded.extxyz"
        mat_resp = bp.request(108, "generation.materialize", {"id": generation_id, "path": str(dest)})
        assert "result" in mat_resp, mat_resp
        mat = mat_resp["result"]
        mat_done = wait_job(bp, mat["job_id"], timeout=120)
        assert mat_done["status"] == "COMPLETED", mat_done
        result = mat_done["result"]
        assert result["lineage"]["operation"] == "dataset_generation"
        assert result["lineage"]["parent_dataset_id"] == ds_id
        reg = bp.request(109, "dataset.register", {"path": str(dest), "name": "gaas_expanded", "lineage": result["lineage"]})
        reg_done = wait_job(bp, reg["result"]["job_id"], timeout=180)
        assert reg_done["status"] == "COMPLETED", reg_done
        child_id = reg_done["result"]["dataset_id"]
        child = bp.request(110, "dataset.get", {"id": child_id})["result"]
        assert child["number_of_frames"] == got["accepted_count"]
    finally:
        bp.close()


def test_generation_submit_validation_errors(tmp_path: Path) -> None:
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        bad = bp.request(200, "generation.submit", {})
        assert bad.get("error"), bad
        assert bad["error"]["code"] == "INVALID_PARAMS"
        unknown = bp.request(
            201,
            "generation.submit",
            {
                "dataset_id": "ds_missing",
                "descriptor_run_id": "run_missing",
                "optimizer": "quantum_annealer",
            },
        )
        assert unknown["error"]["code"] == "INVALID_PARAMS"
    finally:
        bp.close()


def test_generation_cancel_settles_run(tmp_path: Path) -> None:
    ds_dir = tmp_path / "gaas"
    write_deepmd(ds_dir, 12, 64, seed=77)
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        ds_id = register_dataset(bp, 300, ds_dir)
        sub = bp.request(
            301,
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
        submitted = _submit_generation(
            bp,
            302,
            ds_id,
            done["result"]["run_id"],
            budget={"max_evaluations": 1_000_000, "max_accepted": 100_000, "max_generations": 100_000},
        )
        cancelled = bp.request(303, "generation.cancel", {"id": submitted["generation_id"]})["result"]
        assert cancelled["ok"] is True
        # Cancel settles the job synchronously, so its job.finished event may
        # already have been consumed with the cancel reply — poll job.get.
        deadline = time.monotonic() + 60
        while True:
            job = bp.request(304, "job.get", {"id": submitted["job_id"]})["result"]
            if job["status"] == "CANCELLED":
                break
            assert time.monotonic() < deadline, job
            time.sleep(0.2)
        got = bp.request(305, "generation.get", {"id": submitted["generation_id"]})["result"]
        assert got["status"] == "CANCELLED"
    finally:
        bp.close()
