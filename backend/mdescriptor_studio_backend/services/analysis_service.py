"""AnalysisService: PCA on stored descriptor results (design doc §31).

PCA via numpy SVD (no sklearn dependency); atom/pair-level results are mean
pooled per structure so every point maps to exactly one frame for the
PCA -> Explore reverse jump.
"""

from __future__ import annotations

import json
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

    def pca(self, params: dict) -> dict:
        run_id = params.get("run_id")
        if not run_id:
            raise AppError(INVALID_PARAMS, "'run_id' is required")
        row = self.db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (run_id,))
        if row is None:
            raise AppError(INVALID_PARAMS, f"run {run_id} does not exist")
        analysis_id = f"ana_{uuid.uuid4().hex[:12]}"
        self.db.execute(
            "INSERT INTO analysis_runs (id, descriptor_run_id, analysis_type, params_json, status, created_at)"
            " VALUES (?, ?, 'pca', ?, 'QUEUED', ?)",
            (analysis_id, run_id, json.dumps({k: v for k, v in params.items() if k != 'run_id'}), _NOW()),
        )

        def runner(ctx):
            ctx.progress(0, 1, "loading values")
            values, run_row = self.results.load_values(run_id)
            ctx.check_cancelled()
            pooled = self._pool_per_structure(values, run_row)
            coords, explained = self._pca(pooled)
            ctx.progress(0.7, 1, "assembling points")
            frame_props = self._frame_properties(run_row, pooled.shape[0])
            out_dir = self.data_dir / "analysis" / analysis_id
            out_dir.mkdir(parents=True, exist_ok=True)
            np.save(out_dir / "coords.npy", coords)
            payload = {
                "analysis_id": analysis_id,
                "run_id": run_id,
                "n_points": int(coords.shape[0]),
                "points": [
                    {
                        "i": i,
                        "frame": i,
                        "pc1": round(float(coords[i, 0]), 4),
                        "pc2": round(float(coords[i, 1]), 4),
                        **frame_props[i],
                    }
                    for i in range(coords.shape[0])
                ],
                "explained_variance": [round(float(v), 6) for v in explained[:2]],
                "x_label": f"PC1 ({explained[0] * 100:.1f}%)",
                "y_label": f"PC2 ({explained[1] * 100:.1f}%)",
            }
            (out_dir / "pca.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self.db.execute(
                "UPDATE analysis_runs SET status = 'COMPLETED', result_path = ?, finished_at = ? WHERE id = ?",
                (str(out_dir), _NOW(), analysis_id),
            )
            ctx.progress(1, 1, "done")
            return {"analysis_id": analysis_id, "n_points": payload["n_points"]}

        job_id = self.jobs.submit("analysis.pca", runner, dataset_id=row["dataset_id"])
        return {"job_id": job_id, "analysis_id": analysis_id}

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
        props: list[dict] = [{"energy": None, "force_max": None, "volume": None} for _ in range(n_points)]
        dataset_row = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset_row is None:
            return props
        adapter = self.datasets._adapter_for(dataset_row)
        frame_scope = run_row["scope"] == "frame"
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
