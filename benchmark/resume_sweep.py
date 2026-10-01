"""Resume an interrupted pre-registered sweep from its run_results.jsonl.

Every completed (optimizer, seed) row is kept as-is; only missing pairs are
re-run, appended into the same directory, and the aggregate views + checksum
manifest are regenerated (audit P1-06 checkpoint discipline). The machine
sleeping through a long sweep is the expected failure mode — this script is
the recovery path, not an exception.

Material safety (external review 2026-10-01): the pre-registration's
dataset/descriptor ids are applied to the harness module before any pending
run starts, the sweep directory's frozen config.used.json is verified
against the config, and every recorded row's material identity is checked.
A resumed table can never silently mix materials — the previous resume path
resolved its data bindings from module defaults and would have appended
default-carbon rows to a second-material sweep.
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
    DEFAULT_DATASET_ID,
    DEFAULT_RUN_ID,
    _environment_facts,
    _load_preregistration,
    _load_run_config,
    _summarise,
    _write_sha256sums,
    apply_preregistration,
    run_once,
)


def _verify_used_config(config: dict, used_path: Path) -> None:
    """The directory being resumed must have been produced by THIS config.

    Only the keys the frozen copy carries are compared — keys added to the
    pre-registration after the sweep started (annotations, not scenario
    changes) must not block a legitimate resume. The free-text 'note' is
    excluded; scenario divergence is not.
    """
    if not used_path.is_file():
        return  # legacy directory without a frozen copy — rows are checked per-row below
    used = json.loads(used_path.read_text(encoding="utf-8"))
    diverged = sorted(key for key, value in used.items() if key != "note" and value != config.get(key))
    if diverged:
        raise SystemExit(
            f"{used_path} diverges from the given pre-registration in {diverged} — "
            "refusing to resume a different experiment into this directory"
        )


def _verify_row_identities(rows: list[dict], config: dict) -> None:
    """Every recorded row must belong to the config's material.

    Rows written before rows carried material identity (pre-2026-10-01) can
    only have been produced against the harness-default carbon ids, so they
    are admissible for the default experiment only.
    """
    dataset_id = str(config["dataset_id"])
    run_id = str(config["descriptor_run_id"])
    legacy_admissible = dataset_id == DEFAULT_DATASET_ID and run_id == DEFAULT_RUN_ID
    for line_number, row in enumerate(rows, start=1):
        row_dataset = row.get("dataset_id")
        row_run = row.get("descriptor_run_id")
        if row_dataset is None and row_run is None:
            if not legacy_admissible:
                raise SystemExit(
                    f"run_results.jsonl row {line_number} predates material identity and the pre-registration "
                    f"targets {dataset_id}/{run_id} — a second-material resume must only extend rows that "
                    "carry its own identity; refusing"
                )
            continue
        if row_dataset != dataset_id or row_run != run_id:
            raise SystemExit(
                f"run_results.jsonl row {line_number} belongs to {row_dataset}/{row_run} but the pre-registration "
                f"targets {dataset_id}/{run_id} — refusing to append a mixed-material table"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="pre-registration config used by the original sweep")
    parser.add_argument("--results-dir", required=True, help="existing sweep directory holding run_results.jsonl")
    parser.add_argument("--dry-run", action="store_true", help="print the pending (seed, optimizer) pairs and exit")
    args = parser.parse_args()

    config = _load_preregistration(Path(args.config))
    out_dir = Path(args.results_dir)
    _verify_used_config(config, out_dir / "config.used.json")
    jsonl = out_dir / "run_results.jsonl"
    rows: list[dict] = []
    if jsonl.is_file():
        rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    elif not args.dry_run:
        raise SystemExit(f"{jsonl} does not exist — resume targets an interrupted sweep")
    _verify_row_identities(rows, config)
    done = {(str(row["optimizer"]), int(row["seed"])) for row in rows}

    repeats = int(config["repeats"])
    seed_base = int(config["seed_base"])
    total = repeats * len(config["groups"])
    pending = [
        (seed_base + repeat, str(group))
        for repeat in range(repeats)
        for group in config["groups"]
        if (str(group), seed_base + repeat) not in done
    ]
    print(f"{len(done)}/{total} runs already recorded; {len(pending)} pending", flush=True)
    for seed, group in pending:
        print(f"  pending: seed={seed} {group}", flush=True)
    if args.dry_run:
        return 0

    if pending:
        # The pre-registration's material ids reach the harness module BEFORE
        # any run is launched — never the module defaults.
        params = apply_preregistration(config)
        run_row, dataset_row = _load_run_config()
        for index, (seed, optimizer) in enumerate(pending, start=1):
            print(f"[resume {index}/{len(pending)}] {optimizer} seed={seed} budget={params['budget']}", flush=True)
            run = run_once(
                optimizer=optimizer,
                seed=seed,
                budget=params["budget"],
                run_row=run_row,
                dataset_row=dataset_row,
                anchor_frames=params["anchor_frames"],
                selection_strategy=params["selection_strategy"],
            )
            with open(jsonl, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(run, ensure_ascii=False) + "\n")
            print(
                f"    evals={run['evaluations']} accepted={run['accepted']} "
                f"unique={run['unique_novel_environments']} ({run['unique_per_100_evals']}/100) "
                f"coverage={run['final_coverage_radius']} wall={run['wall_seconds']}s",
                flush=True,
            )
    rows = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]

    if not (out_dir / "environment.json").is_file():
        (out_dir / "environment.json").write_text(json.dumps(_environment_facts(), indent=2), encoding="utf-8", newline="\n")
    summary = _summarise(rows)
    (out_dir / "genetic_vs_random.json").write_text(
        json.dumps({"config": config, "runs": rows, "summary": summary}, indent=2, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )
    (out_dir / "summary.json").write_text(
        json.dumps(
            {
                "config": config | {"dataset": str(config["dataset_id"]), "run": str(config["descriptor_run_id"])},
                "summary": summary,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
        newline="\n",
    )
    _write_sha256sums(
        out_dir,
        ["config.used.json", "environment.json", "run_results.jsonl", "genetic_vs_random.json", "summary.json"],
    )
    print(f"resume complete: {len(rows)}/{total} runs; summaries and checksums refreshed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
