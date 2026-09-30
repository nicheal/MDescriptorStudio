"""Resume an interrupted pre-registered sweep from its run_results.jsonl.

Every completed (optimizer, seed) row is kept as-is; only missing pairs are
re-run, appended into the same directory, and the aggregate views + checksum
manifest are regenerated (audit P1-06 checkpoint discipline). The machine
sleeping through a long sweep is the expected failure mode — this script is
the recovery path, not an exception.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from genetic_vs_random import (  # noqa: E402
    DATASET_ID,
    RUN_ID,
    _environment_facts,
    _load_preregistration,
    _load_run_config,
    _summarise,
    _write_sha256sums,
    run_once,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="pre-registration config used by the original sweep")
    parser.add_argument("--results-dir", required=True, help="existing sweep directory holding run_results.jsonl")
    parser.add_argument("--dry-run", action="store_true", help="print the pending (seed, optimizer) pairs and exit")
    args = parser.parse_args()

    config = _load_preregistration(Path(args.config))
    out_dir = Path(args.results_dir)
    jsonl = out_dir / "run_results.jsonl"
    done: set[tuple[str, int]] = set()
    if jsonl.is_file():
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done.add((str(row["optimizer"]), int(row["seed"])))
    elif not args.dry_run:
        raise SystemExit(f"{jsonl} does not exist — resume targets an interrupted sweep")

    groups = [str(g) for g in config["groups"]]
    budget = int(config["budget_evaluations"])
    seed_base = int(config["seed_base"])
    repeats = int(config["repeats"])
    anchor_frames = [int(i) for i in config["anchor_frames"]]
    selection_strategy = str(config["selection_strategy"])

    total = repeats * len(groups)
    pending = [
        (seed_base + repeat, group)
        for repeat in range(repeats)
        for group in groups
        if (group, seed_base + repeat) not in done
    ]
    print(f"{len(done)}/{total} runs already recorded; {len(pending)} pending", flush=True)
    for seed, group in pending:
        print(f"  pending: seed={seed} {group}", flush=True)
    if args.dry_run:
        return 0
    if not pending:
        print("nothing to do; refreshing summaries and checksums", flush=True)
        pending_rows = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
    else:
        run_row, dataset_row = _load_run_config()
        for index, (seed, optimizer) in enumerate(pending, start=1):
            print(f"[resume {index}/{len(pending)}] {optimizer} seed={seed} budget={budget}", flush=True)
            run = run_once(
                optimizer=optimizer,
                seed=seed,
                budget=budget,
                run_row=run_row,
                dataset_row=dataset_row,
                anchor_frames=anchor_frames,
                selection_strategy=selection_strategy,
            )
            with open(jsonl, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(run, ensure_ascii=False) + "\n")
            print(
                f"    evals={run['evaluations']} accepted={run['accepted']} "
                f"unique={run['unique_novel_environments']} ({run['unique_per_100_evals']}/100) "
                f"coverage={run['final_coverage_radius']} wall={run['wall_seconds']}s",
                flush=True,
            )
        pending_rows = [json.loads(l) for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]

    if not (out_dir / "environment.json").is_file():
        (out_dir / "environment.json").write_text(json.dumps(_environment_facts(), indent=2), encoding="utf-8")
    summary = _summarise(pending_rows)
    (out_dir / "genetic_vs_random.json").write_text(
        json.dumps({"config": config, "runs": pending_rows, "summary": summary}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (out_dir / "summary.json").write_text(
        json.dumps({"config": config | {"dataset": DATASET_ID, "run": RUN_ID}, "summary": summary}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_sha256sums(
        out_dir,
        ["config.used.json", "environment.json", "run_results.jsonl", "genetic_vs_random.json", "summary.json"],
    )
    print(f"resume complete: {len(pending_rows)}/{total} runs; summaries and checksums refreshed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
