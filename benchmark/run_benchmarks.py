"""Measured costs for the paper's evaluation section.

    .venv/Scripts/python.exe benchmark/run_benchmarks.py [--suite NAME] [--quick]

Three suites, matching docs/plan Epic-008: **descriptor** (engine throughput
and thread scaling), **analysis** (how the quadratic algorithms grow with
sample count), and **storage** (reading and writing a result matrix).

Each measurement reports wall time, throughput and the process high-water
memory. Results land in ``benchmark/results/<utc>.json`` and a table goes to
stdout.

This is deliberately *not* a CI gate. Wall-clock numbers on shared runners
vary by an order of magnitude, and the register-time gate in
``.github/workflows/ci.yml`` already had to stop measuring a cold-start
contention cost it could not control. CI checks that the harness runs; the
numbers are read, not asserted.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np  # noqa: E402

from mdescriptor_studio_backend.analysis import StructureDescriptorMatrix  # noqa: E402
from mdescriptor_studio_backend.analysis.algorithms.kernel import kernel  # noqa: E402
from mdescriptor_studio_backend.analysis.algorithms.pca import pca  # noqa: E402
from mdescriptor_studio_backend.analysis.algorithms.umap import umap  # noqa: E402
from mdescriptor_studio_backend.datasets.base import DatasetFrame  # noqa: E402
from mdescriptor_studio_backend.mdescriptor_adapter import EngineAdapter  # noqa: E402
from mdescriptor_studio_backend.services.descriptor_service import _process_rss_bytes  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"
# Structures per size class. --quick drops to the smallest only.
DESCRIPTOR_SCALES = (256, 2048)
ANALYSIS_SCALES = (500, 2000, 8000)
STORAGE_SHAPE = (20_000, 512)


def measure(runs, repeat=3):
    """Median wall time and throughput over `repeat` runs of a zero-arg callable."""
    gc.collect()
    before = _process_rss_bytes()
    times = []
    result = None
    for _ in range(repeat):
        started = time.perf_counter()
        result = runs()
        times.append(time.perf_counter() - started)
    after = _process_rss_bytes()
    elapsed = statistics.median(times)
    return {
        "seconds": round(elapsed, 4),
        "spread_seconds": [round(min(times), 4), round(max(times), 4)],
        "peak_rss_mb": round(max(0, (after or 0) - (before or 0)) / 1e6, 1) if before and after else None,
        "rss_high_water_mb": round((after or 0) / 1e6, 1) or None,
        "result": result,
    }


def synthetic_frames(count, atoms=16, seed=7):
    """Seeded displaced diamond cells: periodic, defensible sizes, no model
    weights and no dataset files, so the descriptor cost is the engine's own."""
    rng = np.random.default_rng(seed)
    lattice = 5.43
    frames = []
    for index in range(count):
        base = rng.random((atoms, 3)) * lattice
        frames.append(DatasetFrame(
            numbers=np.full(atoms, 14, dtype=np.int64),
            positions=base,
            cell=np.eye(3) * (lattice + 0.2 * index / max(1, count)),
            pbc=np.array([True, True, True]),
            index=index,
            id=f"frame_{index}",
        ))
    return frames


def descriptor_suite(quick):
    adapter = EngineAdapter()
    # Model-free, so the numbers describe the engine rather than a network
    # download. ACSF needs only the species list the controls would send anyway.
    by_name = {candidate.lower(): candidate for candidate in adapter.list_names()}
    name = next((by_name[key] for key in ("acsf", "soap", "coulombmatrix") if key in by_name), None)
    if name is None:
        return [{"skipped": f"no model-free descriptor among {sorted(by_name)[:8]}"}]
    descriptor = adapter.build(name, {"species": [14]}, device="cpu")
    scales = DESCRIPTOR_SCALES[:1] if quick else DESCRIPTOR_SCALES
    rows = []
    for structures in scales:
        frames = synthetic_frames(structures)
        batch = adapter.to_structure_batch(frames)

        def compute():
            result = adapter.compute(descriptor, batch)
            return int(np.asarray(result.values).shape[-1])

        timing = measure(compute)
        rows.append({
            "case": f"{name} · {structures} structures",
            "structures_per_second": round(structures / timing["seconds"], 1),
            "features": timing["result"],
            "seconds": timing["seconds"],
            "spread_seconds": timing["spread_seconds"],
            "rss_high_water_mb": timing["rss_high_water_mb"],
        })
    if not quick:
        # Thread scaling is the one number a reviewer of an MD tool will ask for.
        threads = [1, 2, min(8, os.cpu_count() or 2)]
        for count in sorted(set(threads)):
            threaded = adapter.build(name, {"species": [14]}, device="cpu", num_threads=count)
            batch = adapter.to_structure_batch(synthetic_frames(DESCRIPTOR_SCALES[1]))

            def compute():
                return len(np.asarray(adapter.compute(threaded, batch).values))

            timing = measure(compute)
            rows.append({
                "case": f"{name} · thread scaling · {count} threads",
                "structures_per_second": round(DESCRIPTOR_SCALES[1] / timing["seconds"], 1),
                "seconds": timing["seconds"],
                "spread_seconds": timing["spread_seconds"],
                "rss_high_water_mb": timing["rss_high_water_mb"],
            })
    return rows


