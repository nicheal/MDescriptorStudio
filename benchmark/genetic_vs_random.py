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

Anchor roles (audit P0-03): ``metric_anchors`` (the ``--anchor-frames``
dataset frames) feed ONLY the proximity statistics; the engine receives
``search_anchors``, empty for every untargeted label so the untargeted
baselines can never drift into their targeted branch. Targeting labels
(``target_region``, ``genetic-target``, ``pso-target``) get search anchors
plus the worker's forced anchor insertion into the seed pool. Every group
shares one identical full seed pool (audit P0-04: same parent resources),
and the proximity ``within_radius`` fraction is computed at the same
experiment radius for all groups.

Usage:
    .venv/Scripts/python.exe benchmark/genetic_vs_random.py --pilot
    .venv/Scripts/python.exe benchmark/genetic_vs_random.py                # full: 20 repeats
    .venv/Scripts/python.exe benchmark/genetic_vs_random.py --repeats 5 --budget 2000

Optimizers compared (``--optimizers``): ``random`` (seed-pool sampling only),
``random-reuse`` (Random with reuse_accepted_seeds — the stronger baseline),
``genetic`` (G4-1), ``pso`` (G5-1), and the targeted ``target_region`` /
``genetic-target`` / ``pso-target`` labels. Results land in
``benchmark/results/<utc>/genetic_vs_random.json`` (gitignored directory).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from mdescriptor_studio_backend.analysis.sampling import apply_scaling, fit_scaling  # noqa: E402
from mdescriptor_studio_backend.datasets import create_adapter  # noqa: E402
from mdescriptor_studio_backend.generation.archive import (  # noqa: E402
    DescriptorArchive,
    LocalEnvironmentArchive,
)
from mdescriptor_studio_backend.generation.constraints import build_constraints  # noqa: E402
from mdescriptor_studio_backend.generation.engine import GENERATION_ALGORITHM_VERSION, GenerationEngine  # noqa: E402
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
# Pre-2026-10-01 harness default (carbon). Rows written before rows carried
# material identity can only have been produced against these ids; the resume
# path uses that fact to admit legacy rows for the default experiment only.
DEFAULT_DATASET_ID = DATASET_ID
DEFAULT_RUN_ID = RUN_ID
SEED_POOL_CAP = 512  # services/generation_service.py::_SEED_POOL_CAP

# Labels whose search policy consumes the anchors; every other label runs
# strictly untargeted (search_anchors = ()) regardless of --anchor-frames.
TARGETING_LABELS = {"target_region", "genetic-target", "pso-target"}

# Identity stamps written into environment.json and checked against every
# pre-registration (external review 2026-10-01): the counting space is part
# of the experiment — the published R4 pack (results 20260929T043150Z) was
# produced by pre-2026-09-30 code whose strict dedup counted in RAW space
# ("strict-unique-raw"); the current engine counts in the archive scaled
# space. A config stamped with another caliber is refused instead of
# silently mixing counting spaces across materials or code versions.
HARNESS_VERSION = "2026-10-01"
METRIC_CALIBER = "strict-unique-scaled"

# The run's full budget contract (external review: caps that actually bind
# must be part of the registration, not harness-internal folklore).
MAX_ACCEPTED = 500
MAX_GENERATIONS = 10_000

KNOWN_OPTIMIZERS = {"random", "random-reuse", "genetic", "pso"} | TARGETING_LABELS
KNOWN_SELECTION_STRATEGIES = ("structure_fps_v1", "local_incremental_maximin_v1")
PRIMARY_METRICS = ("unique_per_100_evals",)
KNOWN_SECONDARY_METRICS = (
    "final_coverage_radius",
    "anchor_proximity.median",
    "anchor_proximity.p90",
    "anchor_proximity.within_radius",
    "accepted",
    "wall_seconds",
    "peak_rss_mb",
    "unique_novel_environments",
    "unique_per_100_evals",
)


