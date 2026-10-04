"""IPC contract coverage for the generation.* method family."""

from __future__ import annotations

import json
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
            # P1-01: the strictly deduplicated counterpart of the raw count.
            "unique_novel_environments",
            # gen-5: screening rejections summed over rounds (gates the
            # archived-metric display row on the frontend).
            "rejected_screening",
            # gen-5: the permutation-invariant counterpart — present because
            # this run's records carry the new fields — and the archived
            # rate (this run has no screening, so archived equals unique).
            "strict_unique_v2",
            "archived_unique_novel_environments",
        }
        assert pca["discovery"]["original_structures"] == 12
        assert pca["discovery"]["generated_environments"] > 0
        assert pca["discovery"]["novel_environments"] > 0
        assert 0 <= pca["discovery"]["unique_novel_environments"] <= pca["discovery"]["novel_environments"]
        assert 0 <= pca["discovery"]["strict_unique_v2"] <= pca["discovery"]["novel_environments"]
        assert pca["discovery"]["archived_unique_novel_environments"] == pca["discovery"]["unique_novel_environments"]

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


def test_generation_resume_after_interruption(tmp_path: Path) -> None:
    """Productized R5.5 acceptance: run → hard-kill the backend mid-flight →
    restart on the same data dir → the run is INTERRUPTED and resumable →
    generation.resume completes it byte-identically to an uninterrupted
    fixed-seed reference run in a separate data dir."""
    ds_dir = tmp_path / "gaas"
    write_deepmd(ds_dir, 12, 64, seed=31)
    budget = {"max_evaluations": 100_000, "max_accepted": 10_000, "max_generations": 6}

    def _descriptor_run(process: BackendProcess, vid: int, dataset: str) -> str:
        sub = process.request(
            vid,
            "descriptor.submit",
            {"dataset_id": dataset, "descriptor_name": "ACE", "parameters": {"species": [31, 33], "N": 1}, "scope": "dataset"},
        )
        done = wait_job(process, sub["result"]["job_id"], timeout=300)
        assert done["status"] == "COMPLETED", done
        return done["result"]["run_id"]

    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        ds_id = register_dataset(bp, 400, ds_dir)
        run_id = _descriptor_run(bp, 401, ds_id)
        submitted = _submit_generation(bp, 402, ds_id, run_id, budget=budget)
        generation_id = submitted["generation_id"]

        # Wait for the first persisted round boundary, then hard-kill the
        # backend mid-run (a graceful shutdown would cancel jobs instead of
        # leaving them INTERRUPTED).
        deadline = time.monotonic() + 180
        while True:
            row = bp.request(403, "generation.get", {"id": generation_id})["result"]
            if (row.get("last_snapshot_generation") or 0) >= 1:
                break
            assert time.monotonic() < deadline, row
            assert row["status"] in ("QUEUED", "RUNNING"), row
            time.sleep(0.2)
        bp.proc.kill()
        bp.proc.wait(timeout=15)

        bp = BackendProcess(tmp_path)
        assert bp.read_line()["event"] == "backend.ready"
        row = bp.request(404, "generation.get", {"id": generation_id})["result"]
        assert row["status"] == "INTERRUPTED", row
        assert row["resumable"] == 1
        assert (row.get("last_snapshot_generation") or 0) >= 1

        resume_resp = bp.request(405, "generation.resume", {"id": generation_id})
        assert "result" in resume_resp, resume_resp
        resumed_job = resume_resp["result"]
        finished = wait_job(bp, resumed_job["job_id"], timeout=600)
        assert finished["status"] == "COMPLETED", finished
        row = bp.request(406, "generation.get", {"id": generation_id})["result"]
        assert row["status"] == "COMPLETED"
        assert row["resumable"] == 0
        resumed_preview = row["preview"]
        assert resumed_preview["stopped_by"] == "max_generations"
        assert len(resumed_preview["rounds"]) == 6

        # Snapshot v3: the resumed run's descriptor-space map is COMPLETE.
        pca = bp.request(407, "generation.pca", {"id": generation_id})["result"]
        assert len(pca["evaluated"]) == row["evaluations"]

        # The geometry spool restarted mid-run: the manifest records the
        # offset, and pre-offset ACCEPTED map points still resolve.
        manifest = json.loads(
            (tmp_path / "generation" / generation_id / "manifest.json").read_text(encoding="utf-8")
        )
        offset = manifest["files"]["evaluated_structures"]["offset"]
        assert offset >= 1
        first_accepted = next(i for i, accepted in enumerate(pca["evaluated_accepted"]) if accepted)
        assert first_accepted < offset
        frame = bp.request(408, "generation.structure", {"id": generation_id, "index": first_accepted})
        assert "result" in frame, frame
    finally:
        if bp.proc is not None and bp.proc.poll() is None:
            bp.close()

    # Reference: the same fixed-seed request, uninterrupted, in its own data
    # dir (same dataset content, so every input to the engine matches).
    ref_data = tmp_path / "reference_data"
    ref_data.mkdir()
    bp_ref = BackendProcess(ref_data)
    try:
        assert bp_ref.read_line()["event"] == "backend.ready"
        ref_ds = register_dataset(bp_ref, 420, ds_dir)
        ref_run = _descriptor_run(bp_ref, 421, ref_ds)
        ref_submitted = _submit_generation(bp_ref, 422, ref_ds, ref_run, budget=budget)
        wait_job(bp_ref, ref_submitted["job_id"], timeout=600)
        ref_row = bp_ref.request(423, "generation.get", {"id": ref_submitted["generation_id"]})["result"]
        assert ref_row["status"] == "COMPLETED"
    finally:
        bp_ref.close()

    # The continuation must equal the uninterrupted run — round records are
    # compared as full JSON payloads (positions/fitness ride the rounds).
    assert ref_row["preview"]["stopped_by"] == resumed_preview["stopped_by"]
    assert ref_row["preview"]["rounds"] == resumed_preview["rounds"]
    assert ref_row["accepted_count"] == row["accepted_count"]
    assert ref_row["evaluations"] == row["evaluations"]
