"""AnalysisService: PCA on stored descriptor results (design doc §31).

PCA via numpy SVD (no sklearn dependency); atom/pair-level results are mean
pooled per structure so every point maps to exactly one frame for the
PCA -> Explore reverse jump.
"""

from __future__ import annotations

import json
import hashlib
import os
import csv
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..analysis import AnalysisEngine, AnalysisResult, ArtifactManifest, SampleMatrix
from ..errors import (
    ANALYSIS_INPUT_INVALID,
    ANALYSIS_NOT_FOUND,
    ANALYSIS_STALE,
    ARTIFACT_INVALID,
    EXPORT_FAILED,
    AppError,
    INVALID_PARAMS,
    RESULT_INCOMPATIBLE,
)
from .result_service import ResultService
from ..storage.database import Database
from .job_service import JobService

_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731
_ANALYSIS_SCHEMA_VERSION = 1
_ALGORITHM_VERSION = "studio-analysis-1"
_MAX_PREVIEW_POINTS = 20_000


class AnalysisService:
    def __init__(self, db: Database, jobs: JobService, results: ResultService, datasets, data_dir: Path):
        self.db = db
        self.jobs = jobs
        self.results = results
        self.datasets = datasets
        self.data_dir = data_dir
        # RPC requests are handled concurrently. Serialize only the
        # cache-lookup/create section so two identical requests cannot enqueue
        # duplicate PCA jobs; the actual calculation still runs in JobService.
        self._pca_submit_lock = threading.Lock()

    def pca(self, params: dict) -> dict:
        params = dict(params or {})
        run_id = params.get("run_id")
        if not run_id:
            raise AppError(INVALID_PARAMS, "'run_id' is required")
        mode = params.get("mode") or "structure"
        if mode not in ("structure", "atom"):
            raise AppError(INVALID_PARAMS, f"mode must be 'structure' or 'atom', got {mode!r}")
        row = self._usable_run(run_id)
        preprocess = str(params.get("preprocess") or "center")
        if preprocess not in ("raw", "center", "standardized"):
            raise AppError(ANALYSIS_INPUT_INVALID, "preprocess must be raw, center, or standardized")
        # Keep the compatibility PCA entry point deterministic with the same
        # explicit defaults as the generic Analysis API. Older rows with only
        # ``mode`` (or an empty object) remain reusable through the matcher.
        analysis_params = {"mode": mode, "seed": 42, "preprocess": preprocess}
        params_json = json.dumps(
            analysis_params, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

        with self._pca_submit_lock:
            cached = self._find_cached_pca(run_id, mode, preprocess)
            if cached is not None:
                return {
                    "job_id": None,
                    "analysis_id": cached["id"],
                    "cache": {"existing_analysis_id": cached["id"]},
                }

            active = self._find_active_pca(run_id, mode, preprocess)
            if active is not None and active["job_id"]:
                return {
                    "job_id": active["job_id"],
                    "analysis_id": active["id"],
                    "cache": {
                        "existing_analysis_id": active["id"],
                        "status": active["status"],
                    },
                }

            # Reuse an abandoned queued/running analysis row if it has no
            # linked job (legacy databases may contain such rows); otherwise
            # create the persistent cache entry now.
            analysis_id = active["id"] if active is not None else f"ana_{uuid.uuid4().hex[:12]}"
            if active is None:
                self.db.execute(
                    "INSERT INTO analysis_runs (id, descriptor_run_id, analysis_type, params_json, status, created_at)"
                    " VALUES (?, ?, 'pca', ?, 'QUEUED', ?)",
                    (analysis_id, run_id, params_json, _NOW()),
                )

            def runner(ctx):
                ctx.progress(0, 1, "loading values")
                values, run_row = self.results.load_values(run_id)
                ctx.check_cancelled()
                coords, explained, frames, atoms = self._pca_points(values, run_row, mode, preprocess)
                n_points = coords.shape[0]
                ctx.progress(0.7, 1, "assembling points")
                frame_props = self._frame_properties(
                    run_row, int(frames.max()) + 1 if frames.size else 1
                )
                out_dir = self.data_dir / "analysis" / analysis_id
                out_dir.mkdir(parents=True, exist_ok=True)
                np.save(out_dir / "coords.npy", coords)
                payload = {
                    "analysis_id": analysis_id,
                    "run_id": run_id,
                    "mode": mode,
                    "preprocess": preprocess,
                    "n_points": int(n_points),
                    "points": [
                        {
                            "i": i,
                            "frame": int(frames[i]),
                            **({"atom": int(atoms[i])} if atoms is not None else {}),
                            "pc1": round(float(coords[i, 0]), 4),
                            "pc2": round(float(coords[i, 1]), 4),
                            **frame_props[int(frames[i])],
                        }
                        for i in range(n_points)
                    ],
                    "explained_variance": [round(float(v), 6) for v in explained[:2]],
                    "x_label": f"PC1 ({explained[0] * 100:.1f}%)" if explained.size else "PC1",
                    "y_label": f"PC2 ({explained[1] * 100:.1f}%)" if explained.size > 1 else "PC2",
                }
                (out_dir / "pca.json").write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8"
                )
                self.db.execute(
                    "UPDATE analysis_runs SET status = 'COMPLETED', result_path = ?, finished_at = ? WHERE id = ?",
                    (str(out_dir), _NOW(), analysis_id),
                )
                ctx.progress(1, 1, "done")
                return {"analysis_id": analysis_id, "n_points": payload["n_points"]}

            job_id = self.jobs.submit(
                "analysis.pca", runner, dataset_id=row["dataset_id"], analysis_run_id=analysis_id
            )
            return {"job_id": job_id, "analysis_id": analysis_id, "cache": None}

    def _find_cached_pca(self, run_id: str, mode: str, preprocess: str = "center") -> dict | None:
        """Find the newest usable completed PCA for this run and mode.

        Parameters are normalized in Python so cache entries written by older
        builds (with JSON whitespace, or with ``{}`` for the old default
        structure mode) remain reusable.
        """
        rows = self.db.query(
            "SELECT id, params_json, result_path FROM analysis_runs"
            " WHERE descriptor_run_id = ? AND analysis_type = 'pca' AND status = 'COMPLETED'"
            " ORDER BY created_at DESC, id DESC",
            (run_id,),
        )
        for row in rows:
            if self._pca_params_match(row["params_json"], mode, preprocess) and self._pca_artifact_exists(row):
                return row
        return None

    def _find_active_pca(self, run_id: str, mode: str, preprocess: str = "center") -> dict | None:
        rows = self.db.query(
            "SELECT id, params_json, status FROM analysis_runs"
            " WHERE descriptor_run_id = ? AND analysis_type = 'pca'"
            " AND status IN ('QUEUED', 'RUNNING')"
            " ORDER BY created_at DESC, id DESC",
            (run_id,),
        )
        for row in rows:
            if not self._pca_params_match(row["params_json"], mode, preprocess):
                continue
            job = self.db.query_one(
                "SELECT id FROM jobs WHERE analysis_run_id = ?"
                " AND status IN ('QUEUED', 'RUNNING') ORDER BY created_at DESC, id DESC LIMIT 1",
                (row["id"],),
            )
            return {**row, "job_id": job["id"] if job else None}
        return None

    @staticmethod
    def _pca_params_match(raw: str | None, mode: str, preprocess: str = "center") -> bool:
        try:
            saved = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            return False
        try:
            saved_mode = str(saved.get("mode") or "structure")
            saved_seed = int(saved.get("seed", 42))
        except (AttributeError, TypeError, ValueError):
            return False
        return saved_mode == mode and saved_seed == 42 and str(saved.get("preprocess") or "center") == preprocess

    @staticmethod
    def _pca_artifact_exists(row: dict) -> bool:
        result_path = row.get("result_path")
        return bool(result_path and (Path(result_path) / "pca.json").is_file())

    @classmethod
    def _pca_points(cls, values: np.ndarray, run_row: dict, mode: str, preprocess: str = "center"):
        """Return (coords, explained, frames, atoms|None).

        Structure mode: one point per frame (atom/pair rows mean-pooled).
        Atom mode: one point per atom/pair row, keeping the owning frame and the
        in-frame row index for tooltips/reverse-jump. Very large runs are evenly
        subsampled so the IPC payload and chart stay responsive (design doc §25).
        """
        path = Path(run_row["result_path"])
        offsets_file = path / "row_offsets.npy"
        offsets = np.asarray(np.load(offsets_file, allow_pickle=False), dtype=np.int64) if offsets_file.exists() else None
        atom_level = values.ndim == 2 and cls._valid_offsets(offsets, values.shape[0])
        if mode == "atom" and not atom_level:
            raise AppError(ANALYSIS_INPUT_INVALID, "atom/local-environment PCA requires verified row_offsets")
        if mode == "structure" or not atom_level:
            pooled = cls._pool_per_structure(values, run_row)
            frames = cls._run_frame_values(run_row, pooled.shape[0])
            atoms = None
        else:
            counts = np.diff(offsets).astype(int)
            local_frames = np.repeat(np.arange(counts.size, dtype=np.int64), counts)
            frame_values = cls._run_frame_values(run_row, counts.size)
            frames = frame_values[local_frames]
            atoms = np.arange(values.shape[0], dtype=np.int64) - offsets[local_frames]
            pooled = values
            max_points = 20000
            if pooled.shape[0] > max_points:
                keep = np.unique(np.linspace(0, pooled.shape[0] - 1, max_points).astype(int))
                pooled, frames, atoms = pooled[keep], frames[keep], atoms[keep]
        coords, explained = cls._pca(pooled, preprocess)
        return coords, explained, frames, atoms

    @staticmethod
    def _run_frame_values(run_row: dict, count: int) -> np.ndarray:
        if run_row.get("scope") == "frame":
            return np.full(count, int(run_row.get("frame_index") or 0), dtype=np.int64)
        return np.arange(count, dtype=np.int64)

    @staticmethod
    def _pool_per_structure(values: np.ndarray, run_row: dict) -> np.ndarray:
        path = Path(run_row["result_path"])
        offsets_file = path / "row_offsets.npy"
        if values.ndim == 2 and offsets_file.exists():
            offsets = np.asarray(np.load(offsets_file, allow_pickle=False), dtype=np.int64)
            if AnalysisService._valid_offsets(offsets, values.shape[0]):
                n_struct = offsets.size - 1
                dim = values.shape[1]
                pooled = np.empty((n_struct, dim), dtype=np.float64)
                for i in range(n_struct):
                    lo, hi = int(offsets[i]), int(offsets[i + 1])
                    if hi > lo:
                        pooled[i] = values[lo:hi].mean(axis=0)
                    else:
                        pooled[i] = 0.0
                return pooled
        return values.reshape(values.shape[0], -1)

    @staticmethod
    def _pca(x: np.ndarray, preprocess: str = "center"):
        x = np.asarray(x, dtype=np.float64)
        if x.ndim != 2 or x.shape[0] == 0 or x.shape[1] == 0 or not np.isfinite(x).all():
            raise AppError(ANALYSIS_INPUT_INVALID, "analysis input must be a finite, non-empty 2D matrix")
        if preprocess not in ("raw", "center", "standardized"):
            raise AppError(ANALYSIS_INPUT_INVALID, "preprocess must be raw, center, or standardized")
        if preprocess == "raw":
            prepared = x
        else:
            centered = x - x.mean(axis=0)
            if preprocess == "center":
                prepared = centered
            else:
                scale = centered.std(axis=0)
                keep = scale > np.finfo(np.float64).eps
                if not bool(keep.any()):
                    keep = np.ones(centered.shape[1], dtype=bool)
                    scale = np.ones(centered.shape[1], dtype=np.float64)
                prepared = centered[:, keep] / np.where(scale[keep] > 0, scale[keep], 1.0)
        xc = prepared
        # SVD on up to ~12k x few-hundred matrix is fast and stable
        _u, s, vt = np.linalg.svd(xc, full_matrices=False)
        var = (s**2) / max(x.shape[0] - 1, 1)
        total = float(var.sum()) or 1.0
        explained = var / total
        components = min(2, vt.shape[0])
        coords = xc @ vt[:components].T
        if components < 2:
            coords = np.pad(coords, ((0, 0), (0, 2 - components)))
        return coords, explained

    def _frame_properties(self, run_row: dict, n_points: int) -> list[dict]:
        """Energy/force/volume per frame for color-by (aligned to frame index)."""
        frame_scope = run_row["scope"] == "frame"
        need = max(n_points, (run_row["frame_index"] + 1) if frame_scope else n_points)
        props: list[dict] = [{"energy": None, "force_max": None, "volume": None} for _ in range(need)]
        dataset_row = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset_row is None:
            return props
        adapter = self.datasets._adapter_for(dataset_row)
        indices = [run_row["frame_index"]] if frame_scope else list(range(min(n_points, len(adapter))))
        for i in indices:
            try:
                f = adapter.get_frame(i)
            except AppError:
                continue
            entry = {"energy": f.energy, "force_max": None, "volume": None}
            if f.forces is not None and len(f.forces):
                mags = np.linalg.norm(np.asarray(f.forces), axis=1)
                entry["force_max"] = round(float(mags.max()), 5)
            det = abs(float(np.linalg.det(np.asarray(f.cell))))
            if det > 1e-8:
                entry["volume"] = round(det, 4)
            props[i] = entry
        return props

    # -- generic Analysis API -------------------------------------------------
    # The legacy PCA path above is intentionally kept intact: old databases
    # contain pca.json artifacts and old clients still call result.get_pca.
    # New modules use the common artifact/cache contract below.

    def list(self, params: dict) -> list[dict]:
        """List analysis metadata without loading any large array."""
        params = params or {}
        sql = "SELECT * FROM analysis_runs"
        conditions: list[str] = []
        args: list[object] = []
        if params.get("run_id"):
            conditions.append("(descriptor_run_id = ? OR input_run_ids_json LIKE ?)")
            args.extend([params["run_id"], f'%"{params["run_id"]}"%'])
        if params.get("analysis_type"):
            conditions.append("analysis_type = ?")
            args.append(params["analysis_type"])
        if params.get("status"):
            conditions.append("status = ?")
            args.append(params["status"])
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at DESC, id DESC LIMIT 500"
        rows = self.db.query(sql, tuple(args))
        return [self._public_analysis_row(row) for row in rows]

    def get(self, params: dict) -> dict:
        row = self._analysis_row(params.get("analysis_id") or params.get("id"))
        return self._public_analysis_row(row, include_preview=True)

    def delete(self, params: dict) -> dict:
        analysis_id = params.get("analysis_id") or params.get("id")
        row = self._analysis_row(analysis_id)
        if row["status"] in ("QUEUED", "RUNNING"):
            raise AppError(RESULT_INCOMPATIBLE, f"analysis {analysis_id} is {row['status']}")
        self.db.execute("DELETE FROM jobs WHERE analysis_run_id = ?", (analysis_id,))
        self.db.execute("DELETE FROM analysis_runs WHERE id = ?", (analysis_id,))
        self._rmtree_quiet(row.get("result_path"))
        return {"ok": True, "analysis_id": analysis_id}

    def preview(self, params: dict) -> dict:
        """Return a bounded identity/plot preview from a completed artifact."""
        row = self._analysis_row(params.get("analysis_id") or params.get("id"))
        self._require_artifact(row)
        try:
            offset = max(0, int(params.get("offset", 0)))
            limit = min(_MAX_PREVIEW_POINTS, max(1, int(params.get("limit", 5000))))
        except (TypeError, ValueError) as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "offset and limit must be integers") from exc
        raw = row.get("preview_json")
        preview = self._json_load(raw, {})
        if not isinstance(preview, dict):
            preview = {}
        if isinstance(preview.get("points"), list):
            preview = {**preview, "points": preview["points"][offset : offset + limit]}
        elif isinstance(preview.get("rows"), list):
            preview = {**preview, "rows": preview["rows"][offset : offset + limit]}
        else:
            # Rebuild a row-oriented preview when a result was written by an
            # earlier generic backend that only stored arrays.
            preview["rows"] = self._rows_from_artifact(row, offset, limit)
        preview.update({"analysis_id": row["id"], "offset": offset, "limit": limit})
        return preview

    def chunk(self, params: dict) -> dict:
        """Read a bounded slice of one named artifact array."""
        import numpy as np

        row = self._analysis_row(params.get("analysis_id") or params.get("id"))
        self._require_artifact(row)
        name = str(params.get("array") or params.get("array_name") or "")
        manifest = self._json_load(row.get("artifact_manifest_json"), {})
        files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
        file_meta = files.get(name)
        if not isinstance(file_meta, dict):
            raise AppError(ANALYSIS_INPUT_INVALID, f"array {name!r} is not present in analysis artifact")
        path = Path(row["result_path"]) / file_meta["path"]
        if not path.is_file():
            raise AppError(ARTIFACT_INVALID, f"missing analysis artifact array: {path}")
        try:
            array = np.load(path, mmap_mode="r", allow_pickle=False)
            offset = max(0, int(params.get("offset", 0)))
            limit = min(_MAX_PREVIEW_POINTS, max(1, int(params.get("limit", 2000))))
        except (OSError, TypeError, ValueError) as exc:
            raise AppError(ARTIFACT_INVALID, f"cannot read analysis array {name!r}: {exc}") from exc
        if array.ndim == 0:
            data = array.reshape(1)
            offset = min(offset, 1)
            stop = min(offset + limit, 1)
            chunk = data[offset:stop]
        else:
            offset = min(offset, array.shape[0])
            stop = min(offset + limit, array.shape[0])
            chunk = array[offset:stop]
        # A chunk is row-bounded and also value-bounded.  The latter prevents a
        # single very-wide descriptor row from turning IPC into a large JSON
        # transport; callers can page columns with column_start/column_end.
        if chunk.ndim == 2:
            col_start = max(0, int(params.get("column_start", 0)))
            col_end = min(chunk.shape[1], int(params.get("column_end", col_start + 256)))
            col_end = max(col_start, col_end)
            chunk = chunk[:, col_start:col_end]
        values = chunk.tolist()
        return {
            "analysis_id": row["id"],
            "array": name,
            "offset": offset,
            "next_offset": stop,
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "data": values,
        }

    # Small wrappers keep the IPC method table explicit and make the supported
    # analysis vocabulary discoverable to frontend code and integration tests.
    def umap(self, params: dict) -> dict:
        return self.submit_generic("umap", params)

    def tsne(self, params: dict) -> dict:
        return self.submit_generic("tsne", params)

    def neighbors(self, params: dict) -> dict:
        return self.submit_generic("neighbors", params)

    def similarity(self, params: dict) -> dict:
        return self.submit_generic("similarity", params)

    def cluster(self, params: dict) -> dict:
        algorithm = str(params.get("algorithm") or params.get("method") or "kmeans").lower()
        return self.submit_generic(algorithm, params)

    def outlier(self, params: dict) -> dict:
        algorithm = str(params.get("algorithm") or params.get("method") or "lof").lower()
        return self.submit_generic(algorithm, params)

    def fps(self, params: dict) -> dict:
        return self.submit_generic("fps", params)

    def coverage(self, params: dict) -> dict:
        return self.submit_generic("coverage", params)

    def compare(self, params: dict) -> dict:
        return self.submit_generic("compare", params)

    def feature_variance(self, params: dict) -> dict:
        return self.submit_generic("feature_variance", params)

    def feature_correlation(self, params: dict) -> dict:
        return self.submit_generic("feature_correlation", params)

    def effective_dimension(self, params: dict) -> dict:
        return self.submit_generic("effective_dimension", params)

    def trajectory(self, params: dict) -> dict:
        return self.submit_generic("trajectory", params)

    def drift(self, params: dict) -> dict:
        return self.submit_generic("drift", params)

    def sensitivity(self, params: dict) -> dict:
        return self.submit_generic("sensitivity", params)

    def sampling(self, params: dict) -> dict:
        algorithm = str(params.get("algorithm") or params.get("method") or "random").lower()
        return self.submit_generic(algorithm, params)

    def export(self, params: dict) -> dict:
        return self.submit_export(params)

    def submit_generic(self, analysis_type: str, params: dict) -> dict:
        """Create or reuse a generic analysis job.

        The returned shape is intentionally identical for every long-running
        module, including sampling and export.  The cache key contains all
        input run IDs and normalized parameters, plus this backend algorithm
        version, so changing implementation cannot reuse an old artifact.
        """
        params = dict(params or {})
        input_ids = self._input_ids(analysis_type, params)
        run_rows = [self._usable_run(run_id) for run_id in input_ids]
        canonical_params = self._canonical_params(params)
        cache_key = hashlib.sha256(
            json.dumps(
                {"analysis_type": analysis_type, "inputs": input_ids, "params": canonical_params, "algorithm_version": _ALGORITHM_VERSION},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        with self._pca_submit_lock:
            cached = self.db.query_one(
                "SELECT * FROM analysis_runs WHERE cache_key = ? AND status = 'COMPLETED' ORDER BY created_at DESC LIMIT 1",
                (cache_key,),
            )
            if cached and self._artifact_is_complete(cached):
                return {"job_id": None, "analysis_id": cached["id"], "cache": {"existing_analysis_id": cached["id"], "cache_key": cache_key}}
            active = self.db.query_one(
                "SELECT * FROM analysis_runs WHERE cache_key = ? AND status IN ('QUEUED', 'RUNNING') ORDER BY created_at DESC LIMIT 1",
                (cache_key,),
            )
            analysis_id = active["id"] if active else f"ana_{uuid.uuid4().hex[:12]}"
            if active:
                job = self.db.query_one(
                    "SELECT id FROM jobs WHERE analysis_run_id = ? AND status IN ('QUEUED', 'RUNNING') ORDER BY created_at DESC LIMIT 1",
                    (analysis_id,),
                )
                if job:
                    return {"job_id": job["id"], "analysis_id": analysis_id, "cache": {"existing_analysis_id": analysis_id, "status": active["status"], "cache_key": cache_key}}
            primary = run_rows[0]
            now = _NOW()
            if not active:
                self.db.execute(
                    "INSERT INTO analysis_runs (id, descriptor_run_id, analysis_type, params_json, status, created_at, input_run_ids_json, dataset_ids_json, cache_key, schema_version, algorithm_version, preprocessing_json, warnings_json, preview_json, updated_at)"
                    " VALUES (?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        analysis_id,
                        primary["id"],
                        analysis_type,
                        json.dumps(canonical_params, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                        now,
                        json.dumps(input_ids, ensure_ascii=False),
                        json.dumps(sorted({r["dataset_id"] for r in run_rows}), ensure_ascii=False),
                        cache_key,
                        _ANALYSIS_SCHEMA_VERSION,
                        _ALGORITHM_VERSION,
                        json.dumps({"preprocess": params.get("preprocess")}, ensure_ascii=False),
                        json.dumps([], ensure_ascii=False),
                        json.dumps({}, ensure_ascii=False),
                        now,
                    ),
                )

            def runner(ctx):
                ctx.progress(0, 1, "loading descriptor results")
                samples = [self._load_samples(row, params, analysis_type) for row in run_rows]
                ctx.check_cancelled()
                result = self._run_engine(analysis_type, params, run_rows, samples, ctx)
                ctx.check_cancelled()
                preview_samples = samples[1] if analysis_type in ("coverage", "drift") else samples[0]
                preview = self._build_preview(result, preview_samples, analysis_type)
                out_dir, manifest = self._commit_artifact(
                    analysis_id,
                    analysis_type,
                    input_ids,
                    canonical_params,
                    result,
                    preview,
                    ctx,
                )
                self.db.execute(
                    "UPDATE analysis_runs SET status = 'COMPLETED', result_path = ?, finished_at = ?, warnings_json = ?, artifact_manifest_json = ?, preview_json = ?, preprocessing_json = ?, updated_at = ? WHERE id = ?",
                    (
                        str(out_dir),
                        _NOW(),
                        json.dumps(result.get("warnings", []), ensure_ascii=False),
                        json.dumps(manifest, ensure_ascii=False),
                        json.dumps(preview, ensure_ascii=False),
                        json.dumps({"preprocess": params.get("preprocess")}, ensure_ascii=False),
                        _NOW(),
                        analysis_id,
                    ),
                )
                ctx.progress(1, 1, "analysis complete")
                n_points = preview_samples.n_samples if analysis_type in ("coverage", "drift") else samples[0].n_samples
                return {"analysis_id": analysis_id, "n_points": n_points, "analysis_type": analysis_type, "warnings": result.get("warnings", [])}

            job_id = self.jobs.submit(
                f"analysis.{analysis_type}",
                runner,
                dataset_id=primary["dataset_id"],
                analysis_run_id=analysis_id,
            )
            return {"job_id": job_id, "analysis_id": analysis_id, "cache": None}

    def submit_export(self, params: dict) -> dict:
        params = dict(params or {})
        input_ids = self._input_ids("export", params)
        if len(input_ids) != 1:
            raise AppError(ANALYSIS_INPUT_INVALID, "export requires exactly one run_id")
        run = self._usable_run(input_ids[0])
        export_format = str(params.get("format") or params.get("output_format") or "json").lower()
        if export_format not in ("json", "csv", "extxyz", "deepmd"):
            raise AppError(ANALYSIS_INPUT_INVALID, "format must be json, csv, extxyz, or deepmd")
        mode = str(params.get("mode") or "structure")
        if mode not in ("structure", "atom"):
            raise AppError(ANALYSIS_INPUT_INVALID, "mode must be structure or atom")
        selected = params.get("indices")
        if selected is None:
            selected = []
        if not isinstance(selected, list) or not all(isinstance(i, int) and i >= 0 for i in selected):
            raise AppError(ANALYSIS_INPUT_INVALID, "indices must be a list of non-negative integers")
        target = str(params.get("output_path") or "")
        if not target:
            raise AppError(ANALYSIS_INPUT_INVALID, "output_path is required for export")
        target = str(Path(target).expanduser())
        canonical = self._canonical_params({"format": export_format, "indices": sorted(set(selected)), "output_path": target, "mode": mode})
        cache_key = hashlib.sha256(json.dumps({"analysis_type": "export", "inputs": input_ids, "params": canonical, "algorithm_version": _ALGORITHM_VERSION}, sort_keys=True).encode()).hexdigest()

        with self._pca_submit_lock:
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
                    (analysis_id, run["id"], json.dumps(canonical, ensure_ascii=False, sort_keys=True), _NOW(), json.dumps(input_ids), json.dumps([run["dataset_id"]]), cache_key, _ANALYSIS_SCHEMA_VERSION, _ALGORITHM_VERSION, _NOW()),
                )

            def runner(ctx):
                ctx.progress(0, 1, "writing export")
                path = self._write_export(run, selected, export_format, mode, Path(target), ctx)
                out_dir, manifest = self._commit_artifact(
                    analysis_id,
                    "export",
                    input_ids,
                    canonical,
                    {"arrays": {}, "warnings": [], "export_path": str(path)},
                    {"kind": "export", "format": export_format, "output_path": str(path), "selected_count": len(selected)},
                    ctx,
                )
                self.db.execute(
                    "UPDATE analysis_runs SET status = 'COMPLETED', result_path = ?, finished_at = ?, artifact_manifest_json = ?, preview_json = ?, updated_at = ? WHERE id = ?",
                    (str(out_dir), _NOW(), json.dumps(manifest), json.dumps({"kind": "export", "format": export_format, "output_path": str(path), "selected_count": len(selected)}), _NOW(), analysis_id),
                )
                ctx.progress(1, 1, "export complete")
                return {"analysis_id": analysis_id, "output_path": str(path), "selected_count": len(selected)}

            job_id = self.jobs.submit("analysis.export", runner, dataset_id=run["dataset_id"], analysis_run_id=analysis_id)
            return {"job_id": job_id, "analysis_id": analysis_id, "cache": None}

    def _run_engine(self, analysis_type: str, params: dict, rows: list[dict], samples: list[SampleMatrix], ctx) -> dict:
        progress = lambda fraction, message: (ctx.check_cancelled(), ctx.progress(None, None, message, fraction=0.1 + 0.85 * float(fraction)))
        if analysis_type == "pca":
            return AnalysisEngine.pca(samples[0], params, progress)
        if analysis_type == "umap":
            return AnalysisEngine.umap(samples[0], params, progress)
        if analysis_type == "tsne":
            return AnalysisEngine.tsne(samples[0], params, progress)
        if analysis_type == "neighbors":
            return AnalysisEngine.neighbors(samples[0], params, progress)
        if analysis_type == "similarity":
            return AnalysisEngine.similarity(samples[0], params, progress)
        if analysis_type in ("kmeans", "dbscan", "hdbscan", "agglomerative", "hierarchical"):
            return AnalysisEngine.cluster(samples[0], params, analysis_type, progress)
        if analysis_type in ("knn", "lof", "isolation_forest", "isolation-forest", "iforest", "mahalanobis", "mahalanobis_distance"):
            return AnalysisEngine.outlier(samples[0], params, analysis_type, progress)
        if analysis_type in ("fps", "random", "stratified", "cluster_representative", "cluster", "per_element", "element"):
            return AnalysisEngine.sampling(samples[0], params, analysis_type, progress)
        if analysis_type == "coverage":
            return AnalysisEngine.coverage(samples[0], samples[1], params, progress)
        if analysis_type == "compare":
            return AnalysisEngine.compare(samples[0], samples[1], params, progress)
        if analysis_type == "feature_variance":
            return AnalysisEngine.feature_variance(samples[0], params, progress)
        if analysis_type == "feature_correlation":
            return AnalysisEngine.feature_correlation(samples[0], params, progress)
        if analysis_type == "effective_dimension":
            return AnalysisEngine.effective_dimension(samples[0], params, progress)
        if analysis_type == "trajectory":
            return AnalysisEngine.trajectory(samples[0], params, progress)
        if analysis_type == "drift":
            return AnalysisEngine.drift(samples[0], samples[1], params, progress)
        if analysis_type == "sensitivity":
            return AnalysisEngine.sensitivity(list(zip(rows, samples)), params, progress)
        raise AppError(ANALYSIS_INPUT_INVALID, f"unsupported analysis type: {analysis_type}")

    def _input_ids(self, analysis_type: str, params: dict) -> list[str]:
        if analysis_type in ("coverage", "drift"):
            ids = [params.get("reference_run_id"), params.get("query_run_id")]
        elif analysis_type == "compare":
            ids = [params.get("left_run_id") or params.get("reference_run_id"), params.get("right_run_id") or params.get("query_run_id")]
        elif analysis_type == "sensitivity":
            ids = params.get("run_ids") or []
        else:
            ids = params.get("run_ids") or [params.get("run_id")]
        if isinstance(ids, (str, bytes)):
            ids = [ids]
        ids = [str(v) for v in ids if v]
        if not ids:
            raise AppError(INVALID_PARAMS, "at least one descriptor run id is required")
        if analysis_type in ("coverage", "drift", "compare") and len(ids) != 2:
            raise AppError(ANALYSIS_INPUT_INVALID, f"{analysis_type} requires reference and query run IDs")
        return ids

    def _usable_run(self, run_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (run_id,))
        if row is None:
            raise AppError(INVALID_PARAMS, f"run {run_id} does not exist")
        if row["status"] == "STALE":
            raise AppError(ANALYSIS_STALE, f"run {run_id} is STALE and cannot feed new analysis", {"run_id": run_id})
        if row["status"] != "COMPLETED" or not row.get("result_path"):
            raise AppError(RESULT_INCOMPATIBLE, f"run {run_id} is {row['status']}")
        self._assert_dataset_current(row)
        return row

    def _assert_dataset_current(self, row: dict) -> None:
        if self.datasets is None:
            return
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (row["dataset_id"],))
        if dataset is None:
            return
        from ..datasets import compute_fingerprint

        current = compute_fingerprint(Path(dataset["source_path"]), dataset["number_of_frames"])
        if current != dataset["fingerprint"]:
            self._mark_stale(row["dataset_id"], f"source fingerprint changed ({dataset['fingerprint']} -> {current})")
            raise AppError(ANALYSIS_STALE, f"run {row['id']} is stale because the source dataset changed", {"run_id": row["id"], "dataset_id": row["dataset_id"]})

    def _load_samples(self, run_row: dict, params: dict, analysis_type: str) -> SampleMatrix:
        import numpy as np

        values, row = self.results.load_values(run_row["id"])
        values = np.asarray(values, dtype=np.float64)
        if values.ndim > 2:
            values = values.reshape(values.shape[0], -1)
        if values.ndim == 1:
            values = values.reshape(-1, 1)
        if values.ndim != 2 or values.shape[0] == 0 or values.shape[1] == 0:
            raise AppError(ANALYSIS_INPUT_INVALID, "descriptor result is empty or not a 2D feature matrix")
        if not np.isfinite(values).all():
            raise AppError(ANALYSIS_INPUT_INVALID, "descriptor result contains NaN or Inf")
        path = Path(row["result_path"])
        offsets_path = path / "row_offsets.npy"
        offsets = np.asarray(np.load(offsets_path, allow_pickle=False), dtype=np.int64) if offsets_path.is_file() else None
        meta = self._result_metadata(row)
        requested_mode = str(params.get("mode") or "structure")
        if requested_mode not in ("structure", "atom"):
            raise AppError(ANALYSIS_INPUT_INVALID, "mode must be structure or atom")
        valid_offsets = self._valid_offsets(offsets, values.shape[0])
        level = str(meta.get("level") or "").lower()
        declared_atom = bool(meta.get("row_semantics") in ("atom", "local_environment", "pair") or any(token in level for token in ("atom", "local", "pair")))
        if requested_mode == "atom":
            if not valid_offsets:
                raise AppError(ANALYSIS_INPUT_INVALID, "atom/local-environment analysis requires verified row_offsets")
            if not declared_atom:
                raise AppError(ANALYSIS_INPUT_INVALID, "descriptor metadata does not declare atom/local-environment rows")
            local_frames = np.repeat(np.arange(len(offsets) - 1, dtype=np.int64), np.diff(offsets).astype(np.int64))
            frame_values = self._run_frame_values(row, len(offsets) - 1)
            frames = frame_values[local_frames]
            rows = np.arange(values.shape[0], dtype=np.int64) - offsets[local_frames]
            sample_ids = [f"frame:{int(f)}:row:{int(r)}" for f, r in zip(frames, rows)]
            used = values
            elements = self._atom_elements(row, offsets, local_frames)
            mode = "atom"
        elif valid_offsets and declared_atom:
            n_frames = len(offsets) - 1
            used = np.empty((n_frames, values.shape[1]), dtype=np.float64)
            for i in range(n_frames):
                lo, hi = int(offsets[i]), int(offsets[i + 1])
                used[i] = values[lo:hi].mean(axis=0) if hi > lo else 0.0
            frame_value = int(row.get("frame_index") or 0) if row.get("scope") == "frame" else 0
            frames = np.arange(n_frames, dtype=np.int64) + frame_value if row.get("scope") == "frame" else np.arange(n_frames, dtype=np.int64)
            rows = None
            sample_ids = [f"frame:{int(f)}" for f in frames]
            elements = None
            mode = "structure"
        else:
            used = values
            if row.get("scope") == "frame":
                frames = np.arange(values.shape[0], dtype=np.int64) + int(row.get("frame_index") or 0)
            else:
                frames = np.arange(values.shape[0], dtype=np.int64)
            rows = None
            sample_ids = [f"frame:{int(f)}" for f in frames]
            elements = None
            mode = "structure"
        return SampleMatrix(used, frames, rows, sample_ids, elements, mode)

    @staticmethod
    def _valid_offsets(offsets, n_rows: int) -> bool:
        try:
            return bool(offsets is not None and offsets.ndim == 1 and offsets.size >= 2 and int(offsets[0]) == 0 and int(offsets[-1]) == n_rows and np.all(np.diff(offsets) >= 0))
        except (TypeError, ValueError):
            return False

    def _atom_elements(self, run_row: dict, offsets, local_frames: np.ndarray) -> object:
        """Load element labels only when a dataset adapter can verify them."""
        import numpy as np

        if self.datasets is None:
            return None
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset is None:
            return None
        try:
            adapter = self.datasets._adapter_for(dataset)
            labels = []
            frame_values = self._run_frame_values(run_row, len(offsets) - 1)
            for frame_index in frame_values.tolist():
                labels.extend([int(z) for z in adapter.get_frame(int(frame_index)).numbers.tolist()])
            if len(labels) != len(local_frames):
                return None
            return np.asarray(labels, dtype=np.int64)
        except Exception:  # element labels are optional metadata, not a reason to corrupt a run
            return None

    @staticmethod
    def _result_metadata(row: dict) -> dict:
        try:
            return json.loads((Path(row["result_path"]) / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return {}

    def _build_preview(self, result: dict, samples: SampleMatrix, analysis_type: str) -> dict:
        arrays = result.get("arrays", {})
        preview = dict(result.get("preview") or {})

        def sample_identity(index: int) -> dict | None:
            if index < 0 or index >= samples.n_samples:
                return None
            item = {"i": index, "frame": int(samples.frame[index]), "sample_id": samples.sample_ids[index]}
            if samples.row is not None:
                item["row"] = int(samples.row[index])
            return item

        if "coords" in arrays:
            coords = np.asarray(arrays["coords"])
            count = min(coords.shape[0], samples.n_samples, _MAX_PREVIEW_POINTS)
            indices = np.linspace(0, coords.shape[0] - 1, count, dtype=np.int64) if coords.shape[0] > count else np.arange(coords.shape[0])
            points = []
            for i in indices.tolist():
                point = sample_identity(int(i))
                if point is None:
                    continue
                point.update({"x": float(coords[i, 0]), "y": float(coords[i, 1])})
                for key in ("labels", "scores", "distances"):
                    if key in arrays and np.asarray(arrays[key]).ndim == 1 and i < len(arrays[key]):
                        point[key[:-1] if key == "labels" else key] = float(arrays[key][i]) if key != "labels" else int(arrays[key][i])
                points.append(point)
            preview["points"] = points
            preview["total_points"] = int(coords.shape[0])
        elif "selected_indices" in arrays:
            selected = np.asarray(arrays["selected_indices"], dtype=np.int64)
            preview["selected"] = [item for i in selected[:_MAX_PREVIEW_POINTS].tolist() if (item := sample_identity(int(i))) is not None]
        elif analysis_type in ("similarity", "neighbors") and "indices" in arrays:
            indices = np.asarray(arrays["indices"], dtype=np.int64)
            distances = np.asarray(arrays.get("distances", []))
            similarities = np.asarray(arrays.get("similarity", []))
            rows = []
            if indices.ndim == 1:
                for rank, neighbor in enumerate(indices.tolist()):
                    item = sample_identity(int(neighbor))
                    if item is None:
                        continue
                    item["rank"] = rank + 1
                    if rank < distances.size:
                        item["distance"] = float(distances[rank])
                    if rank < similarities.size:
                        item["similarity"] = float(similarities[rank])
                    rows.append(item)
            elif indices.ndim == 2:
                for source_index in range(indices.shape[0]):
                    for rank, neighbor in enumerate(indices[source_index].tolist()):
                        if len(rows) >= _MAX_PREVIEW_POINTS:
                            break
                        item = sample_identity(int(neighbor))
                        source = sample_identity(source_index)
                        if item is None or source is None:
                            continue
                        item.update({"source_i": source_index, "source_frame": source["frame"], "rank": rank + 1})
                        if distances.ndim == 2 and rank < distances.shape[1]:
                            item["distance"] = float(distances[source_index, rank])
                        rows.append(item)
                    if len(rows) >= _MAX_PREVIEW_POINTS:
                        break
            preview["rows"] = rows
            preview["total_rows"] = int(indices.size if indices.ndim == 1 else indices.shape[0] * indices.shape[1])
        elif "labels" in arrays or "scores" in arrays or "distances" in arrays:
            lengths = [len(np.asarray(arrays[key])) for key in ("labels", "scores", "distances") if key in arrays and np.asarray(arrays[key]).ndim == 1]
            n = min([samples.n_samples, *lengths]) if lengths else samples.n_samples
            count = min(n, _MAX_PREVIEW_POINTS)
            rows = []
            for i in range(count):
                item = sample_identity(i)
                if item is None:
                    continue
                for key in ("labels", "scores", "distances"):
                    if key in arrays and i < len(arrays[key]):
                        value = np.asarray(arrays[key])[i]
                        if np.asarray(value).ndim == 0:
                            item[key] = int(value) if key == "labels" else float(value)
                        else:
                            item[key] = self._json_safe(value)
                rows.append(item)
            preview["rows"] = rows
            preview["total_rows"] = n
        return self._json_safe(preview)

    def _commit_artifact(self, analysis_id: str, analysis_type: str, input_ids: list[str], params: dict, result: dict, preview: dict, ctx) -> tuple[Path, dict]:
        import numpy as np

        root = self.data_dir / "analysis"
        root.mkdir(parents=True, exist_ok=True)
        staging = root / f".{analysis_id}.tmp-{uuid.uuid4().hex[:8]}"
        final = root / analysis_id
        staging.mkdir(parents=True, exist_ok=False)
        manifest = ArtifactManifest(
            analysis_id=analysis_id,
            analysis_type=analysis_type,
            input_run_ids=input_ids,
            algorithm_version=_ALGORITHM_VERSION,
            schema_version=_ANALYSIS_SCHEMA_VERSION,
        ).to_dict()
        try:
            arrays = result.get("arrays", {})
            for name, value in arrays.items():
                ctx.check_cancelled()
                array = np.asarray(value)
                if np.issubdtype(array.dtype, np.floating):
                    array = array.astype(np.float64, copy=False)
                safe_name = name.replace("/", "_")
                np.save(staging / f"{safe_name}.npy", array, allow_pickle=False)
                target = staging / f"{safe_name}.npy"
                manifest["files"][name] = {"path": target.name, "shape": list(array.shape), "dtype": str(array.dtype), "bytes": target.stat().st_size}
            metadata = AnalysisResult(
                analysis_id=analysis_id,
                analysis_type=analysis_type,
                input_run_ids=input_ids,
                parameters=params,
                algorithm_version=_ALGORITHM_VERSION,
                warnings=list(result.get("warnings", [])),
                preview=preview,
            ).to_metadata()
            metadata["created_at"] = _NOW()
            (staging / "metadata.json").write_text(json.dumps(self._json_safe(metadata), ensure_ascii=False, indent=2), encoding="utf-8")
            manifest["files"]["metadata"] = {"path": "metadata.json", "bytes": (staging / "metadata.json").stat().st_size}
            manifest["completed"] = True
            # The manifest describes the payload and metadata. It is not
            # listed inside itself, which keeps the byte metadata stable.
            (staging / "manifest.json").write_text(json.dumps(self._json_safe(manifest), ensure_ascii=False, indent=2), encoding="utf-8")
            if final.exists():
                raise AppError(ARTIFACT_INVALID, f"analysis artifact already exists: {final}")
            os.replace(staging, final)
            return final, manifest
        except Exception:
            self._rmtree_quiet(str(staging))
            raise

    def _write_export(self, run: dict, selected: list[int], export_format: str, mode: str, target: Path, ctx) -> Path:
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
        target = target.expanduser()
        if export_format == "json":
            target.parent.mkdir(parents=True, exist_ok=True)
            records = [{"sample_index": i, "frame": frame, "sample_id": f"frame:{frame}"} for i, frame in enumerate(frames)]
            target.write_text(json.dumps({"dataset_id": dataset["id"], "run_id": run["id"], "format": dataset["format"], "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
            return target
        if export_format == "csv":
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", newline="", encoding="utf-8") as fh:
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
        target.mkdir(parents=True, exist_ok=True)
        self._write_deepmd(adapter, frames, target, ctx)
        return target

    @staticmethod
    def _write_extxyz(adapter, frames: list[int], target: Path, ctx) -> None:
        from ..datasets.deepmd_symbols import _Z_TO_SYMBOL

        with target.open("w", encoding="utf-8") as fh:
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
        import numpy as np
        from ..datasets.deepmd_symbols import _Z_TO_SYMBOL

        first = adapter.get_frame(frames[0])
        numbers = np.asarray(first.numbers, dtype=np.int64)
        unique = sorted({int(z) for z in numbers.tolist()})
        type_map = {z: i for i, z in enumerate(unique)}
        (target / "type.raw").write_text(" ".join(str(type_map[int(z)]) for z in numbers) + "\n", encoding="utf-8")
        (target / "type_map.raw").write_text(" ".join(_Z_TO_SYMBOL.get(z, f"Z{z}") for z in unique) + "\n", encoding="utf-8")
        set_dir = target / "set.000"
        set_dir.mkdir(parents=True, exist_ok=True)
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
        np.save(set_dir / "coord.npy", np.stack(coords))
        if any(np.abs(cell).sum() > 1e-12 for cell in cells):
            np.save(set_dir / "box.npy", np.stack(cells))
        else:
            (set_dir / "nopbc").write_text("", encoding="utf-8")
        if has_energy:
            np.save(set_dir / "energy.npy", np.asarray(energies, dtype=np.float64))
        if has_forces:
            np.save(set_dir / "force.npy", np.stack(forces))
        if has_virial:
            np.save(set_dir / "virial.npy", np.stack(virials))

    def _analysis_row(self, analysis_id: str | None) -> dict:
        if not analysis_id:
            raise AppError(INVALID_PARAMS, "'analysis_id' is required")
        row = self.db.query_one("SELECT * FROM analysis_runs WHERE id = ?", (analysis_id,))
        if row is None:
            raise AppError(ANALYSIS_NOT_FOUND, f"analysis {analysis_id} does not exist")
        return row

    def _public_analysis_row(self, row: dict, include_preview: bool = False) -> dict:
        output = dict(row)
        for key in ("input_run_ids_json", "dataset_ids_json", "warnings_json", "artifact_manifest_json", "preprocessing_json"):
            target = key.removesuffix("_json")
            output[target] = self._json_load(output.pop(key, None), [] if key.endswith("ids_json") or key == "warnings_json" else {})
        output.pop("params_json", None)
        output["parameters"] = self._json_load(row.get("params_json"), {})
        if include_preview:
            output["preview"] = self._json_load(row.get("preview_json"), {})
            output.pop("preview_json", None)
        else:
            output.pop("preview_json", None)
        return output

    def _require_artifact(self, row: dict) -> None:
        # STALE artifacts remain readable for audit and comparison history;
        # only _usable_run rejects stale descriptor inputs for new work.
        if row.get("status") not in ("COMPLETED", "STALE"):
            raise AppError(RESULT_INCOMPATIBLE, f"analysis {row['id']} is {row['status']}")
        if not self._artifact_is_complete(row):
            raise AppError(ARTIFACT_INVALID, f"analysis {row['id']} has no complete artifact")

    @staticmethod
    def _artifact_is_complete(row: dict) -> bool:
        path = row.get("result_path")
        if not path:
            return False
        root = Path(path)
        if not (root / "manifest.json").is_file():
            # legacy PCA is valid through its compatibility artifact
            return (root / "pca.json").is_file()
        try:
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            if manifest.get("completed") is not True:
                return False
            for file_meta in (manifest.get("files") or {}).values():
                if isinstance(file_meta, dict) and not (root / str(file_meta.get("path", ""))).is_file():
                    return False
            return True
        except (OSError, TypeError, ValueError):
            return False

    @staticmethod
    def _json_load(raw, fallback):
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
            return value if value is not None else fallback
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _canonical_params(params: dict) -> dict:
        def normalize(value):
            if isinstance(value, dict):
                return {str(k): normalize(value[k]) for k in sorted(value)}
            if isinstance(value, (list, tuple)):
                return [normalize(v) for v in value]
            if isinstance(value, float):
                if not np.isfinite(value):
                    raise AppError(ANALYSIS_INPUT_INVALID, "analysis parameters must be finite")
                return float(format(value, ".15g"))
            return value

        return normalize(params)

    def _rows_from_artifact(self, row: dict, offset: int, limit: int) -> list[dict]:
        manifest = self._json_load(row.get("artifact_manifest_json"), {})
        files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
        arrays = {}
        for name in ("coords", "indices", "labels", "scores", "distances", "similarity", "selected_indices"):
            meta = files.get(name)
            if isinstance(meta, dict):
                try:
                    arrays[name] = np.load(Path(row["result_path"]) / meta["path"], mmap_mode="r", allow_pickle=False)
                except (OSError, ValueError):
                    pass
        input_ids = self._json_load(row.get("input_run_ids_json"), [row.get("descriptor_run_id")])
        if isinstance(input_ids, list) and input_ids:
            source_index = 1 if row.get("analysis_type") in ("coverage", "drift") and len(input_ids) > 1 else 0
            source_row = self.db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (input_ids[source_index],))
            if source_row is not None:
                try:
                    params = self._json_load(row.get("params_json"), {})
                    built = self._build_preview({"arrays": arrays}, self._load_samples(source_row, params, row.get("analysis_type") or ""), row.get("analysis_type") or "")
                    candidate = built.get("rows") or built.get("selected") or built.get("points") or []
                    return candidate[offset : offset + limit]
                except (AppError, OSError, TypeError, ValueError, IndexError):
                    pass
        n = max((len(v) for v in arrays.values()), default=0)
        return [{"i": i, **{name: self._json_safe(value[i]) for name, value in arrays.items() if i < len(value)}} for i in range(offset, min(offset + limit, n))]

    def _mark_stale(self, dataset_id: str, reason: str) -> None:
        now = _NOW()
        self.db.execute(
            "UPDATE descriptor_runs SET status = 'STALE', error_message = ?, finished_at = ? WHERE dataset_id = ? AND status = 'COMPLETED'",
            (reason, now, dataset_id),
        )
        self.db.execute(
            "UPDATE analysis_runs SET status = 'STALE', stale_reason = ?, finished_at = ?, updated_at = ?"
            " WHERE status = 'COMPLETED' AND (descriptor_run_id IN"
            " (SELECT id FROM descriptor_runs WHERE dataset_id = ?) OR dataset_ids_json LIKE ?)",
            (reason, now, now, dataset_id, f'%"{dataset_id}"%'),
        )

    @staticmethod
    def _json_safe(value):
        if isinstance(value, dict):
            return {str(k): AnalysisService._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [AnalysisService._json_safe(v) for v in value]
        if isinstance(value, np.ndarray):
            return AnalysisService._json_safe(value.tolist())
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        return value

    @staticmethod
    def _rmtree_quiet(path: str | None) -> None:
        import shutil

        if path:
            shutil.rmtree(path, ignore_errors=True)