def search_anchor_role(label: str, metric_anchors: tuple) -> tuple[tuple, bool]:
    """P0-03: the only path metric anchors may take into an engine run.

    Targeting labels search with them; every other label runs strictly
    untargeted — the engine never sees the anchors, so a baseline's
    proposals cannot drift into the optimizer's targeted branch through
    measurement inputs."""
    targeting = label in TARGETING_LABELS
    return (metric_anchors if targeting else ()), targeting
# Experiment-wide region radius for the proximity statistics (robust-scaled
# descriptor units; one displacement mutation moves this descriptor single
# to low-double-digit units on both benchmarked materials — measured
# 2026-10-01, see docs/reviews/2026-10-01-pdcunip-review-response.md). All
# groups report within_radius at THIS radius — never a per-group default.
REGION_RADIUS = 15.0

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


def _load_run_config(dataset_id: str | None = None, run_id: str | None = None) -> tuple[dict, dict]:
    """Load the descriptor run + dataset rows the harness benchmarks against.

    Explicit ids (a pre-registration's) win over the module defaults. The
    run must belong to the dataset and its recorded dataset fingerprint must
    still match — the harness must not silently benchmark a different
    material pairing than the pre-registration froze (external review
    2026-10-01; the app's submit/worker gates mirror this).
    """
    dataset_id = dataset_id or DATASET_ID
    run_id = run_id or RUN_ID
    db = sqlite3.connect(f"file:{(DATA_ROOT / 'database.sqlite').as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        run_row = db.execute("SELECT * FROM descriptor_runs WHERE id=?", (run_id,)).fetchone()
        dataset_row = db.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
    finally:
        db.close()
    if not dataset_row:
        raise SystemExit(f"dataset {dataset_id} is not registered in the app database")
    dataset_row = dict(dataset_row)
    if not run_row or run_row["status"] != "COMPLETED":
        raise SystemExit(f"descriptor run {run_id} is not COMPLETED")
    run_row = dict(run_row)
    if run_row.get("dataset_id") != dataset_id:
        raise SystemExit(
            f"descriptor run {run_id} belongs to dataset {run_row.get('dataset_id')!r}, "
            f"not the pre-registered {dataset_id!r} — reconcile the pre-registration"
        )
    metadata_path = Path(run_row["result_path"]) / "metadata.json"
    if not metadata_path.is_file():
        raise SystemExit(f"descriptor run {run_id} has no result metadata.json — fingerprint freshness cannot be verified")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("dataset_fingerprint") != dataset_row.get("fingerprint"):
        raise SystemExit(
            f"descriptor run {run_id} was computed against a different dataset fingerprint "
            "than the dataset currently carries — re-run the descriptor before benchmarking"
        )
    return run_row, dataset_row


def apply_preregistration(config: dict) -> dict:
    """Point the module's data bindings at a pre-registered config and return
    the resolved run parameters.

    The main harness and resume_sweep MUST resolve their material through
    this helper — reading the ids off module globals silently benchmarks the
    default carbon dataset under a second-material pre-registration
    (the 2026-10-01 review's resume finding).
    """
    global DATASET_ID, RUN_ID
    DATASET_ID = str(config["dataset_id"])
    RUN_ID = str(config["descriptor_run_id"])
    return {
        "seed_base": int(config["seed_base"]),
        "repeats": int(config["repeats"]),
        "budget": int(config["budget_evaluations"]),
        "groups": [str(name) for name in config["groups"]],
        "anchor_frames": [int(index) for index in config["anchor_frames"]],
        "selection_strategy": str(config["selection_strategy"]),
    }


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


def _seed_pool(adapter, frame_total: int, seed: int, force_anchors: list[int] | None = None) -> list[StructureCandidate]:
    """Worker seed-pool sampling, bit-for-bit (rng(seed), linspace, shuffle),
    plus the worker's forced-anchor insertion when given: the anchor frames
    (deduplicated, sorted) go first, then the sampled indices minus the
    anchors, truncated to the pool cap — services/generation_service.py
    does exactly this for every run that declares anchor frames."""
    rng = np.random.default_rng(seed)
    candidates = np.asarray(range(frame_total), dtype=np.int64)
    take = min(len(candidates), SEED_POOL_CAP)
    indices = candidates[np.linspace(0, len(candidates) - 1, take, dtype=np.int64)]
    rng.shuffle(indices)
    if force_anchors:
        anchor_array = np.asarray(sorted(set(force_anchors)), dtype=np.int64)
        keep = ~np.isin(indices, anchor_array)
        indices = np.concatenate([anchor_array, indices[keep]])[:SEED_POOL_CAP]
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


def run_once(
    *,
    optimizer: str,
    seed: int,
    budget: int,
    run_row: dict,
    dataset_row: dict,
    anchor_frames: list[int],
    selection_strategy: str = "structure_fps_v1",
) -> dict:
    label = optimizer  # reported name ("random-reuse" stays distinct)
    # P0-03: metric vs search anchors. The metric anchors below feed ONLY
    # the proximity statistics; the engine receives search_anchors, empty
    # for every untargeted label, so a baseline's proposals can never enter
    # the optimizer's targeted branch through measurement inputs.
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
    if reference_structure.shape[0] != len(frame_adapter):
        raise SystemExit("reference rows do not align 1:1 with dataset frames")
    metric_anchors = tuple(
        apply_scaling(structure_scaling, reference_structure[index][np.newaxis, :])[0] for index in anchor_frames
    )
    search_anchors, targeting = search_anchor_role(label, metric_anchors)

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
    if optimizer == "target_region":
        registry_name = "random"
    elif optimizer in TARGETING_LABELS:
        registry_name = optimizer.removesuffix("-target")
    region_radius = REGION_RADIUS if targeting else None
    optimizer_obj = GENERATION_REGISTRY.build_optimizer(registry_name, operators, params)
    # Identical full seed pool for every group (same parent resources);
    # targeting groups additionally match the worker's forced anchors.
    seed_pool = _seed_pool(frame_adapter, len(frame_adapter), seed, force_anchors=anchor_frames)
    seed_descriptors = tuple(
        (
            apply_scaling(structure_scaling, reference_structure[candidate.parent_frame][np.newaxis, :])[0]
            if candidate.parent_frame is not None and 0 <= int(candidate.parent_frame) < reference_structure.shape[0]
            else None
        )
        for candidate in seed_pool
    )
    constraints = build_constraints(CONSTRAINTS)
    if anchor_frames:
        # Worker parity (services/generation_service.py): anchors must index
        # the dataset and satisfy the run's own geometry constraints — a
        # pathological anchor wastes every mutation of itself on rejections.
        if not all(0 <= int(index) < len(frame_adapter) for index in anchor_frames):
            raise SystemExit("anchor_frames contains a dataset frame index out of range")
        for index in anchor_frames:
            anchor_candidate = StructureCandidate.from_frame(
                frame_adapter.get_frame(int(index)), candidate_id=f"anchor_{index}", parent_frame=int(index)
            )
            verdict = constraints.validate(anchor_candidate)
            if not verdict.valid:
                raise SystemExit(
                    f"anchor frame {index} violates the run's geometry constraints: " + "; ".join(verdict.reasons)
                )

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
            max_accepted=MAX_ACCEPTED,
            max_generations=MAX_GENERATIONS,  # the evaluation budget is the binding cap
            no_improvement_rounds=None,
            discovery_window=0,  # same-budget comparison: no early stopping
        ),
        rng=np.random.default_rng(seed),
        n_seeds=params["n_seeds"],
        workers=WORKERS,
        seed_descriptors=seed_descriptors,
        anchor_descriptors=search_anchors,
        region_radius=region_radius,
        selection_strategy=selection_strategy,
    )
    # Runtime targeting assertion (P0-03): the anchors that reached the
    # engine are exactly the search anchors — a targeting label cannot run
    # untargeted, and a baseline cannot run targeted. The state-level check
    # is random-registry-only: GA/PSO carry targeting inside their draw
    # logic and do not report a targeting block in state_dict.
    assert bool(engine.anchor_descriptors) == targeting, "search anchors disagree with the run's targeting flag"
    started = time.perf_counter()
    with _PeakRSS() as rss:
        result = engine.run()
    if targeting and registry_name == "random":
        assert optimizer_obj.state_dict().get("targeting"), "targeting run shows no targeting state"
    elapsed = time.perf_counter() - started

    rounds = [record.to_json() for record in result.rounds]
    total_unique = sum(r["unique_novel_environments"] or 0 for r in rounds)
    total_evals = sum(r["evaluations"] for r in rounds)
    coverage = next((r["coverage_radius"] for r in reversed(rounds) if r["coverage_radius"] is not None), None)
    proximity = None
    if metric_anchors:
        # result.evaluated carries RAW structure rows; the anchors are scaled.
        accepted_desc = [
            apply_scaling(structure_scaling, np.asarray(record.structure_descriptor, dtype=np.float64)[np.newaxis, :])[0]
            for record in result.evaluated
            if record.accepted
        ]
        distances = [
            min(float(np.linalg.norm(desc - anchor)) for anchor in metric_anchors)
            for desc in accepted_desc
        ]
        if distances:
            proximity = {
                "median": round(float(np.median(distances)), 4),
                "p90": round(float(np.quantile(distances, 0.9)), 4),
                "within_radius": round(sum(1 for d in distances if d <= REGION_RADIUS) / len(distances), 4),
            }
    return {
        "optimizer": label,
        "targeting_enabled": targeting,
        "selection_strategy": selection_strategy,
        # Material identity on every row (external review 2026-10-01): a
        # resumed or concatenated table can be checked for mixed materials.
        "dataset_id": dataset_row["id"],
        "descriptor_run_id": run_row["id"],
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
        "peak_rss_mb": rss.peak_mb,
        "rounds": rounds,
    }


