"""Export jobs and the writers for selection, report and dataset formats."""

from __future__ import annotations

import csv
import json
import threading
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from ..datasets.exporters import write_deepmd, write_extxyz
from ..errors import ANALYSIS_INPUT_INVALID, EXPORT_FAILED, AppError
from ..security import (
    UnsafePathError,
    ensure_no_reparse_points,
    open_text_for_write,
    path_within,
    validate_local_path,
)
from .analysis_helpers import ANALYSIS_ALGORITHM_VERSION, _NOW, _view_id


def _cancellable_frames(adapter, frames: list[int], ctx):
    """Resolve dataset frames lazily so a cancelled export stops mid-write.

    Progress is reported on the 250-frame cadence the statistics and materialize
    passes use. Without it a ten-thousand-frame extxyz export sat at 0% for
    minutes - which is indistinguishable from a frozen application - because the
    only reports around this loop were 0 and 1.
    """
    total = len(frames)
    for position, frame_index in enumerate(frames, start=1):
        ctx.check_cancelled()
        if position % 250 == 0 or position == total:
            ctx.progress(position, total, "writing frames")
        yield adapter.get_frame(frame_index)


_DESTINATION_LOCKS: dict[str, tuple[threading.Lock, int]] = {}
_DESTINATION_LOCKS_GUARD = threading.Lock()


@contextmanager
def _destination_lock(target: Path):
    """One writer per destination, process-wide.

    Overwriting an earlier export is this RPC's documented behaviour (unlike
    materialize, which refuses an existing path), so what must not happen is two
    live jobs writing one file *at the same time*: each truncates and each counts
    from zero, so the bytes end up describing neither selection while both rows
    report COMPLETED.
    """
    key = str(target)
    with _DESTINATION_LOCKS_GUARD:
        current = _DESTINATION_LOCKS.get(key)
        lock = current[0] if current is not None else threading.Lock()
        _DESTINATION_LOCKS[key] = (lock, (current[1] if current is not None else 0) + 1)
    try:
        with lock:
            yield
    finally:
        with _DESTINATION_LOCKS_GUARD:
            current = _DESTINATION_LOCKS.get(key)
            if current is None or current[0] is not lock:
                return
            if current[1] <= 1:
                _DESTINATION_LOCKS.pop(key, None)
            else:
                _DESTINATION_LOCKS[key] = (lock, current[1] - 1)


def _identity_records(samples, chosen: list[int]) -> list[dict]:
    """One row per exported sample, keyed by its index in the analysis.

    Frame and sample id come from the same identity table previews use: an
    atom-level run has several samples sharing one frame, and its ids are not
    ``frame:<index>``.
    """
    return [
        {
            "sample_index": index,
            "frame": int(samples.frame[index]),
            "sample_id": samples.sample_ids[index],
        }
        for index in chosen
    ]