def analysis_matrices(samples, features=64, seed=11):
    rng = np.random.default_rng(seed)
    values = rng.normal(size=(samples, features)) + samples / 1e4
    return StructureDescriptorMatrix(
        values, np.arange(samples), sample_ids=[f"frame:{i}" for i in range(samples)]
    )


def analysis_suite(quick):
    algorithms = {
        "pca (linear)": lambda samples: pca(samples, {"preprocess": "standardized"}),
        "kernel (rbf, capped)": lambda samples: kernel(samples, {"kernel": "rbf", "max_samples": 2000}),
        "umap (exact kNN)": lambda samples: umap(samples, {"n_neighbors": 15}),
    }
    scales = ANALYSIS_SCALES[:2] if quick else ANALYSIS_SCALES
    rows = []
    for label, run in algorithms.items():
        for samples in scales:
            matrix = analysis_matrices(samples)
            timing = measure(lambda: run(matrix), repeat=1 if samples > 4000 else 3)
            rows.append({
                "case": f"{label} · {samples} samples",
                "seconds": timing["seconds"],
                "spread_seconds": timing["spread_seconds"],
                "samples_per_second": round(samples / timing["seconds"], 1),
                "rss_high_water_mb": timing["rss_high_water_mb"],
            })
    return rows


def storage_suite(quick, tmp_dir):
    rows = []
    rows_count, features = (2_000, 512) if quick else STORAGE_SHAPE
    for dtype in (np.float32, np.float64):
        matrix = np.random.default_rng(3).normal(size=(rows_count, features)).astype(dtype)
        path = tmp_dir / f"values_{np.dtype(dtype).name}.npy"
        write = measure(lambda: np.save(path, matrix, allow_pickle=False) or path.stat().st_size, repeat=3)
        read = measure(lambda: np.load(path).shape, repeat=3)
        for phase, timing in (("write", write), ("read", read)):
            megabytes = (write["result"] or 0) / 1e6
            rows.append({
                "case": f"{phase} {np.dtype(dtype).name} · {rows_count}x{features} ({megabytes:.0f} MB)",
                "seconds": timing["seconds"],
                "mb_per_second": round(megabytes / timing["seconds"], 1) if timing["seconds"] else None,
                "rss_high_water_mb": timing["rss_high_water_mb"],
            })
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", choices=("descriptor", "analysis", "storage", "all"), default="all")
    parser.add_argument("--quick", action="store_true", help="smallest size class only, for CI smoke runs")
    arguments = parser.parse_args()

    started = datetime.now(timezone.utc)
    os.environ.setdefault("OMP_NUM_THREADS", str(os.cpu_count() or 1))
    results = {"started_at": started.isoformat(timespec="seconds"), "engine_version": None, "rows": []}
    try:
        results["engine_version"] = EngineAdapter().runtime_info().get("version")
    except Exception as error:  # noqa: BLE001 - a missing engine must not hide the other suites
        results["engine_error"] = f"{type(error).__name__}: {error}"

    out_dir = RESULTS_DIR / started.strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    if arguments.suite in ("descriptor", "all"):
        results["rows"] += descriptor_suite(arguments.quick)
    if arguments.suite in ("analysis", "all"):
        results["rows"] += analysis_suite(arguments.quick)
    if arguments.suite in ("storage", "all"):
        results["rows"] += storage_suite(arguments.quick, out_dir)
    (out_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    results["results_path"] = str(out_dir / "results.json")

    print(f"\nmdescriptor {results.get('engine_version')} · {'quick' if arguments.quick else 'full'} · {started:%Y-%m-%d %H:%M}Z")
    print(f"{'case':<46}{'seconds':>10}{'rate':>18}{'RSS MB':>10}")
    for row in results["rows"]:
        rate = next((f"{row[key]:,.1f} {unit}" for key, unit in (
            ("structures_per_second", "str/s"), ("samples_per_second", "smp/s"), ("mb_per_second", "MB/s"))
            if row.get(key)), "")
        print(f"{row.get('case', row.get('skipped', '?')):<46}{row.get('seconds') or 0:>10}"
              f"{rate:>18}{row.get('rss_high_water_mb') or 0:>10,.0f}")
    print(f"\nwrote {results.get('results_path')}")


if __name__ == "__main__":
    main()