def _load_preregistration(path: Path) -> dict:
    """Load and validate a frozen pre-registration config (audit R4; full
    contract per the 2026-10-01 external review).

    Two layers. The frozen scenario keys must match the harness constants
    exactly — a divergence means the config and the code disagree about the
    experiment, which is precisely what pre-registration exists to surface.
    And the bookkeeping contract (metric identity incl. counting space,
    algorithm/harness versions, material binding, budget caps, group and
    metric names, well-typed counts) must hold at LOAD time — a config that
    only fails mid-sweep, or worse runs, defeats the registration. Exit
    instead of silently benchmarking a different scenario.
    """
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("kind") != "generation-benchmark-preregistration":
        raise SystemExit(f"{path} is not a generation benchmark pre-registration")
    for key, frozen in (
        ("operators", OPERATORS),
        ("objective", OBJECTIVE),
        ("constraints", CONSTRAINTS),
        ("optimizer_params", OPTIMIZER_PARAMS),
    ):
        if config.get(key) != frozen:
            raise SystemExit(f"pre-registration '{key}' diverges from the harness constants — reconcile first")
    if float(config.get("region_radius", -1)) != REGION_RADIUS:
        raise SystemExit("pre-registration 'region_radius' diverges from the harness REGION_RADIUS")
    if config.get("metric_caliber") != METRIC_CALIBER:
        raise SystemExit(
            f"pre-registration 'metric_caliber' is {config.get('metric_caliber')!r} but this harness counts "
            f"in {METRIC_CALIBER!r} — re-baseline or reconcile first (never mix counting spaces)"
        )
    if config.get("algorithm_version") != GENERATION_ALGORITHM_VERSION:
        raise SystemExit(
            f"pre-registration 'algorithm_version' is {config.get('algorithm_version')!r}, "
            f"the engine reports {GENERATION_ALGORITHM_VERSION!r}"
        )
    harness_min = str(config.get("harness_min_version") or "")
    if not harness_min or harness_min > HARNESS_VERSION:
        raise SystemExit(f"pre-registration 'harness_min_version' ({harness_min!r}) exceeds this harness ({HARNESS_VERSION})")
    if config.get("selection_strategy") not in KNOWN_SELECTION_STRATEGIES:
        raise SystemExit(f"pre-registration 'selection_strategy' must be one of {list(KNOWN_SELECTION_STRATEGIES)}")
    if config.get("primary_metric") not in PRIMARY_METRICS:
        raise SystemExit(f"pre-registration 'primary_metric' must be one of {list(PRIMARY_METRICS)}")
    for key in ("dataset_id", "descriptor_run_id", "preregistered_at", "metric_definition"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise SystemExit(f"pre-registration is missing a usable '{key}'")
    for key, minimum in (("repeats", 1), ("budget_evaluations", 1), ("seed_base", 0)):
        value = config.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise SystemExit(f"pre-registration '{key}' must be an integer >= {minimum}")
    groups = config.get("groups")
    if (
        not isinstance(groups, list)
        or not groups
        or len(set(groups)) != len(groups)
        or not all(group in KNOWN_OPTIMIZERS for group in groups)
    ):
        raise SystemExit(f"pre-registration 'groups' must be a duplicate-free list of known labels {sorted(KNOWN_OPTIMIZERS)}")
    primary_groups = config.get("primary_groups")
    if not isinstance(primary_groups, list) or not primary_groups or not all(group in groups for group in primary_groups):
        raise SystemExit("pre-registration 'primary_groups' must be a non-empty subset of 'groups'")
    secondary = config.get("secondary_metrics")
    if (
        not isinstance(secondary, list)
        or not secondary
        or len(set(secondary)) != len(secondary)
        or not all(metric in KNOWN_SECONDARY_METRICS for metric in secondary)
    ):
        raise SystemExit(f"pre-registration 'secondary_metrics' must be a duplicate-free subset of {list(KNOWN_SECONDARY_METRICS)}")
    anchors = config.get("anchor_frames")
    if (
        not isinstance(anchors, list)
        or not anchors
        or not all(isinstance(index, int) and not isinstance(index, bool) and index >= 0 for index in anchors)
    ):
        raise SystemExit("pre-registration 'anchor_frames' must be a non-empty list of non-negative frame indices")
    for key, cap in (("max_accepted", MAX_ACCEPTED), ("max_generations", MAX_GENERATIONS)):
        if key in config and config[key] != cap:
            raise SystemExit(f"pre-registration '{key}' diverges from the harness budget cap {cap}")
    return config


class _PeakRSS:
    """Peak resident-memory sampler for one run (audit R4: real wall time
    AND peak RSS under an identical budget). Uses psutil when available;
    degrades to None otherwise. Samples the whole process, which includes
    the descriptor engine and the archives — the run's true footprint."""

    def __init__(self) -> None:
        try:
            import psutil

            self._process = psutil.Process()
            self._peak = 0.0
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._ok = True
        except Exception:
            self._ok = False

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._peak = max(self._peak, self._process.memory_info().rss / (1024.0 * 1024.0))
            except Exception:
                pass
            self._stop.wait(0.5)

    def __enter__(self) -> "_PeakRSS":
        if self._ok:
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._ok:
            self._stop.set()
            self._thread.join(timeout=2.0)

    @property
    def peak_mb(self) -> float | None:
        return round(self._peak, 1) if self._ok else None


def _environment_facts() -> dict:
    """Reproducibility metadata for the results directory (audit P1-06)."""
    import importlib.metadata
    import platform

    packages = {}
    for name in ("numpy", "scipy", "mdescriptor"):
        try:
            packages[name] = importlib.metadata.version(name)
        except Exception:
            packages[name] = None
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "packages": packages,
        # Identity stamps (external review 2026-10-01): a results directory
        # states which counting space and code produced it.
        "harness_version": HARNESS_VERSION,
        "metric_caliber": METRIC_CALIBER,
        "algorithm_version": GENERATION_ALGORITHM_VERSION,
    }


