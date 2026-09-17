"""Analysis service responsibilities split by concern.

The :class:`AnalysisService` facade inherits these mixins; each file owns one
responsibility (data loading, preview shaping, artifacts/export or job
execution) so the request surface stays free of numerical and filesystem
details.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ..analysis import (
    ANALYSIS_REGISTRY,
    AnalysisEngine,
    AtomDescriptorMatrix,
    DescriptorMatrix,
    StructureDescriptorMatrix,
)
from ..analysis.sampling import FeatureBlock, group_sizes, sqrt_quota
from ..errors import (
    ANALYSIS_INPUT_INVALID,
    ANALYSIS_INSUFFICIENT_SAMPLES,
    ANALYSIS_NOT_FOUND,
    ANALYSIS_STALE,
    ARTIFACT_INVALID,
    EXPORT_FAILED,
    AppError,
    INVALID_PARAMS,
    JOB_CANCELLED,
    RESULT_INCOMPATIBLE,
)
from ..security import (
    UnsafePathError,
    ensure_no_reparse_points,
    escape_like,
    open_text_for_write,
    validate_local_path,
)
from .analysis_artifact_store import AnalysisArtifactStore
from .analysis_helpers import (
    ANALYSIS_ALGORITHM_VERSION,
    COMPOSITE_BLOCKS,
    PHYSICAL_BLOCKS,
    _ANALYSIS_SCHEMA_VERSION,
    _MAX_PREVIEW_POINTS,
    _NOW,
    _PREVIEW_ARRAY_KEYS,
    _block_names,
    _cell_parameters,
    _composition_matrix,
    _descriptor_summary,
    _pool_rows,
    _require_finite,
)


class AnalysisExportMixin:
    """Dataset/sampling export writers and export job submission."""

    def submit_export(self, params: dict) -> dict:
        params = dict(params or {})
        input_ids = self._input_ids("export", params)
        if len(input_ids) != 1:
            raise AppError(ANALYSIS_INPUT_INVALID, "export requires exactly one run_id")
        run = self._usable_run(input_ids[0])
        export_format = str(params.get("format") or params.get("output_format") or "json").lower()
        if export_format not in ("json", "csv", "extxyz", "deepmd", "indices", "report"):
            raise AppError(ANALYSIS_INPUT_INVALID, "format must be json, csv, extxyz, deepmd, indices, or report")
        mode = str(params.get("mode") or "structure")
        if mode not in ("structure", "atom"):
            raise AppError(ANALYSIS_INPUT_INVALID, "mode must be structure or atom")
        # The sampling report carries the provenance of the analysis it came
        # from; every other format only needs the run plus the selection.
        report_analysis = self._analysis_row(str(params["analysis_id"])) if export_format == "report" and params.get("analysis_id") else None
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
            target = str(validate_local_path(target, field="export output path"))
        except UnsafePathError as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "export output must be an absolute local path") from exc
        # The analysis id is part of a report's identity: two different
        # sampling analyses must never share one report cache entry.
        canonical = self._canonical_params({
            "format": export_format,
            "indices": sorted(set(selected)),
            "output_path": target,
            "mode": mode,
            **({"analysis_id": str(params["analysis_id"])} if export_format == "report" and params.get("analysis_id") else {}),
        })
        cache_key = self._analysis_cache_key("export", input_ids, canonical)

        with self._submit_lock:
            cached = self.db.query_one("SELECT * FROM analysis_runs WHERE cache_key = ? AND status = 'COMPLETED'", (cache_key,))
            if cached and self._artifact_is_complete(cached):
                return {"job_id": None, "analysis_id": cached["id"], "cache": {"existing_analysis_id": cached["id"], "cache_key": cache_key}}
            active = self.db.query_one(
                "SELECT * FROM analysis_runs WHERE cache_key = ? AND status IN ('QUEUED', 'RUNNING') ORDER BY created_at DESC LIMIT 1",
                (cache_key,),
            )
            if active:
                job = self.db.query_one(
                    "SELECT id FROM jobs WHERE analysis_run_id = ? AND status IN ('QUEUED', 'RUNNING') ORDER BY created_at DESC LIMIT 1",
                    (active["id"],),
                )
                if job:
                    return {"job_id": job["id"], "analysis_id": active["id"], "cache": {"existing_analysis_id": active["id"], "status": active["status"], "cache_key": cache_key}}
            analysis_id = active["id"] if active else f"ana_{uuid.uuid4().hex[:12]}"
            if not active:
                self.db.execute(
                    "INSERT INTO analysis_runs (id, descriptor_run_id, analysis_type, params_json, status, created_at, input_run_ids_json, dataset_ids_json, cache_key, schema_version, algorithm_version, updated_at) VALUES (?, ?, 'export', ?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?)",
                    (analysis_id, run["id"], json.dumps(canonical, ensure_ascii=False, sort_keys=True), _NOW(), json.dumps(input_ids), json.dumps([run["dataset_id"]]), cache_key, _ANALYSIS_SCHEMA_VERSION, ANALYSIS_ALGORITHM_VERSION, _NOW()),
                )

            def runner(ctx):
                artifact_path: Path | None = None
                try:
                    self._mark_run_running(analysis_id)
                    ctx.progress(0, 1, "writing export")
                    path = self._write_export(run, selected, export_format, mode, Path(target), ctx, report_analysis)
                    artifact_path, manifest = self._commit_artifact(
                        analysis_id,
                        "export",
                        input_ids,
                        canonical,
                        {"arrays": {}, "warnings": [], "export_path": str(path)},
                        {"kind": "export", "format": export_format, "output_path": str(path), "selected_count": len(selected)},
                        ctx,
                    )
                    self._complete_analysis_run(
                        analysis_id,
                        {
                            "result_path": str(artifact_path),
                            "finished_at": _NOW(),
                            "artifact_manifest_json": json.dumps(manifest),
                            "preview_json": json.dumps({"kind": "export", "format": export_format, "output_path": str(path), "selected_count": len(selected)}),
                            "updated_at": _NOW(),
                        },
                        artifact_path,
                    )
                    ctx.progress(1, 1, "export complete")
                    return {"analysis_id": analysis_id, "output_path": str(path), "selected_count": len(selected)}

                except BaseException:
                    # Keep failed runs atomic: a committed artifact must
                    # never outlive the run row that failed to settle.
                    if artifact_path is not None:
                        self._rmtree_quiet(artifact_path)
                    raise
            try:
                job_id = self.jobs.submit("analysis.export", runner, dataset_id=run["dataset_id"], analysis_run_id=analysis_id)
            except Exception:
                if not active:
                    self.db.execute("DELETE FROM analysis_runs WHERE id = ?", (analysis_id,))
                raise
            return {"job_id": job_id, "analysis_id": analysis_id, "cache": None}

    def _write_export(self, run: dict, selected: list[int], export_format: str, mode: str, target: Path, ctx, report_analysis: dict | None = None) -> Path:
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
            return target
        if export_format == "report":
            if not selected:
                raise AppError(EXPORT_FAILED, "export selection is empty")
            target.parent.mkdir(parents=True, exist_ok=True)
            ensure_no_reparse_points(target.parent)
            self._write_sampling_report(run, report_analysis or {}, sorted(set(int(i) for i in selected)), target)
            return target
        if self.datasets is None:
            raise AppError(EXPORT_FAILED, "dataset service is unavailable")
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run["dataset_id"],))
        if dataset is None:
            raise AppError(EXPORT_FAILED, f"dataset {run['dataset_id']} does not exist")
        adapter = self.datasets._adapter_for(dataset)
        count = len(adapter)
        # Analysis selections are sample indices, not dataset frame indices.
        # Resolve them through the same identity table used to build previews;
        # this is essential for frame-scoped runs whose only sample may be
        # dataset frame 7 (or any other non-zero frame).
        samples = self._load_samples(run, {"mode": mode}, "export")
        frames = sorted({int(samples.frame[i]) for i in selected if 0 <= i < samples.n_samples}) if selected else sorted({int(frame) for frame in samples.frame})
        frames = [frame for frame in frames if 0 <= frame < count]
        if not frames:
            raise AppError(EXPORT_FAILED, "export selection is empty")
        try:
            target = validate_local_path(str(target), field="export output path")
        except UnsafePathError as exc:
            raise AppError(EXPORT_FAILED, "export output must be an absolute local path") from exc
        if export_format == "json":
            target.parent.mkdir(parents=True, exist_ok=True)
            records = [{"sample_index": i, "frame": frame, "sample_id": f"frame:{frame}"} for i, frame in enumerate(frames)]
            ensure_no_reparse_points(target.parent)
            with open_text_for_write(target) as fh:
                json.dump({"dataset_id": dataset["id"], "run_id": run["id"], "format": dataset["format"], "records": records}, fh, ensure_ascii=False, indent=2)
            return target
        if export_format == "csv":
            target.parent.mkdir(parents=True, exist_ok=True)
            ensure_no_reparse_points(target.parent)
            with open_text_for_write(target, newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["sample_index", "frame", "sample_id"])
                writer.writeheader()
                for i, frame in enumerate(frames):
                    writer.writerow({"sample_index": i, "frame": frame, "sample_id": f"frame:{frame}"})
            return target
        if export_format == "extxyz":
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_extxyz(adapter, frames, target, ctx)
            return target
        if dataset["format"] != "deepmd":
            raise AppError(EXPORT_FAILED, "DeepMD export requires a DeepMD source dataset")
        if target.exists() and not target.is_dir():
            raise AppError(EXPORT_FAILED, "DeepMD export requires a directory destination")
        target.mkdir(parents=True, exist_ok=True)
        ensure_no_reparse_points(target)
        self._write_deepmd(adapter, frames, target, ctx)
        return target

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
                root = self._managed_artifact_path(str(analysis_row["id"]), analysis_row.get("result_path"))
                manifest = self._json_load(analysis_row.get("artifact_manifest_json"), {})
                files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
                for name, key in (("coverage_radius_curve", "coverage_radius"), ("coverage_mean_curve", "mean_residual"), ("coverage_r2_curve", "coverage_r2")):
                    meta = files.get(name)
                    if not isinstance(meta, dict):
                        continue
                    path = self._artifact_file(root, meta.get("path"))
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

    @staticmethod
    def _write_extxyz(adapter, frames: list[int], target: Path, ctx) -> None:
        from ..datasets.deepmd_symbols import _Z_TO_SYMBOL

        with open_text_for_write(target) as fh:
            for pos, frame_index in enumerate(frames):
                ctx.check_cancelled()
                frame = adapter.get_frame(frame_index)
                symbols = [_Z_TO_SYMBOL.get(int(z), f"Z{int(z)}") for z in frame.numbers]
                fields = ["species:S:1", "pos:R:3"]
                if frame.forces is not None:
                    fields.append("forces:R:3")
                lattice = " ".join(f"{float(v):.12g}" for v in np.asarray(frame.cell).reshape(-1))
                comment = f'Properties={":".join(fields)}'
                if np.abs(frame.cell).sum() > 1e-12:
                    comment += f' Lattice="{lattice}" pbc="T T T"'
                if frame.energy is not None:
                    comment += f" energy={float(frame.energy):.12g}"
                fh.write(f"{len(symbols)}\n{comment}\n")
                for i, (symbol, xyz) in enumerate(zip(symbols, frame.positions)):
                    line = f"{symbol} {' '.join(f'{float(v):.12g}' for v in xyz)}"
                    if frame.forces is not None:
                        line += " " + " ".join(f"{float(v):.12g}" for v in frame.forces[i])
                    fh.write(line + "\n")

    @staticmethod
    def _write_deepmd(adapter, frames: list[int], target: Path, ctx) -> None:
        from ..datasets.deepmd_symbols import _Z_TO_SYMBOL

        first = adapter.get_frame(frames[0])
        numbers = np.asarray(first.numbers, dtype=np.int64)
        unique = sorted({int(z) for z in numbers.tolist()})
        type_map = {z: i for i, z in enumerate(unique)}
        with open_text_for_write(target / "type.raw") as fh:
            fh.write(" ".join(str(type_map[int(z)]) for z in numbers) + "\n")
        with open_text_for_write(target / "type_map.raw") as fh:
            fh.write(" ".join(_Z_TO_SYMBOL.get(z, f"Z{z}") for z in unique) + "\n")
        set_dir = target / "set.000"
        set_dir.mkdir(parents=True, exist_ok=True)
        ensure_no_reparse_points(set_dir)
        coords, cells, energies, forces, virials = [], [], [], [], []
        has_energy = has_forces = has_virial = True
        for frame_index in frames:
            ctx.check_cancelled()
            frame = adapter.get_frame(frame_index)
            coords.append(np.asarray(frame.positions, dtype=np.float64))
            cells.append(np.asarray(frame.cell, dtype=np.float64))
            if frame.energy is None:
                has_energy = False
            else:
                energies.append(float(frame.energy))
            if frame.forces is None:
                has_forces = False
            else:
                forces.append(np.asarray(frame.forces, dtype=np.float64))
            if frame.virial is None:
                has_virial = False
            else:
                virials.append(np.asarray(frame.virial, dtype=np.float64))
        np.save(set_dir / "coord.npy", np.stack(coords), allow_pickle=False)
        if any(np.abs(cell).sum() > 1e-12 for cell in cells):
            np.save(set_dir / "box.npy", np.stack(cells), allow_pickle=False)
        else:
            with open_text_for_write(set_dir / "nopbc") as fh:
                fh.write("")
        if has_energy:
            np.save(set_dir / "energy.npy", np.asarray(energies, dtype=np.float64), allow_pickle=False)
        if has_forces:
            np.save(set_dir / "force.npy", np.stack(forces), allow_pickle=False)
        if has_virial:
            np.save(set_dir / "virial.npy", np.stack(virials), allow_pickle=False)


__all__ = ["AnalysisExportMixin"]
