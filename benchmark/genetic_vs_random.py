"""G4-2 scientific benchmark: Random vs GA under identical budgets.

For each repeat seed, generation runs share the same seed pool, the same
frozen reference archive and the same engine budget; only the optimizer
differs. Core metrics (per the G4-2 plan): unique novel environments per
100 descriptor evaluations and the final accepted-only coverage radius.

The harness replicates the generation worker's assembly
(``services/generation_service.py::_run_generation``) against the local app
data — same archive construction, same seed-pool sampling, same evaluator —
minus DB rows, the job queue and the artifact writer, so runs are fully
deterministic and nothing the app owns is written.

Usage:
    .venv/Scripts/python.exe benchmark/genetic_vs_random.py --pilot
    .venv/Scripts/python.exe benchmark/genetic_vs_random.py                # full: 20 repeats
    .venv/Scripts/python.exe benchmark/genetic_vs_random.py --repeats 5 --budget 2000

Optimizers compared (``--optimizers``): ``random`` (seed-pool sampling only),
``random-reuse`` (Random with reuse_accepted_seeds — the stronger baseline),
``genetic`` (G4-1). Results land in
``benchmark/results/<utc>/genetic_vs_random.json`` (gitignored directory).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from mdescriptor_studio_backend.analysis.sampling import fit_scaling  # noqa: E402
from mdescriptor_studio_backend.datasets import create_adapter  # noqa: E402
from mdescriptor_studio_backend.generation.archive import (  # noqa: E402
    DescriptorArchive,
    LocalEnvironmentArchive,
)
from mdescriptor_studio_backend.generation.constraints import build_constraints  # noqa: E402
from mdescriptor_studio_backend.generation.engine import GenerationEngine  # noqa: E402
from mdescriptor_studio_backend.generation.evaluator import DescriptorEvaluator  # noqa: E402
from mdescriptor_studio_backend.generation.models import Budget, OperatorSpec, StructureCandidate  # noqa: E402
from mdescriptor_studio_backend.generation.registry import GENERATION_REGISTRY  # noqa: E402
from mdescriptor_studio_backend.mdescriptor_adapter import EngineAdapter  # noqa: E402

_local = os.environ.get("LOCALAPPDATA")
DATA_ROOT = Path(os.environ.get("MDS_DATA_DIR") or (Path(_local) if _local else Path.home()) / "MDescriptorStudio")
# The production worker passes distance_workers (= cpu count) into the
# archives and the engine; a single-threaded harness would spend 90% of the
# wall clock in nearest-row scans, not in the search being benchmarked.
WORKERS = max(1, os.cpu_count() or 1)

DATASET_ID = "ds_d56748fb4391"  # carbon, 6738 extxyz frames
RUN_ID = "run_57a8b8c40286"  # NEP, atom-level rows, 35 features, device=cpu
SEED_POOL_CAP = 512  # services/generation_service.py::_SEED_POOL_CAP

OPERATORS = {
    "atomic_displacement": {"enabled": True, "max_sigma": 0.15},
    "isotropic_strain": {"enabled": True, "max_strain": 0.05},
    "anisotropic_strain": {"enabled": True, "max_strain": 0.05},
    "cell_shear": {"enabled": True, "max_shear": 0.05},
}
OBJECTIVE = {
    "type": "local_environment_novelty",
    "aggregation": "top_fraction_mean",
    "top_fraction": 0.2,
    "novelty_threshold": 0.25,
    "scaling": "robust",
}
CONSTRAINTS = {"min_distance_mode": "covalent", "min_distance_factor": 0.7}
OPTIMIZER_PARAMS = {"children_per_seed": 8, "batch_accept": 8, "n_seeds": 64}


def _load_run_config() -> tuple[dict, dict]:
    db = sqlite3.connect(f"file:{(DATA_ROOT / 'database.sqlite').as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        run_row = dict(db.execute("SELECT * FROM descriptor_runs WHERE id=?", (RUN_ID,)).fetchone())
        dataset_row = dict(db.execute("SELECT * FROM datasets WHERE id=?", (DATASET_ID,)).fetchone())
    finally:
        db.close()
    if not run_row or run_row["status"] != "COMPLETED":
        raise SystemExit(f"descriptor run {RUN_ID} is not COMPLETED")
    return run_row, dataset_row


def _reference_matrices(run_row: dict, needs_atomic: bool):
    """The worker's reference path: atom rows from values.npy + row_offsets,
    pooled per structure for the structure archive."""
    result_path = Path(run_row["result_path"])
    values = np.asarray(np.load(result_path / "values.npy", allow_pickle=False), dtype=np.float64)
    if values.ndim > 2:
        values = values.reshape(values.shape[0], -1)
    offsets_file = result_path / "row_offsets.npy"
    if not offsets_file.is_file():
        return values, None
    offsets = np.asarray(np.load(offsets_file, allow_pickle=False), dtype=np.int64)
    structure = np.empty((len(offsets) - 1, values.shape[1]), dtype=np.float64)
    for index in range(len(offsets) - 1):
        lo, hi = int(offsets[index]), int(offsets[index + 1])
        structure[index] = values[lo:hi].mean(axis=0) if hi > lo else 0.0
    return structure, (values if needs_atomic else None)


def _seed_pool(adapter, frame_total: int, seed: int) -> list[StructureCandidate]:
    """Worker seed-pool sampling, bit-for-bit (rng(seed), linspace, shuffle)."""
    rng = np.random.default_rng(seed)
    candidates = np.asarray(range(frame_total), dtype=np.int64)
    take = min(len(candidates), SEED_POOL_CAP)
    indices = candidates[np.linspace(0, len(candidates) - 1, take, dtype=np.int64)]
    rng.shuffle(indices)
    pool = []
    for position, frame_index in enumerate(indices.tolist()):
        frame = adapter.get_frame(int(frame_index))
        pool.append(
            StructureCandidate.from_frame(
                frame,
                candidate_id=f"seed_{position}",
                parent_frame=int(frame_index),
            )
        )
    return pool


def run_once(*, optimizer: str, seed: int, budget: int, run_row: dict, dataset_row: dict, anchor_frames: list[int]) -> dict:
    label = optimizer  # reported name ("random-reuse" stays distinct)
    needs_atomic = True  # local_environment_novelty requires atom rows
    reference_structure, reference_atomic = _reference_matrices(run_row, needs_atomic)

    structure_scaling, _ = fit_scaling(reference_structure, OBJECTIVE["scaling"])
    structure_archive = DescriptorArchive(reference_structure, structure_scaling, workers=WORKERS)
    local_archive = None
    if reference_atomic is not None:
        local_scaling, _ = fit_scaling(reference_atomic, OBJECTIVE["scaling"])
        local_archive = LocalEnvironmentArchive(reference_atomic, local_scaling, workers=WORKERS)

    frame_adapter = create_adapter(Path(dataset_row["source_path"]), dataset_row["format"])
    engine_adapter = EngineAdapter()
    evaluator = DescriptorEvaluator(
        engine_adapter,
        run_row["descriptor_name"],
        json.loads(run_row.get("parameters_json") or "{}"),
        device="cpu",
        num_threads=WORKERS,
    )

    objective = GENERATION_REGISTRY.build_objective(dict(OBJECTIVE))
    operators = [
        GENERATION_REGISTRY.build_operator(OperatorSpec(name, dict(params)))
        for name, params in OPERATORS.items()
    ]
    params = dict(OPTIMIZER_PARAMS)
    registry_name = optimizer
    if optimizer == "random-reuse":
        registry_name = "random"
        params["reuse_accepted_seeds"] = True
    region_radius = None
    if optimizer in ("target_region", "genetic-target", "pso-target"):
        # G5-2: targeting is a search target consumed by random/genetic/pso.
        # Labels keep the report continuous; radius calibrated on this
        # descriptor (one mutation moves it ~13-18 robust units).
        if optimizer == "target_region":
            registry_name = "random"
        else:
            registry_name = optimizer.removesuffix("-target")
        region_radius = 15.0
    optimizer_obj = GENERATION_REGISTRY.build_optimizer(registry_name, operators, params)
    # Every dataset frame's descriptor is a row of the frozen reference; the
    # worker path (generation_service) does exactly this for real runs,
    # including forcing the anchor frames into the seed pool. Anchors are
    # handed to EVERY optimizer so the proximity baseline (random) is
    # measurable; only target_region uses them for proposals.
    from mdescriptor_studio_backend.analysis.sampling import apply_scaling

    if reference_structure.shape[0] != len(frame_adapter):
        raise SystemExit("reference rows do not align 1:1 with dataset frames")
    anchor_descriptors = tuple(
        apply_scaling(structure_scaling, reference_structure[index][np.newaxis, :])[0] for index in anchor_frames
    )
    seed_pool = _seed_pool(frame_adapter, len(frame_adapter), seed)
    if optimizer == "target_region":
        anchor_set = set(anchor_frames)
        rest = [c for c in seed_pool if c.parent_frame not in anchor_set]
        seed_pool = (
            [StructureCandidate.from_frame(frame_adapter.get_frame(int(i)), candidate_id=f"seed_{p}", parent_frame=int(i))
             for p, i in enumerate(sorted(anchor_set))]
            + rest
        )
    seed_descriptors = tuple(
        (
            apply_scaling(structure_scaling, reference_structure[candidate.parent_frame][np.newaxis, :])[0]
            if candidate.parent_frame is not None and 0 <= int(candidate.parent_frame) < reference_structure.shape[0]
            else None
        )
        for candidate in seed_pool
    )
    constraints = build_constraints(CONSTRAINTS)

    engine = GenerationEngine(
        seed_pool=seed_pool,
        evaluator=evaluator,
        structure_archive=structure_archive,
        local_archive=local_archive,
        objective=objective,
        optimizer=optimizer_obj,
        constraints=constraints,
        budget=Budget(
            max_evaluations=budget,
            max_accepted=500,
            max_generations=10_000,  # the evaluation budget is the binding cap
            no_improvement_rounds=None,
            discovery_window=0,  # same-budget comparison: no early stopping
        ),
        rng=np.random.default_rng(seed),
        n_seeds=params["n_seeds"],
        workers=WORKERS,
        seed_descriptors=seed_descriptors,
        anchor_descriptors=anchor_descriptors,
        region_radius=region_radius,
    )
    started = time.perf_counter()
    result = engine.run()
    elapsed = time.perf_counter() - started

    rounds = [record.to_json() for record in result.rounds]
    total_unique = sum(r["unique_novel_environments"] or 0 for r in rounds)
    total_evals = sum(r["evaluations"] for r in rounds)
    coverage = next((r["coverage_radius"] for r in reversed(rounds) if r["coverage_radius"] is not None), None)
    proximity = None
    if anchor_descriptors:
        from mdescriptor_studio_backend.analysis.sampling import apply_scaling

        # result.evaluated carries RAW structure rows; the anchors are scaled.
        accepted_desc = [
            apply_scaling(structure_scaling, np.asarray(record.structure_descriptor, dtype=np.float64)[np.newaxis, :])[0]
            for record in result.evaluated
            if record.accepted
        ]
        distances = [
            min(float(np.linalg.norm(desc - anchor)) for anchor in anchor_descriptors)
            for desc in accepted_desc
        ]
        if distances:
            radius = float(params.get("region_radius", 1.0))
            proximity = {
                "median": round(float(np.median(distances)), 4),
                "p90": round(float(np.quantile(distances, 0.9)), 4),
                "within_radius": round(sum(1 for d in distances if d <= radius) / len(distances), 4),
            }
    return {
        "optimizer": label,
        "anchor_frames": list(anchor_frames),
        "anchor_proximity": proximity,
        "seed": seed,
        "stopped_by": result.stopped_by,
        "evaluations": total_evals,
        "accepted": result.accepted_count,
        "unique_novel_environments": total_unique,
        "unique_per_100_evals": round(100.0 * total_unique / total_evals, 6) if total_evals else None,
        "final_coverage_radius": coverage,
        "wall_seconds": round(elapsed, 1),
        "rounds": rounds,
    }


def _summarise(runs: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for run in runs:
        groups.setdefault(run["optimizer"], []).append(run)
    summary = {}
    for name, group in groups.items():
        per_100 = [r["unique_per_100_evals"] for r in group]
        coverage = [r["final_coverage_radius"] for r in group]
        accepted = [r["accepted"] for r in group]
        summary[name] = {
            "repeats": len(group),
            "unique_per_100_evals": {
                "mean": statistics.fmean(per_100),
                "median": statistics.median(per_100),
                "stdev": statistics.stdev(per_100) if len(per_100) > 1 else 0.0,
            },
            "final_coverage_radius": {
                "mean": statistics.fmean(coverage),
                "median": statistics.median(coverage),
                "stdev": statistics.stdev(coverage) if len(coverage) > 1 else 0.0,
            },
            "accepted": {"mean": statistics.fmean(accepted)},
        }
        proximities = [r["anchor_proximity"] for r in group if r.get("anchor_proximity")]
        if proximities:
            summary[name]["anchor_proximity"] = {
                "median_of_medians": statistics.median([p["median"] for p in proximities]),
                "mean_within_radius": statistics.fmean([p["within_radius"] for p in proximities]),
            }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--budget", type=int, default=10_000)
    parser.add_argument("--pilot", action="store_true", help="1 repeat, small budget, quick smoke")
    parser.add_argument("--optimizers", default="random,random-reuse,genetic")
    parser.add_argument("--anchor-frames", default="1322,5075", help="target_region anchor dataset frames (must satisfy the run constraints)")
    args = parser.parse_args()

    repeats = 1 if args.pilot else args.repeats
    budget = 400 if args.pilot else args.budget
    optimizers = [name.strip() for name in args.optimizers.split(",")]

    out_dir = REPO / "benchmark" / "results" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)

    run_row, dataset_row = _load_run_config()
    runs: list[dict] = []
    for repeat in range(repeats):
        seed = 1000 + repeat
        for optimizer in optimizers:
            print(f"[repeat {repeat + 1}/{repeats}] {optimizer} seed={seed} budget={budget}", flush=True)
            anchor_frames = [int(i) for i in str(args.anchor_frames).split(",") if i.strip()]
            run = run_once(
                optimizer=optimizer, seed=seed, budget=budget, run_row=run_row, dataset_row=dataset_row,
                anchor_frames=anchor_frames,
            )
            runs.append(run)
            print(
                f"    evals={run['evaluations']} accepted={run['accepted']} "
                f"unique={run['unique_novel_environments']} ({run['unique_per_100_evals']}/100) "
                f"coverage={run['final_coverage_radius']} wall={run['wall_seconds']}s",
                flush=True,
            )
            # Incremental checkpoint so an interrupted sweep still leaves data.
            (out_dir / "genetic_vs_random.json").write_text(
                json.dumps({"args": vars(args) | {"dataset": DATASET_ID, "run": RUN_ID}, "runs": runs, "summary": _summarise(runs)}, indent=2),
                encoding="utf-8",
            )

    print(json.dumps(_summarise(runs), indent=2))
    print(f"results: {out_dir / 'genetic_vs_random.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
