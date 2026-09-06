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
import logging
import re
import threading
import unicodedata
import uuid
from dataclasses import replace
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
from ..security import (
    UnsafePathError,
    ensure_no_reparse_points,
    escape_like,
    open_text_for_write,
    remove_managed_tree,
    validate_local_path,
    validate_managed_path,
)

log = logging.getLogger(__name__)

_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731
_ANALYSIS_SCHEMA_VERSION = 1
_ALGORITHM_VERSION = "studio-analysis-2"
_MAX_PREVIEW_POINTS = 20_000
_ANALYSIS_ID_RE = re.compile(r"^ana_[A-Za-z0-9_-]{1,64}$")
_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_RESERVED_ARTIFACT_NAME_RE = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE)


def _pool_rows(values: np.ndarray, offsets: np.ndarray) -> np.ndarray:
    """Mean-pool atom/pair rows per structure in one vectorized pass.

    Equivalent to the historical per-structure Python loop (which held the GIL
    for seconds on large atom-level runs), but each pool step also runs with
    the GIL released. Empty structures pool to zero; np.add.reduceat needs two
    quirks handled explicitly: a start index equal to len(values) (trailing
    empty structures) is out of bounds, and repeated indices (empty groups)
    return a single element instead of zero — both are masked below.
    """
    values = np.asarray(values, dtype=np.float64)
    counts = np.diff(offsets).astype(np.int64)
    pooled = np.zeros((counts.size, values.shape[1]), dtype=np.float64)
    starts = offsets[:-1]
    # offsets are monotonic and end at len(values), so starts >= len(values)
    # can only be a run of trailing empty structures — trim them.
    k = int(np.searchsorted(starts, values.shape[0], side="left"))
    if k > 0:
        sums = np.add.reduceat(values, starts[:k], axis=0)
        pooled[:k] = sums / np.maximum(counts[:k], 1)[:, None]
    pooled[counts == 0] = 0.0
    return pooled