class AnalysisExportMixin:
    """Dataset/sampling export writers and export job submission."""

    def submit_export(self, params: dict) -> dict:
        params = dict(params or {})
        input_ids = self._input_ids("export", params)
        if len(input_ids) != 1:
            raise AppError(ANALYSIS_INPUT_INVALID, "export requires exactly one run_id")
        run = self._usable_run(input_ids[0])
        export_format = str(params.get("format") or "json").lower()
        if export_format not in ("json", "csv", "extxyz", "deepmd", "indices", "report"):
            raise AppError(ANALYSIS_INPUT_INVALID, "format must be json, csv, extxyz, deepmd, indices, or report")
        mode = str(params.get("mode") or "structure")
        if mode not in ("structure", "atom"):
            raise AppError(ANALYSIS_INPUT_INVALID, "mode must be structure or atom")
        # The selection is a set of sample indices *in the analysis' own scope*:
        # a view slices the run's samples, so exporting without it writes the
        # frames that merely happen to carry the same numbers in the whole set.
        view_id = _view_id(params)
        # The sampling report carries the provenance of the analysis it came
        # from; every other format only needs the run plus the selection.
        report_analysis = self._analysis_row(str(params["analysis_id"]), include_preview=True) if export_format == "report" and params.get("analysis_id") else None
        if export_format == "report" and report_analysis is None:
            raise AppError(ANALYSIS_INPUT_INVALID, "report export requires the analysis_id of a completed analysis")
        selected = params.get("indices")
        if selected is None:
            selected = []
        if not isinstance(selected, list) or not all(
            isinstance(i, int) and not isinstance(i, bool) and i >= 0 for i in selected
        ):
            raise AppError(ANALYSIS_INPUT_INVALID, "indices must be a list of non-negative integers")
        target = str(params.get("output_path") or "")
        if not target:
            raise AppError(ANALYSIS_INPUT_INVALID, "output_path is required for export")
        try:
            target_path = validate_local_path(target, field="export output path")
        except UnsafePathError as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "export output must be an absolute local path") from exc
        # Every format below opens its destination with O_TRUNC and DeepMD writes a
        # directory, so pointing an export at the file (or folder) the run was
        # computed from would replace the source with a selection of it - and the
        # row would still claim success. materialize refuses this already.
        source_row = self.db.query_one("SELECT source_path FROM datasets WHERE id = ?", (run["dataset_id"],))
        if source_row is not None and source_row.get("source_path"):
            source = Path(str(source_row["source_path"]))
            if path_within(source, target_path) or path_within(target_path, source):
                raise AppError(ANALYSIS_INPUT_INVALID, "export output must be outside the dataset it exports")
        target = str(target_path)
        # The analysis id is part of a report's identity: two different
        # sampling analyses must never share one report cache entry.
        canonical = self._canonical_params({
            "format": export_format,
            "indices": sorted(set(selected)),
            "output_path": target,
            "mode": mode,
            **({"view_id": view_id} if view_id else {}),
            **({"analysis_id": str(params["analysis_id"])} if export_format == "report" and params.get("analysis_id") else {}),
        })
        cache_key = self._analysis_cache_key("export", input_ids, canonical)

        with self._submit_lock:
            # An export is a side effect on a user path, not a pure computation:
            # a COMPLETED row only proves the file was written once. If it is no
            # longer there (moved, deleted, cleaned up) the cached answer would
            # report a success the disk contradicts, so keep claiming under keys
            # no completed row can match until this submission owns a fresh
            # QUEUED row. Two live jobs may therefore share one destination - the
            # per-path lock in `_write_export` keeps them out of each other's
            # bytes, which the key cannot: a different selection of the same run
            # is a different cache key and the same file.
            attempt = 0
            while True:
                analysis_id, early, created = self._claim_analysis_row(
                    analysis_type="export",
                    cache_key=cache_key if attempt == 0 else f"{cache_key}\x1fre-export-{attempt}",
                    canonical_params=canonical,
                    input_ids=input_ids,
                    primary_run_id=run["id"],
                    dataset_ids=[run["dataset_id"]],
                )
                if early is None:
                    break
                if early.get("job_id") is not None or Path(target).exists():
                    return early
                attempt += 1

            def runner(ctx):
                artifact_path: Path | None = None
                try:
                    self._mark_run_running(analysis_id)
                    ctx.progress(0, 1, "writing export")
                    path, written = self._write_export(run, selected, export_format, mode, Path(target), ctx, report_analysis, view_id)
                    artifact_path, manifest = self._commit_artifact(
                        analysis_id,
                        "export",
                        input_ids,
                        canonical,
                        {"arrays": {}, "warnings": [], "export_path": str(path)},
                        {"kind": "export", "format": export_format, "output_path": str(path), "selected_count": written},
                        ctx,
                    )
                    self._complete_analysis_run(
                        analysis_id,
                        {
                            "result_path": str(artifact_path),
                            "finished_at": _NOW(),
                            "artifact_manifest_json": json.dumps(manifest),
                            "preview_json": json.dumps({"kind": "export", "format": export_format, "output_path": str(path), "selected_count": written}),
                            "updated_at": _NOW(),
                        },
                        artifact_path,
                    )
                    ctx.progress(1, 1, "export complete")
                    return {"analysis_id": analysis_id, "output_path": str(path), "selected_count": written}

                except BaseException:
                    # Keep failed runs atomic: a committed artifact must
                    # never outlive the run row that failed to settle.
                    if artifact_path is not None:
                        self._artifacts.remove_quiet(artifact_path)
                    raise
            try:
                job_id = self.jobs.submit("analysis.export", runner, dataset_id=run["dataset_id"], analysis_run_id=analysis_id)
            except Exception:
                if created:
                    self.db.execute("DELETE FROM analysis_runs WHERE id = ?", (analysis_id,))
                raise
            return {"job_id": job_id, "analysis_id": analysis_id, "cache": None}

    def _write_export(self, run: dict, selected: list[int], export_format: str, mode: str, target: Path, ctx, report_analysis: dict | None = None, view_id: str | None = None) -> tuple[Path, int]:
        with _destination_lock(target):
            return self._write_export_to(run, selected, export_format, mode, target, ctx, report_analysis, view_id)

    def _write_export_to(self, run: dict, selected: list[int], export_format: str, mode: str, target: Path, ctx, report_analysis: dict | None = None, view_id: str | None = None) -> tuple[Path, int]:
        """Write the export and report how many entries the file actually holds.

        The caller records that number rather than the length of the request:
        each format normalises the selection differently - indices and report
        dedupe, json and csv drop out-of-range samples, extxyz and DeepMD fold
        samples into the frames they came from - so a row saying "selected 500"
        beside a 312-line file described a list nobody wrote (deep review
        pass 4, C-11).
        """
        # Sample-index and provenance exports need neither the dataset service
        # nor a frame resolution, so they short-circuit before any loading.
        if export_format == "indices":
            if not selected:
                raise AppError(EXPORT_FAILED, "export selection is empty")
            target.parent.mkdir(parents=True, exist_ok=True)
            ensure_no_reparse_points(target.parent)
            with open_text_for_write(target) as fh:
                for index in sorted(set(int(i) for i in selected)):
                    fh.write(f"{index}\n")
            return target, len({int(i) for i in selected})
        if export_format == "report":
            if not selected:
                raise AppError(EXPORT_FAILED, "export selection is empty")
            target.parent.mkdir(parents=True, exist_ok=True)
            ensure_no_reparse_points(target.parent)
            chosen = sorted(set(int(i) for i in selected))
            self._write_sampling_report(run, report_analysis or {}, chosen, target)
            return target, len(chosen)
        if self.datasets is None:
            raise AppError(EXPORT_FAILED, "dataset service is unavailable")
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run["dataset_id"],))
        if dataset is None:
            raise AppError(EXPORT_FAILED, f"dataset {run['dataset_id']} does not exist")
        adapter = self.datasets.adapter_for(dataset)
        count = len(adapter)
        # Analysis selections are sample indices, not dataset frame indices.
        # Resolve them through the same identity table used to build previews;
        # this is essential for frame-scoped runs whose only sample may be
        # dataset frame 7 (or any other non-zero frame), and for a selection
        # taken under a dataset view, which renumbers every sample.
        samples = self._load_samples(run, {"mode": mode}, "export", view_id=view_id)
        chosen = (
            sorted({int(i) for i in selected if 0 <= int(i) < samples.n_samples})
            if selected
            else list(range(samples.n_samples))
        )
        frames = sorted({int(samples.frame[i]) for i in chosen})
        frames = [frame for frame in frames if 0 <= frame < count]
        if not frames:
            raise AppError(EXPORT_FAILED, "export selection is empty")
        try:
            target = validate_local_path(str(target), field="export output path")
        except UnsafePathError as exc:
            raise AppError(EXPORT_FAILED, "export output must be an absolute local path") from exc
        if export_format == "json":
            target.parent.mkdir(parents=True, exist_ok=True)
            records = _identity_records(samples, chosen)
            ensure_no_reparse_points(target.parent)
            with open_text_for_write(target) as fh:
                json.dump({"dataset_id": dataset["id"], "run_id": run["id"], "format": dataset["format"], "records": records}, fh, ensure_ascii=False, indent=2)
            return target, len(records)
        if export_format == "csv":
            target.parent.mkdir(parents=True, exist_ok=True)
            ensure_no_reparse_points(target.parent)
            with open_text_for_write(target, newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["sample_index", "frame", "sample_id"])
                writer.writeheader()
                for record in _identity_records(samples, chosen):
                    writer.writerow(record)
            return target, len(chosen)
        if export_format == "extxyz":
            return target, write_extxyz(target, _cancellable_frames(adapter, frames, ctx))
        if dataset["format"] != "deepmd":
            raise AppError(EXPORT_FAILED, "DeepMD export requires a DeepMD source dataset")
        if target.exists() and not target.is_dir():
            raise AppError(EXPORT_FAILED, "DeepMD export requires a directory destination")
        return target, write_deepmd(target, _cancellable_frames(adapter, frames, ctx))

    def _write_sampling_report(self, run: dict, analysis_row: dict, selected: list[int], target: Path) -> None:
        """Write the reprovenance JSON for a sampling selection.

        Everything needed to reproduce — and to plot — the selection lives in
        one file: parameters, coverage statistics, the coverage curve, and the
        selected sample indices.
        """
        params = self._json_load(analysis_row.get("params_json"), {})
        preview = self._json_load(analysis_row.get("preview_json"), {})
        input_ids = self._json_load(analysis_row.get("input_run_ids_json"), [run["id"]])
        existing_run_id = params.get("existing_run_id")
        curve: dict[str, list] = {"samples": [], "coverage_radius": [], "mean_residual": []}
        try:
            if analysis_row.get("result_path"):
                root = self._artifacts.managed_path(str(analysis_row["id"]), analysis_row.get("result_path"))
                manifest = self._json_load(analysis_row.get("artifact_manifest_json"), {})
                files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
                for name, key in (("coverage_radius_curve", "coverage_radius"), ("coverage_mean_curve", "mean_residual"), ("coverage_r2_curve", "coverage_r2")):
                    meta = files.get(name)
                    if not isinstance(meta, dict):
                        continue
                    path = self._artifacts.artifact_file(root, meta.get("path"))
                    if path is not None and path.is_file():
                        curve[key] = np.load(path, mmap_mode="r", allow_pickle=False).tolist()
                curve["samples"] = list(range(1, len(curve["coverage_radius"]) + 1))
        except (OSError, ValueError, TypeError, UnsafePathError):
            curve = {"samples": [], "coverage_radius": [], "mean_residual": []}
        report = {
            "algorithm": "farthest_point_sampling",
            "strategy": params.get("strategy") or "global",
            "descriptor": run.get("descriptor_name"),
            "descriptor_run_id": run["id"],
            "dataset_id": run["dataset_id"],
            "descriptor_dimension": preview.get("sampling_dimension"),
            "granularity": params.get("mode") or "structure",
            "scaling": preview.get("scaling"),
            "initialization": preview.get("initialization"),
            "min_distance": preview.get("min_distance"),
            "target_coverage": preview.get("target_coverage"),
            "requested_samples": preview.get("requested_count"),
            "selected_samples": len(selected),
            "n_candidates": preview.get("n_candidates"),
            "stop_reason": preview.get("stop_reason"),
            "warm_start": bool(existing_run_id),
            "existing_descriptor_run_id": existing_run_id,
            "input_run_ids": input_ids,
            "sampling_space": preview.get("sampling_space"),
            "feature_blocks": preview.get("blocks"),
            "group_allocation": preview.get("allocation"),
            "coverage": {
                "radius": preview.get("coverage_radius"),
                "r2": preview.get("coverage_r2"),
                "mean_residual": preview.get("mean_residual"),
                "p50_residual": preview.get("p50_residual"),
                "p90_residual": preview.get("p90_residual"),
                "p95_residual": preview.get("p95_residual"),
                "p99_residual": preview.get("p99_residual"),
                "max_residual": preview.get("max_residual"),
            },
            "coverage_curve": curve,
            "selected_sample_indices": selected,
            "sampling_analysis_id": analysis_row.get("id"),
            "algorithm_version": ANALYSIS_ALGORITHM_VERSION,
            "generated_at": _NOW(),
        }
        with open_text_for_write(target) as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

__all__ = ["AnalysisExportMixin"]
