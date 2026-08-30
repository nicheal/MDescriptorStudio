"""AnalysisService: PCA on stored descriptor results (design doc §31).

PCA via numpy SVD (no sklearn dependency); atom/pair-level results are mean
pooled per structure so every point maps to exactly one frame for the
PCA -> Explore reverse jump.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..errors import AppError, INVALID_PARAMS
from .result_service import ResultService
from ..storage.database import Database
from .job_service import JobService

_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731


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
        run_id = params.get("run_id")
        if not run_id:
            raise AppError(INVALID_PARAMS, "'run_id' is required")
        mode = params.get("mode") or "structure"
        if mode not in ("structure", "atom"):
            raise AppError(INVALID_PARAMS, f"mode must be 'structure' or 'atom', got {mode!r}")
        row = self.db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (run_id,))
        if row is None:
            raise AppError(INVALID_PARAMS, f"run {run_id} does not exist")
        analysis_params = {"mode": mode}
        params_json = json.dumps(
            analysis_params, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

        with self._pca_submit_lock:
            cached = self._find_cached_pca(run_id, mode)
            if cached is not None:
                return {
                    "job_id": None,
                    "analysis_id": cached["id"],
                    "cache": {"existing_analysis_id": cached["id"]},
                }

            active = self._find_active_pca(run_id, mode)
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
                coords, explained, frames, atoms = self._pca_points(values, run_row, mode)
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
                    "x_label": f"PC1 ({explained[0] * 100:.1f}%)",
                    "y_label": f"PC2 ({explained[1] * 100:.1f}%)",
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

    def _find_cached_pca(self, run_id: str, mode: str) -> dict | None:
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
            if self._pca_params_match(row["params_json"], mode) and self._pca_artifact_exists(row):
                return row
        return None

    def _find_active_pca(self, run_id: str, mode: str) -> dict | None:
        rows = self.db.query(
            "SELECT id, params_json, status FROM analysis_runs"
            " WHERE descriptor_run_id = ? AND analysis_type = 'pca'"
            " AND status IN ('QUEUED', 'RUNNING')"
            " ORDER BY created_at DESC, id DESC",
            (run_id,),
        )
        for row in rows:
            if not self._pca_params_match(row["params_json"], mode):
                continue
            job = self.db.query_one(
                "SELECT id FROM jobs WHERE analysis_run_id = ?"
                " AND status IN ('QUEUED', 'RUNNING') ORDER BY created_at DESC, id DESC LIMIT 1",
                (row["id"],),
            )
            return {**row, "job_id": job["id"] if job else None}
        return None

    @staticmethod
    def _pca_params_match(raw: str | None, mode: str) -> bool:
        try:
            saved = json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            return False
        return saved == {"mode": mode} or (mode == "structure" and saved == {})

    @staticmethod
    def _pca_artifact_exists(row: dict) -> bool:
        result_path = row.get("result_path")
        return bool(result_path and (Path(result_path) / "pca.json").is_file())

    @classmethod
    def _pca_points(cls, values: np.ndarray, run_row: dict, mode: str):
        """Return (coords, explained, frames, atoms|None).

        Structure mode: one point per frame (atom/pair rows mean-pooled).
        Atom mode: one point per atom/pair row, keeping the owning frame and the
        in-frame row index for tooltips/reverse-jump. Very large runs are evenly
        subsampled so the IPC payload and chart stay responsive (design doc §25).
        """
        path = Path(run_row["result_path"])
        offsets_file = path / "row_offsets.npy"
        atom_level = values.ndim == 2 and offsets_file.exists()
        if mode == "structure" or not atom_level:
            pooled = cls._pool_per_structure(values, run_row)
            frames = np.arange(pooled.shape[0])
            atoms = None
        else:
            offsets = np.load(offsets_file)
            if offsets.size <= 2 or int(offsets[-1]) != values.shape[0]:
                pooled = cls._pool_per_structure(values, run_row)
                frames = np.arange(pooled.shape[0])
                atoms = None
            else:
                counts = np.diff(offsets).astype(int)
                frames = np.repeat(np.arange(counts.size), counts)
                atoms = np.arange(values.shape[0]) - offsets[frames]
                pooled = values
                max_points = 20000
                if pooled.shape[0] > max_points:
                    keep = np.unique(np.linspace(0, pooled.shape[0] - 1, max_points).astype(int))
                    pooled, frames, atoms = pooled[keep], frames[keep], atoms[keep]
        coords, explained = cls._pca(pooled)
        return coords, explained, frames, atoms

    @staticmethod
    def _pool_per_structure(values: np.ndarray, run_row: dict) -> np.ndarray:
        path = Path(run_row["result_path"])
        offsets_file = path / "row_offsets.npy"
        if values.ndim == 2 and offsets_file.exists():
            offsets = np.load(offsets_file)
            if offsets.size > 2:  # atom/pair level: one row per atom
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
    def _pca(x: np.ndarray):
        mean = x.mean(axis=0)
        xc = x - mean
        # SVD on up to ~12k x few-hundred matrix is fast and stable
        u, s, vt = np.linalg.svd(xc, full_matrices=False)
        var = (s**2) / max(x.shape[0] - 1, 1)
        total = float(var.sum()) or 1.0
        explained = var / total
        coords = xc @ vt[:2].T
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