def _write_sha256sums(out_dir: Path, names: list[str]) -> None:
    import hashlib

    lines = []
    for name in names:
        path = out_dir / name
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}")
    (out_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _distribution(values: list) -> dict | None:
    """mean/median/stdev/p90 over the non-None entries; None when absent."""
    present = [value for value in values if value is not None]
    if not present:
        return None
    return {
        "mean": statistics.fmean(present),
        "median": statistics.median(present),
        "stdev": statistics.stdev(present) if len(present) > 1 else 0.0,
        "p90": float(np.quantile(present, 0.9)),
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
            "unique_per_100_evals": _distribution(per_100),
            "final_coverage_radius": _distribution(coverage),
            "accepted": {"mean": statistics.fmean(accepted)},
        }
        wall = _distribution([r.get("wall_seconds") for r in group])
        if wall is not None:
            summary[name]["wall_seconds"] = wall
        rss = _distribution([r.get("peak_rss_mb") for r in group])
        if rss is not None:
            summary[name]["peak_rss_mb"] = rss
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
    parser.add_argument("--anchor-frames", default="1322,5075", help="dataset frames for the proximity metric (all groups) and, for the targeting labels, the search anchors (must satisfy the run constraints)")
    parser.add_argument(
        "--config",
        default=None,
        help="pre-registered config (e.g. benchmark/config.json): fixes repeats/budget/groups/strategy "
        "and writes run_results.jsonl + SHA256SUMS for reproducibility (audit P1-06/R4)",
    )
    args = parser.parse_args()

    config = None
    if args.config:
        config = _load_preregistration(Path(args.config))
        params = apply_preregistration(config)
    else:
        params = {
            "seed_base": 1000,
            "repeats": 1 if args.pilot else args.repeats,
            "budget": 400 if args.pilot else args.budget,
            "groups": [name.strip() for name in args.optimizers.split(",")],
            "anchor_frames": [],
            "selection_strategy": "structure_fps_v1",
        }
    repeats, budget = params["repeats"], params["budget"]
    optimizers, selection_strategy = params["groups"], params["selection_strategy"]

    out_dir = REPO / "benchmark" / "results" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    if config is not None:
        (out_dir / "config.used.json").write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        (out_dir / "environment.json").write_text(json.dumps(_environment_facts(), indent=2), encoding="utf-8", newline="\n")

    run_row, dataset_row = _load_run_config()
    runs: list[dict] = []
    results_jsonl = out_dir / "run_results.jsonl"
    for repeat in range(repeats):
        seed = params["seed_base"] + repeat
        for optimizer in optimizers:
            print(f"[repeat {repeat + 1}/{repeats}] {optimizer} seed={seed} budget={budget}", flush=True)
            anchor_frames = params["anchor_frames"] or [int(i) for i in str(args.anchor_frames).split(",") if i.strip()]
            run = run_once(
                optimizer=optimizer, seed=seed, budget=budget, run_row=run_row, dataset_row=dataset_row,
                anchor_frames=anchor_frames, selection_strategy=selection_strategy,
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
                newline="\n",
            )
            if config is not None:
                with open(results_jsonl, "a", encoding="utf-8", newline="\n") as fh:
                    fh.write(json.dumps(run, ensure_ascii=False) + "\n")

    summary = _summarise(runs)
    print(json.dumps(summary, indent=2))
    print(f"results: {out_dir / 'genetic_vs_random.json'}")
    if config is not None:
        (out_dir / "summary.json").write_text(
            json.dumps({"args": vars(args) | {"dataset": DATASET_ID, "run": RUN_ID}, "summary": summary}, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        _write_sha256sums(
            out_dir,
            ["config.used.json", "environment.json", "run_results.jsonl", "genetic_vs_random.json", "summary.json"],
        )
        print(f"pre-registered run complete; per-seed rows in {results_jsonl}, checksums in {out_dir / 'SHA256SUMS'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