class AnalysisService:
    def __init__(self, db: Database, jobs: JobService, results: ResultService, datasets, data_dir: Path):
        self.db = db
        self.jobs = jobs
        self.results = results
        self.datasets = datasets
        self.data_dir = Path(data_dir).resolve(strict=False)
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
        cache_key = self._analysis_cache_key("pca", [run_id], analysis_params)

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
                    "INSERT INTO analysis_runs (id, descriptor_run_id, analysis_type, params_json, status, created_at,"
                    " input_run_ids_json, dataset_ids_json, cache_key, schema_version, algorithm_version,"
                    " preprocessing_json, warnings_json, artifact_manifest_json, preview_json, updated_at)"
                    " VALUES (?, ?, 'pca', ?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        analysis_id,
                        run_id,
                        params_json,
                        _NOW(),
                        json.dumps([run_id], ensure_ascii=False),
                        json.dumps([row["dataset_id"]], ensure_ascii=False),
                        cache_key,
                        _ANALYSIS_SCHEMA_VERSION,
                        _ALGORITHM_VERSION,
                        json.dumps({"preprocess": preprocess}, ensure_ascii=False),
                        json.dumps([], ensure_ascii=False),
                        json.dumps({}, ensure_ascii=False),
                        json.dumps({}, ensure_ascii=False),
                        _NOW(),
                    ),
                )

            def runner(ctx):
                self._mark_run_running(analysis_id)
                ctx.progress(0, 1, "loading values")
                self._apply_thread_limit()
                values, run_row = self.results.load_values(run_id)
                ctx.check_cancelled()
                coords, explained, frames, atoms = self._pca_points(values, run_row, mode, preprocess)
                ctx.check_cancelled()
                n_points = coords.shape[0]
                ctx.progress(0.7, 1, "assembling points")
                frame_props = self._frame_properties(
                    run_row, int(frames.max()) + 1 if frames.size else 1
                )
                out_dir = self.data_dir / "analysis" / analysis_id
                out_dir.mkdir(parents=True, exist_ok=True)
                ensure_no_reparse_points(out_dir)
                np.save(out_dir / "coords.npy", coords, allow_pickle=False)
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
                with open_text_for_write(out_dir / "pca.json") as fh:
                    json.dump(payload, fh, ensure_ascii=False)
                self.db.execute(
                    "UPDATE analysis_runs SET status = 'COMPLETED', result_path = ?, finished_at = ? WHERE id = ?",
                    (str(out_dir), _NOW(), analysis_id),
                )
                ctx.progress(1, 1, "done")
                return {"analysis_id": analysis_id, "n_points": payload["n_points"]}

            try:
                job_id = self.jobs.submit(
                    "analysis.pca", runner, dataset_id=row["dataset_id"], analysis_run_id=analysis_id
                )
            except Exception:
                if active is None:
                    self.db.execute("DELETE FROM analysis_runs WHERE id = ?", (analysis_id,))
                raise
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

    def _pca_artifact_exists(self, row: dict) -> bool:
        if not row.get("result_path"):
            return False
        try:
            root = self._managed_artifact_path(str(row.get("id") or ""), row["result_path"])
            target = self._artifact_file(root, "pca.json")
            return bool(target and target.is_file())
        except (TypeError, ValueError, UnsafePathError):
            return False

    def _result_root(self, row: dict) -> Path:
        try:
            root = self.results._managed_result_path(str(row.get("id") or ""), row.get("result_path"))
            if not root.is_dir():
                raise OSError("descriptor result directory is missing")
            ensure_no_reparse_points(root)
            return root
        except (OSError, TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(RESULT_INCOMPATIBLE, "descriptor result artifact is unavailable") from exc

    def _pca_points(self, values: np.ndarray, run_row: dict, mode: str, preprocess: str = "center"):
        """Return (coords, explained, frames, atoms|None).

        Structure mode: one point per frame (atom/pair rows mean-pooled).
        Atom mode: one point per atom/pair row, keeping the owning frame and the
        in-frame row index for tooltips/reverse-jump. Very large runs are evenly
        subsampled so the IPC payload and chart stay responsive (design doc §25).
        """
        path = self._result_root(run_row)
        offsets_file = path / "row_offsets.npy"
        ensure_no_reparse_points(offsets_file)
        offsets = np.asarray(np.load(offsets_file, allow_pickle=False), dtype=np.int64) if offsets_file.is_file() else None
        atom_level = values.ndim == 2 and self._valid_offsets(offsets, values.shape[0])
        if mode == "atom" and not atom_level:
            raise AppError(ANALYSIS_INPUT_INVALID, "atom/local-environment PCA requires verified row_offsets")
        if mode == "structure" or not atom_level:
            pooled = self._pool_per_structure(values, run_row)
            frames = self._run_frame_values(run_row, pooled.shape[0])
            atoms = None
        else:
            counts = np.diff(offsets).astype(int)
            local_frames = np.repeat(np.arange(counts.size, dtype=np.int64), counts)
            frame_values = self._run_frame_values(run_row, counts.size)
            frames = frame_values[local_frames]
            atoms = np.arange(values.shape[0], dtype=np.int64) - offsets[local_frames]
            pooled = values
            max_points = 20000
            if pooled.shape[0] > max_points:
                keep = np.unique(np.linspace(0, pooled.shape[0] - 1, max_points).astype(int))
                pooled, frames, atoms = pooled[keep], frames[keep], atoms[keep]
        coords, explained = self._pca(pooled, preprocess)
        return coords, explained, frames, atoms

    @staticmethod
    def _run_frame_values(run_row: dict, count: int) -> np.ndarray:
        if run_row.get("scope") == "frame":
            return np.full(count, int(run_row.get("frame_index") or 0), dtype=np.int64)
        return np.arange(count, dtype=np.int64)

    def _pool_per_structure(self, values: np.ndarray, run_row: dict) -> np.ndarray:
        path = self._result_root(run_row)
        offsets_file = path / "row_offsets.npy"
        ensure_no_reparse_points(offsets_file)
        if values.ndim == 2 and offsets_file.is_file():
            offsets = np.asarray(np.load(offsets_file, allow_pickle=False), dtype=np.int64)
            if self._valid_offsets(offsets, values.shape[0]):
                return _pool_rows(values, offsets)
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
            run_id = str(params["run_id"])
            conditions.append("(descriptor_run_id = ? OR input_run_ids_json LIKE ? ESCAPE '!')")
            args.extend([run_id, f'%"{escape_like(run_id)}"%'])
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
        try:
            artifact_path = self._managed_artifact_path(str(analysis_id), row.get("result_path")) if row.get("result_path") else None
        except (TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(ARTIFACT_INVALID, "stored analysis artifact path is invalid") from exc
        self.db.execute("DELETE FROM jobs WHERE analysis_run_id = ?", (analysis_id,))
        self.db.execute("DELETE FROM analysis_runs WHERE id = ?", (analysis_id,))
        self._rmtree_quiet(artifact_path)
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
        has_bounded_data = False
        if isinstance(preview.get("points"), list):
            preview = {**preview, "points": preview["points"][offset : offset + limit]}
            has_bounded_data = True
        if isinstance(preview.get("rows"), list):
            preview = {**preview, "rows": preview["rows"][offset : offset + limit]}
            has_bounded_data = True
        if not has_bounded_data:
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
        try:
            root = self._managed_artifact_path(str(row["id"]), row.get("result_path"))
        except (TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(ARTIFACT_INVALID, "stored analysis artifact path is invalid") from exc
        path = self._artifact_file(root, file_meta.get("path"))
        if path is None or not path.is_file():
            raise AppError(ARTIFACT_INVALID, "missing analysis artifact array")
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

    def pairwise(self, params: dict) -> dict:
        return self.submit_generic("pairwise", params)

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

    def overlap(self, params: dict) -> dict:
        return self.submit_generic("overlap", params)

    def acquisition(self, params: dict) -> dict:
        return self.submit_generic("acquisition", params)

    def compare(self, params: dict) -> dict:
        return self.submit_generic("compare", params)

    def feature_variance(self, params: dict) -> dict:
        return self.submit_generic("feature_variance", params)

    def feature_correlation(self, params: dict) -> dict:
        return self.submit_generic("feature_correlation", params)

    def property_correlation(self, params: dict) -> dict:
        return self.submit_generic("property_correlation", params)

    def local_diversity(self, params: dict) -> dict:
        params = {**dict(params or {}), "mode": "atom"}
        return self.submit_generic("local_diversity", params)

    def kernel(self, params: dict) -> dict:
        return self.submit_generic("kernel", params)

    def effective_dimension(self, params: dict) -> dict:
        return self.submit_generic("effective_dimension", params)

    def trajectory(self, params: dict) -> dict:
        return self.submit_generic("trajectory", params)

    def drift(self, params: dict) -> dict:
        return self.submit_generic("drift", params)

    def sensitivity(self, params: dict) -> dict:
        return self.submit_generic("sensitivity", params)

    def mantel(self, params: dict) -> dict:
        return self.submit_generic("mantel", params)

    def perturbation_sensitivity(self, params: dict) -> dict:
        return self.submit_generic("perturbation_sensitivity", params)

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
        if analysis_type == "sensitivity":
            descriptor_names = {str(row["descriptor_name"]) for row in run_rows}
            if len(descriptor_names) > 1:
                raise AppError(
                    ANALYSIS_INPUT_INVALID,
                    "parameter sensitivity requires the same descriptor; use Compare for different descriptors",
                    {"descriptors": sorted(descriptor_names)},
                )
        canonical_params = self._canonical_params(params)
        cache_key = self._analysis_cache_key(analysis_type, input_ids, canonical_params)
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
                self._mark_run_running(analysis_id)
                ctx.progress(0, 1, "loading descriptor results")
                samples = [self._load_samples(row, params, analysis_type, check=ctx.check_cancelled) for row in run_rows]
                ctx.check_cancelled()
                result = self._run_engine(analysis_type, params, run_rows, samples, ctx)
                ctx.check_cancelled()
                cross_dataset = analysis_type in ("coverage", "overlap", "acquisition", "drift")
                preview_samples = samples[1] if cross_dataset else samples[0]
                preview = self._build_preview(
                    result,
                    preview_samples,
                    analysis_type,
                    reference_samples=samples[0] if cross_dataset else None,
                )
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
                n_points = preview_samples.n_samples if cross_dataset else samples[0].n_samples
                return {"analysis_id": analysis_id, "n_points": n_points, "analysis_type": analysis_type, "warnings": result.get("warnings", [])}

            try:
                job_id = self.jobs.submit(
                    f"analysis.{analysis_type}",
                    runner,
                    dataset_id=primary["dataset_id"],
                    analysis_run_id=analysis_id,
                )
            except Exception:
                if not active:
                    self.db.execute("DELETE FROM analysis_runs WHERE id = ?", (analysis_id,))
                raise
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
        canonical = self._canonical_params({"format": export_format, "indices": sorted(set(selected)), "output_path": target, "mode": mode})
        cache_key = self._analysis_cache_key("export", input_ids, canonical)

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
                self._mark_run_running(analysis_id)
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

            try:
                job_id = self.jobs.submit("analysis.export", runner, dataset_id=run["dataset_id"], analysis_run_id=analysis_id)
            except Exception:
                if not active:
                    self.db.execute("DELETE FROM analysis_runs WHERE id = ?", (analysis_id,))
                raise
            return {"job_id": job_id, "analysis_id": analysis_id, "cache": None}

    def _mark_run_running(self, analysis_id: str) -> None:
        """Flip the analysis run to RUNNING when its job actually starts.
        Without this the row sits at QUEUED for the whole computation and the
        history shows 排队中 for a result that is being computed right now.
        The WHERE guard keeps a cancel-settled row from being resurrected."""
        self.db.execute(
            "UPDATE analysis_runs SET status = 'RUNNING', updated_at = ? WHERE id = ? AND status IN ('QUEUED', 'RUNNING')",
            (_NOW(), analysis_id),
        )

    def _apply_thread_limit(self) -> None:
        """Apply the user's `compute.default_threads` setting to the numeric
        stack used by analysis compute (BLAS/OpenMP pools via threadpoolctl).

        Applied process-wide and deliberately left in place: the setting is a
        user preference for analysis parallelism, not a per-job override, and
        concurrent analysis jobs all read the same value. Descriptor engine
        threads are managed by the engine itself and unaffected. Missing,
        invalid, or non-positive values mean "engine default" (no change).
        """
        raw = self.db.get_setting("compute.default_threads")
        if not raw:
            return
        try:
            limit = int(str(raw).strip())
        except (TypeError, ValueError):
            return
        if limit <= 0:
            return
        from threadpoolctl import threadpool_limits

        threadpool_limits(limits=limit)

    def _run_engine(self, analysis_type: str, params: dict, rows: list[dict], samples: list[SampleMatrix], ctx) -> dict:
        progress = lambda fraction, message: (ctx.check_cancelled(), ctx.progress(None, None, message, fraction=0.1 + 0.85 * float(fraction)))
        self._apply_thread_limit()
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
        if analysis_type == "pairwise":
            return AnalysisEngine.pairwise(samples[0], params, progress)
        if analysis_type in ("kmeans", "dbscan", "hdbscan", "agglomerative", "hierarchical"):
            return AnalysisEngine.cluster(samples[0], params, analysis_type, progress)
        if analysis_type in ("knn", "lof", "isolation_forest", "isolation-forest", "iforest", "mahalanobis", "mahalanobis_distance"):
            return AnalysisEngine.outlier(samples[0], params, analysis_type, progress)
        if analysis_type in ("fps", "random", "stratified", "cluster_representative", "cluster", "per_element", "element"):
            return AnalysisEngine.sampling(samples[0], params, analysis_type, progress)
        if analysis_type == "coverage":
            return AnalysisEngine.coverage(samples[0], samples[1], params, progress)
        if analysis_type == "overlap":
            return AnalysisEngine.overlap(samples[0], samples[1], params, progress)
        if analysis_type == "acquisition":
            return AnalysisEngine.acquisition(samples[0], samples[1], params, progress)
        if analysis_type == "compare":
            return AnalysisEngine.compare(samples[0], samples[1], params, progress)
        if analysis_type == "mantel":
            return AnalysisEngine.mantel(samples[0], samples[1], params, progress)
        if analysis_type == "feature_variance":
            return AnalysisEngine.feature_variance(samples[0], params, progress)
        if analysis_type == "feature_correlation":
            return AnalysisEngine.feature_correlation(samples[0], params, progress)
        if analysis_type == "property_correlation":
            return AnalysisEngine.property_correlation(samples[0], params, progress)
        if analysis_type == "local_diversity":
            return AnalysisEngine.local_diversity(samples[0], params, progress)
        if analysis_type == "kernel":
            return AnalysisEngine.kernel(samples[0], params, progress)
        if analysis_type == "effective_dimension":
            return AnalysisEngine.effective_dimension(samples[0], params, progress)
        if analysis_type == "trajectory":
            return AnalysisEngine.trajectory(samples[0], params, progress)
        if analysis_type == "drift":
            return AnalysisEngine.drift(samples[0], samples[1], params, progress)
        if analysis_type == "sensitivity":
            return AnalysisEngine.sensitivity(list(zip(rows, samples)), params, progress)
        if analysis_type == "perturbation_sensitivity":
            return self._run_perturbation_sensitivity(params, rows[0], samples[0], ctx)
        raise AppError(ANALYSIS_INPUT_INVALID, f"unsupported analysis type: {analysis_type}")

    def _run_perturbation_sensitivity(self, params: dict, run_row: dict, samples: SampleMatrix, ctx) -> dict:
        """Recompute one descriptor on deterministic structure perturbations."""
        if self.datasets is None:
            raise AppError(ANALYSIS_INPUT_INVALID, "structural perturbation requires the dataset service")
        if samples.mode != "structure":
            raise AppError(ANALYSIS_INPUT_INVALID, "structural perturbation sensitivity is structure-level only")
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset is None:
            raise AppError(ANALYSIS_INPUT_INVALID, f"dataset {run_row['dataset_id']} does not exist")
        source_adapter = self.datasets._adapter_for(dataset)
        frame_count = samples.n_samples
        if frame_count < 1:
            raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "at least one structure is required")
        max_structures = params.get("max_structures", 64)
        try:
            max_structures = int(max_structures)
        except (TypeError, ValueError) as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "max_structures must be an integer") from exc
        if max_structures < 1:
            raise AppError(ANALYSIS_INPUT_INVALID, "max_structures must be >= 1")
        selected_indices = np.linspace(0, frame_count - 1, min(frame_count, max_structures), dtype=np.int64)
        selected_frames = np.asarray(samples.frame, dtype=np.int64)[selected_indices]
        baseline = SampleMatrix(
            values=np.asarray(samples.values, dtype=np.float64)[selected_indices],
            frame=selected_frames,
            sample_ids=[samples.sample_ids[int(index)] for index in selected_indices.tolist()],
            mode="structure",
        )

        perturbation = str(params.get("perturbation") or "jitter").lower()
        if perturbation not in ("jitter", "strain"):
            raise AppError(ANALYSIS_INPUT_INVALID, "perturbation must be jitter or strain")
        raw_amplitudes = params.get("amplitudes")
        if raw_amplitudes is None:
            try:
                count = int(params.get("n_amplitudes", 8))
                maximum = float(params.get("max_amplitude", 0.2 if perturbation == "jitter" else 0.1))
            except (TypeError, ValueError) as exc:
                raise AppError(ANALYSIS_INPUT_INVALID, "n_amplitudes and max_amplitude must be numeric") from exc
            if count < 2:
                raise AppError(ANALYSIS_INPUT_INVALID, "n_amplitudes must be >= 2")
            raw_amplitudes = np.linspace(0.0, maximum, count).tolist()
        if not isinstance(raw_amplitudes, (list, tuple)):
            raise AppError(ANALYSIS_INPUT_INVALID, "amplitudes must be a list of numbers")
        if len(raw_amplitudes) < 2 or len(raw_amplitudes) > 32:
            raise AppError(ANALYSIS_INPUT_INVALID, "amplitudes must contain between 2 and 32 values")
        try:
            amplitudes = [float(value) for value in raw_amplitudes]
        except (TypeError, ValueError) as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "amplitudes must be a list of numbers") from exc
        if not all(np.isfinite(value) and value >= 0 for value in amplitudes):
            raise AppError(ANALYSIS_INPUT_INVALID, "amplitudes must be finite and non-negative")
        if perturbation == "strain" and any(value >= 1.0 for value in amplitudes):
            raise AppError(ANALYSIS_INPUT_INVALID, "strain amplitudes must be smaller than 1")
        if not any(np.isclose(value, 0.0) for value in amplitudes):
            amplitudes = [0.0, *amplitudes]

        try:
            seed = int(params.get("seed", 42))
        except (TypeError, ValueError) as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "seed must be an integer") from exc
        rng = np.random.default_rng(seed)
        source_frames = [source_adapter.get_frame(int(frame)) for frame in selected_frames.tolist()]
        jitter_vectors = []
        for frame in source_frames:
            vector = rng.normal(size=np.asarray(frame.positions).shape)
            scale = float(np.sqrt(np.mean(vector * vector)))
            jitter_vectors.append(vector / max(scale, 1e-15))

        try:
            descriptor_parameters = json.loads(run_row.get("parameters_json") or "{}")
        except (TypeError, ValueError):
            descriptor_parameters = {}
        # Sensitivity recomputes the producer descriptor; keep the device the
        # run originally declared so baselines stay reproducible.
        run_device = str(run_row.get("device") or "cpu")
        descriptor = self.datasets.adapter.build(run_row["descriptor_name"], descriptor_parameters, device=run_device)
        control = self.datasets.adapter.make_control()
        ctx.attach_control(control)
        perturbation_results: list[tuple[float, SampleMatrix]] = []
        for amplitude_index, amplitude in enumerate(amplitudes):
            ctx.check_cancelled()
            perturbed_frames = [
                self._perturb_frame(frame, amplitude, perturbation, jitter_vectors[index])
                for index, frame in enumerate(source_frames)
            ]
            batch = self.datasets.adapter.to_structure_batch(perturbed_frames)
            computed = self.datasets.adapter.compute(descriptor, batch, control)
            values = self._computed_structure_values(computed, len(perturbed_frames))
            perturbation_results.append((
                amplitude,
                SampleMatrix(
                    values=values,
                    frame=selected_frames.copy(),
                    sample_ids=list(baseline.sample_ids),
                    mode="structure",
                ),
            ))
            ctx.progress(
                amplitude_index + 1,
                len(amplitudes),
                "computing structural perturbations",
                fraction=0.1 + 0.65 * (amplitude_index + 1) / len(amplitudes),
            )

        def report(fraction: float, message: str) -> None:
            ctx.check_cancelled()
            ctx.progress(None, None, message, fraction=0.75 + 0.25 * float(fraction))

        return AnalysisEngine.perturbation_sensitivity(baseline, perturbation_results, params, report)

    @staticmethod
    def _perturb_frame(frame, amplitude: float, perturbation: str, jitter_vector: np.ndarray):
        positions = np.asarray(frame.positions, dtype=np.float64)
        if perturbation == "jitter":
            return replace(frame, positions=positions + amplitude * jitter_vector)
        center = positions.mean(axis=0, keepdims=True) if positions.size else np.zeros((1, 3), dtype=np.float64)
        scale = 1.0 + amplitude
        cell = np.asarray(frame.cell, dtype=np.float64)
        if cell.shape == (3, 3) and abs(float(np.linalg.det(cell))) > 1e-10:
            cell = cell * scale
        return replace(frame, positions=center + (positions - center) * scale, cell=cell)

    def _computed_structure_values(self, computed, frame_count: int) -> np.ndarray:
        values = np.asarray(computed.values, dtype=np.float64)
        if values.ndim > 2:
            values = values.reshape(values.shape[0], -1)
        if values.ndim == 1:
            values = values.reshape(-1, 1)
        if values.ndim != 2 or not np.isfinite(values).all():
            raise AppError(ANALYSIS_INPUT_INVALID, "perturbed descriptor result is not a finite 2D matrix")
        offsets = np.asarray(getattr(computed, "row_offsets", None), dtype=np.int64) if getattr(computed, "row_offsets", None) is not None else None
        if self._valid_offsets(offsets, values.shape[0]) and offsets.size == frame_count + 1:
            pooled = np.empty((frame_count, values.shape[1]), dtype=np.float64)
            for index in range(frame_count):
                lo, hi = int(offsets[index]), int(offsets[index + 1])
                pooled[index] = values[lo:hi].mean(axis=0) if hi > lo else 0.0
            return pooled
        if values.shape[0] == frame_count:
            return values
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "perturbed descriptor result cannot be aligned to structures",
            {"rows": int(values.shape[0]), "structures": frame_count},
        )

    def _input_ids(self, analysis_type: str, params: dict) -> list[str]:
        if analysis_type in ("coverage", "overlap", "acquisition", "drift"):
            ids = [params.get("reference_run_id"), params.get("query_run_id")]
        elif analysis_type in ("compare", "mantel"):
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
        if analysis_type in ("coverage", "overlap", "acquisition", "drift", "compare", "mantel") and len(ids) != 2:
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
        refresh = getattr(self.datasets, "refresh_if_changed", None)
        if callable(refresh):
            refresh(dataset)
            return
        from ..datasets import compute_fingerprint

        try:
            source = validate_local_path(dataset["source_path"], field="dataset source path")
            current = compute_fingerprint(source, dataset["number_of_frames"], use_cache=False)
        except (OSError, TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(RESULT_INCOMPATIBLE, "dataset source is unavailable") from exc
        if current != dataset["fingerprint"]:
            self._mark_stale(row["dataset_id"], f"source fingerprint changed ({dataset['fingerprint']} -> {current})")
            raise AppError(ANALYSIS_STALE, f"run {row['id']} is stale because the source dataset changed", {"run_id": row["id"], "dataset_id": row["dataset_id"]})

    def _load_samples(self, run_row: dict, params: dict, analysis_type: str, check=None) -> SampleMatrix:
        import numpy as np

        # Cooperative-cancellation checkpoints between the load/pool stages:
        # without them a cancel during a multi-GB load waits for the whole
        # phase to finish before it takes effect.
        check = check or (lambda: None)
        values, row = self.results.load_values(run_row["id"])
        check()
        values = np.asarray(values, dtype=np.float64)
        if values.ndim > 2:
            values = values.reshape(values.shape[0], -1)
        if values.ndim == 1:
            values = values.reshape(-1, 1)
        if values.ndim != 2 or values.shape[0] == 0 or values.shape[1] == 0:
            raise AppError(ANALYSIS_INPUT_INVALID, "descriptor result is empty or not a 2D feature matrix")
        if not np.isfinite(values).all():
            raise AppError(ANALYSIS_INPUT_INVALID, "descriptor result contains NaN or Inf")
        check()
        path = self._result_root(row)
        offsets_path = path / "row_offsets.npy"
        ensure_no_reparse_points(offsets_path)
        offsets = np.asarray(np.load(offsets_path, allow_pickle=False), dtype=np.int64) if offsets_path.is_file() else None
        meta = self._result_metadata(row)
        requested_mode = str(params.get("mode") or "structure")
        if requested_mode not in ("structure", "atom"):
            raise AppError(ANALYSIS_INPUT_INVALID, "mode must be structure or atom")
        valid_offsets = self._valid_offsets(offsets, values.shape[0])
        positions = None
        cells = None
        pbc = None
        level = str(meta.get("level") or "").lower()
        declared_atom = bool(meta.get("row_semantics") in ("atom", "local_environment", "pair") or any(token in level for token in ("atom", "local", "pair")))
        if requested_mode == "atom":
            if not valid_offsets:
                raise AppError(ANALYSIS_INPUT_INVALID, "atom/local-environment analysis requires verified row_offsets")
            if not declared_atom:
                raise AppError(ANALYSIS_INPUT_INVALID, "descriptor metadata does not declare atom/local-environment rows")
            check()
            local_frames = np.repeat(np.arange(len(offsets) - 1, dtype=np.int64), np.diff(offsets).astype(np.int64))
            frame_values = self._run_frame_values(row, len(offsets) - 1)
            frames = frame_values[local_frames]
            rows = np.arange(values.shape[0], dtype=np.int64) - offsets[local_frames]
            sample_ids = [f"frame:{int(f)}:row:{int(r)}" for f, r in zip(frames, rows)]
            used = values
            elements = self._atom_elements(row, offsets, local_frames)
            positions, cells, pbc = self._atom_geometry(row, offsets, local_frames)
            mode = "atom"
        elif valid_offsets and declared_atom:
            used = _pool_rows(values, offsets)
            check()
            n_frames = used.shape[0]
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
        properties = self._sample_properties(row, frames, rows, mode) if analysis_type == "property_correlation" else {}
        return SampleMatrix(
            values=used,
            frame=frames,
            row=rows,
            sample_ids=sample_ids,
            elements=elements,
            mode=mode,
            properties=properties,
            positions=positions,
            cells=cells,
            pbc=pbc,
        )

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

    def _atom_geometry(self, run_row: dict, offsets, local_frames: np.ndarray) -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None]:
        """Load atom coordinates/cells for local-environment neighbor analysis."""
        if self.datasets is None:
            return None, None, None
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset is None:
            return None, None, None
        try:
            adapter = self.datasets._adapter_for(dataset)
            frame_values = self._run_frame_values(run_row, len(offsets) - 1)
            position_rows: list[np.ndarray] = []
            cell_rows: list[np.ndarray] = []
            pbc_rows: list[np.ndarray] = []
            for offset_index, frame_index in enumerate(frame_values.tolist()):
                frame = adapter.get_frame(int(frame_index))
                expected = int(offsets[offset_index + 1] - offsets[offset_index])
                frame_positions = np.asarray(frame.positions, dtype=np.float64)
                if frame_positions.shape != (expected, 3):
                    return None, None, None
                position_rows.append(frame_positions)
                cell = np.asarray(frame.cell, dtype=np.float64)
                if cell.shape != (3, 3):
                    cell = np.zeros((3, 3), dtype=np.float64)
                cell_rows.append(np.repeat(cell[None, :, :], expected, axis=0))
                pbc_rows.append(np.repeat(np.asarray(frame.pbc, dtype=bool)[None, :], expected, axis=0))
            return (
                np.concatenate(position_rows, axis=0) if position_rows else np.zeros((0, 3), dtype=np.float64),
                np.concatenate(cell_rows, axis=0) if cell_rows else np.zeros((0, 3, 3), dtype=np.float64),
                np.concatenate(pbc_rows, axis=0) if pbc_rows else np.zeros((0, 3), dtype=bool),
            )
        except Exception:  # geometry is optional metadata; preserve descriptor analysis if unavailable
            return None, None, None

    def _sample_properties(self, run_row: dict, frames: np.ndarray, rows: np.ndarray | None, mode: str) -> dict[str, np.ndarray]:
        """Load only the physical targets requested by property analysis."""
        if self.datasets is None:
            return {}
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset is None:
            return {}
        try:
            adapter = self.datasets._adapter_for(dataset)
            frame_cache = {int(index): adapter.get_frame(int(index)) for index in np.unique(frames).tolist()}
        except Exception:
            return {}

        names = ("energy", "energy_per_atom", "force_max", "force_magnitude", "volume")
        values = {name: np.full(frames.size, np.nan, dtype=np.float64) for name in names}
        for sample_index, frame_index in enumerate(frames.tolist()):
            frame = frame_cache.get(int(frame_index))
            if frame is None:
                continue
            natoms = max(int(len(frame.numbers)), 1)
            if frame.energy is not None:
                values["energy"][sample_index] = float(frame.energy)
                values["energy_per_atom"][sample_index] = float(frame.energy) / natoms
            if frame.forces is not None and len(frame.forces):
                magnitudes = np.linalg.norm(np.asarray(frame.forces, dtype=np.float64), axis=1)
                values["force_max"][sample_index] = float(magnitudes.max())
                if mode == "atom" and rows is not None:
                    atom = int(rows[sample_index])
                    if 0 <= atom < magnitudes.size:
                        values["force_magnitude"][sample_index] = float(magnitudes[atom])
            cell = np.asarray(frame.cell, dtype=np.float64)
            if cell.shape == (3, 3):
                volume = abs(float(np.linalg.det(cell)))
                if np.isfinite(volume) and volume > 0:
                    values["volume"][sample_index] = volume
        return {name: array for name, array in values.items() if bool(np.isfinite(array).any())}

    def _result_metadata(self, row: dict) -> dict:
        try:
            root = self._result_root(row)
            metadata = root / "metadata.json"
            ensure_no_reparse_points(metadata)
            return json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, UnsafePathError):
            return {}

    def _build_preview(self, result: dict, samples: SampleMatrix, analysis_type: str, reference_samples: SampleMatrix | None = None) -> dict:
        arrays = result.get("arrays", {})
        preview = dict(result.get("preview") or {})

        def sample_identity(index: int) -> dict | None:
            if index < 0 or index >= samples.n_samples:
                return None
            item = {"i": index, "frame": int(samples.frame[index]), "sample_id": samples.sample_ids[index]}
            if samples.row is not None:
                item["row"] = int(samples.row[index])
            if samples.elements is not None and index < len(samples.elements):
                item["element"] = int(samples.elements[index])
            return item

        def reference_identity(index: int) -> dict | None:
            if reference_samples is None or index < 0 or index >= reference_samples.n_samples:
                return None
            item = {
                "reference_i": index,
                "reference_frame": int(reference_samples.frame[index]),
                "reference_sample_id": reference_samples.sample_ids[index],
            }
            if reference_samples.row is not None:
                item["reference_row"] = int(reference_samples.row[index])
            return item

        if "coords" in arrays:
            coords = np.asarray(arrays["coords"])
            count = min(coords.shape[0], samples.n_samples, _MAX_PREVIEW_POINTS)
            indices = np.linspace(0, coords.shape[0] - 1, count, dtype=np.int64) if coords.shape[0] > count else np.arange(coords.shape[0])
            sample_indices = np.asarray(arrays.get("sample_indices", []), dtype=np.int64)
            points = []
            for i in indices.tolist():
                logical_index = int(sample_indices[i]) if sample_indices.ndim == 1 and i < sample_indices.size else int(i)
                point = sample_identity(logical_index)
                if point is None:
                    continue
                point.update({"x": float(coords[i, 0]), "y": float(coords[i, 1])})
                for key in ("labels", "scores", "distances", "cluster_labels", "elements", "coordination", "novelty", "uncertainty", "diversity"):
                    if key in arrays and np.asarray(arrays[key]).ndim == 1 and i < len(arrays[key]):
                        output_key = "label" if key == "labels" else "cluster" if key == "cluster_labels" else "element" if key == "elements" else key
                        point[output_key] = int(arrays[key][i]) if key in ("labels", "cluster_labels", "elements", "coordination") else float(arrays[key][i])
                points.append(point)
            preview["points"] = points
            preview["total_points"] = int(coords.shape[0])
            row_keys = [key for key in ("labels", "scores", "distances", "cluster_labels", "elements", "coordination", "novelty", "uncertainty", "diversity") if key in arrays and np.asarray(arrays[key]).ndim == 1]
            if row_keys:
                rows = []
                for i in range(min(coords.shape[0], _MAX_PREVIEW_POINTS)):
                    logical_index = int(sample_indices[i]) if sample_indices.ndim == 1 and i < sample_indices.size else int(i)
                    item = sample_identity(logical_index)
                    if item is None:
                        continue
                    for key in row_keys:
                        if i >= len(arrays[key]):
                            continue
                        value = np.asarray(arrays[key])[i]
                        output_key = "labels" if key == "labels" else "cluster_labels" if key == "cluster_labels" else "element" if key == "elements" else key
                        item[output_key] = int(value) if key in ("labels", "cluster_labels", "elements", "coordination") else float(value)
                    rows.append(item)
                preview["rows"] = rows
                preview["total_rows"] = int(coords.shape[0])
            if "selected_indices" in arrays:
                selected = np.asarray(arrays["selected_indices"], dtype=np.int64)
                preview["selected"] = [item for i in selected[:_MAX_PREVIEW_POINTS].tolist() if (item := sample_identity(int(i))) is not None]
        elif analysis_type == "pairwise" and "sample_indices" in arrays:
            matrix_indices = np.asarray(arrays["sample_indices"], dtype=np.int64)
            preview["matrix_samples"] = [item for i in matrix_indices[:_MAX_PREVIEW_POINTS].tolist() if (item := sample_identity(int(i))) is not None]
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
        elif "labels" in arrays or "scores" in arrays or "distances" in arrays or "coordination" in arrays or "uncertainty" in arrays:
            lengths = [len(np.asarray(arrays[key])) for key in ("labels", "scores", "distances", "elements", "coordination", "novelty", "uncertainty", "diversity") if key in arrays and np.asarray(arrays[key]).ndim == 1]
            n = min([samples.n_samples, *lengths]) if lengths else samples.n_samples
            count = min(n, _MAX_PREVIEW_POINTS)
            rows = []
            for i in range(count):
                item = sample_identity(i)
                if item is None:
                    continue
                for key in ("labels", "scores", "distances", "elements", "coordination", "novelty", "uncertainty", "diversity"):
                    if key in arrays and i < len(arrays[key]):
                        value = np.asarray(arrays[key])[i]
                        if np.asarray(value).ndim == 0:
                            output_key = "element" if key == "elements" else key
                            item[output_key] = int(value) if key in ("labels", "elements", "coordination") else float(value)
                        else:
                            item[key] = self._json_safe(value)
                if "nearest_indices" in arrays and i < len(arrays["nearest_indices"]):
                    nearest = reference_identity(int(np.asarray(arrays["nearest_indices"])[i]))
                    if nearest:
                        item.update(nearest)
                rows.append(item)
            preview["rows"] = rows
            preview["total_rows"] = n
        return self._json_safe(preview)

    def _commit_artifact(self, analysis_id: str, analysis_type: str, input_ids: list[str], params: dict, result: dict, preview: dict, ctx) -> tuple[Path, dict]:
        import numpy as np

        root = self.data_dir / "analysis"
        root.mkdir(parents=True, exist_ok=True)
        ensure_no_reparse_points(root)
        staging = root / f".{analysis_id}.tmp-{uuid.uuid4().hex[:8]}"
        final = root / analysis_id
        staging.mkdir(parents=True, exist_ok=False)
        ensure_no_reparse_points(staging)
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
                if not isinstance(name, str) or not name:
                    raise AppError(ARTIFACT_INVALID, "analysis artifact contains an invalid array name")
                normalized_name = unicodedata.normalize("NFKC", name)
                safe_name = (
                    normalized_name
                    if normalized_name not in (".", "..")
                    and len(normalized_name) <= 64
                    and not normalized_name.endswith((".", " "))
                    and not _RESERVED_ARTIFACT_NAME_RE.fullmatch(normalized_name)
                    and _ARTIFACT_NAME_RE.fullmatch(normalized_name)
                    else f"array-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:16]}"
                )
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
            with open_text_for_write(staging / "metadata.json") as fh:
                json.dump(self._json_safe(metadata), fh, ensure_ascii=False, indent=2)
            manifest["files"]["metadata"] = {"path": "metadata.json", "bytes": (staging / "metadata.json").stat().st_size}
            manifest["completed"] = True
            # The manifest describes the payload and metadata. It is not
            # listed inside itself, which keeps the byte metadata stable.
            with open_text_for_write(staging / "manifest.json") as fh:
                json.dump(self._json_safe(manifest), fh, ensure_ascii=False, indent=2)
            ensure_no_reparse_points(final)
            if final.exists() or final.is_symlink():
                raise AppError(ARTIFACT_INVALID, f"analysis artifact already exists: {final}")
            os.replace(staging, final)
            return final, manifest
        except Exception:
            self._rmtree_quiet(staging)
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
        import numpy as np
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

    def _managed_artifact_path(self, analysis_id: str, stored: object) -> Path:
        if not _ANALYSIS_ID_RE.fullmatch(analysis_id or ""):
            raise UnsafePathError("invalid analysis id")
        return validate_managed_path(self.data_dir / "analysis", stored, analysis_id)

    @staticmethod
    def _artifact_file(root: Path, raw_name: object) -> Path | None:
        if (
            not isinstance(raw_name, str)
            or raw_name in (".", "..")
            or raw_name.endswith((".", " "))
            or _RESERVED_ARTIFACT_NAME_RE.fullmatch(raw_name)
            or not _ARTIFACT_NAME_RE.fullmatch(raw_name)
        ):
            return None
        candidate = root / raw_name
        try:
            ensure_no_reparse_points(candidate)
        except UnsafePathError:
            return None
        return candidate

    def _artifact_is_complete(self, row: dict) -> bool:
        path = row.get("result_path")
        if not path:
            return False
        try:
            root = self._managed_artifact_path(str(row.get("id") or ""), path)
        except (TypeError, ValueError, UnsafePathError):
            return False
        manifest_path = self._artifact_file(root, "manifest.json")
        if manifest_path is None or not manifest_path.is_file():
            # legacy PCA is valid through its compatibility artifact
            target = self._artifact_file(root, "pca.json")
            return bool(target and target.is_file())
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("completed") is not True:
                return False
            files = manifest.get("files")
            if not isinstance(files, dict) or not files:
                return False
            for file_meta in files.values():
                if not isinstance(file_meta, dict):
                    return False
                target = self._artifact_file(root, file_meta.get("path"))
                if target is None or not target.is_file():
                    return False
                expected_bytes = file_meta.get("bytes")
                if isinstance(expected_bytes, int) and expected_bytes != target.stat().st_size:
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

    @staticmethod
    def _analysis_cache_key(analysis_type: str, input_ids: list[str], params: dict) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "analysis_type": analysis_type,
                    "inputs": input_ids,
                    "params": params,
                    "algorithm_version": _ALGORITHM_VERSION,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _rows_from_artifact(self, row: dict, offset: int, limit: int) -> list[dict]:
        manifest = self._json_load(row.get("artifact_manifest_json"), {})
        files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
        arrays = {}
        try:
            root = self._managed_artifact_path(str(row.get("id") or ""), row.get("result_path"))
        except (TypeError, ValueError, UnsafePathError):
            return []
        for name in ("coords", "indices", "labels", "scores", "distances", "similarity", "selected_indices"):
            meta = files.get(name)
            if isinstance(meta, dict):
                try:
                    target = self._artifact_file(root, meta.get("path"))
                    if target is None:
                        continue
                    arrays[name] = np.load(target, mmap_mode="r", allow_pickle=False)
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
            " (SELECT id FROM descriptor_runs WHERE dataset_id = ?) OR dataset_ids_json LIKE ? ESCAPE '!')",
            (reason, now, now, dataset_id, f'%"{escape_like(dataset_id)}"%'),
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

    def _rmtree_quiet(self, path: Path | None) -> None:
        if not path:
            return
        try:
            ensure_no_reparse_points(path)
            root = self.data_dir / "analysis"
            if Path(path).parent != root:
                log.warning("refusing to remove an unmanaged analysis path")
                return
            remove_managed_tree(path)
        except (OSError, UnsafePathError):
            log.warning("could not remove managed analysis artifact", exc_info=True)
