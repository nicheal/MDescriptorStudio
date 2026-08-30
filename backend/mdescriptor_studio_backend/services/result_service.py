"""ResultService: run history and stored-result access (no big arrays over IPC)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..errors import AppError, INVALID_PARAMS, RESULT_INCOMPATIBLE


class ResultService:
    def __init__(self, db):
        self.db = db

    def list(self, params: dict) -> list[dict]:
        sql = (
            "SELECT r.*, d.name AS dataset_name FROM descriptor_runs r"
            " JOIN datasets d ON d.id = r.dataset_id"
        )
        cond, args = [], []
        if params.get("dataset_id"):
            cond.append("r.dataset_id = ?")
            args.append(params["dataset_id"])
        if params.get("descriptor_name"):
            cond.append("r.descriptor_name = ?")
            args.append(params["descriptor_name"])
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY r.created_at DESC LIMIT 500"
        rows = self.db.query(sql, tuple(args))
        for row in rows:
            # Keep the list response small: expose only the computed array
            # shape from metadata, never the descriptor values themselves.
            row["shape"] = self._read_result_shape(row.get("result_path"))
        return rows

    @staticmethod
    def _read_result_shape(result_path: str | None) -> str | None:
        if not result_path:
            return None
        try:
            metadata = json.loads(
                (Path(result_path) / "metadata.json").read_text(encoding="utf-8")
            )
        except (OSError, TypeError, ValueError):
            # Pending/legacy runs may not have result metadata yet; they still
            # belong in the run history with an empty shape.
            return None
        shape = metadata.get("shape") if isinstance(metadata, dict) else None
        if isinstance(shape, str):
            return shape
        if isinstance(shape, list):
            return json.dumps(shape, ensure_ascii=False)
        return None

    def get(self, params: dict) -> dict:
        run_id = params.get("run_id")
        row = self.db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (run_id,))
        if row is None:
            raise AppError(INVALID_PARAMS, f"run {run_id} does not exist")
        # STALE runs remain readable for audit/reproducibility.  AnalysisService
        # separately rejects them as new inputs, so historical artifacts cannot
        # accidentally participate in a fresh calculation.
        if row["status"] not in ("COMPLETED", "STALE") or not row["result_path"]:
            raise AppError(RESULT_INCOMPATIBLE, f"run {run_id} is {row['status']}")
        meta = json.loads((Path(row["result_path"]) / "metadata.json").read_text(encoding="utf-8"))
        dataset = self.db.query_one("SELECT name FROM datasets WHERE id = ?", (row["dataset_id"],))
        return {**row, "dataset_name": dataset["name"] if dataset else None, "metadata": meta}

    def load_values(self, run_id: str):
        """Internal helper for analysis (never serialized to IPC)."""
        import numpy as np

        row = self.get({"run_id": run_id})
        path = Path(row["result_path"])
        return np.load(path / "values.npy"), row

    def remove(self, params: dict) -> dict:
        """Delete ONE run: DB rows (runs, analyses, linked jobs) + result dirs."""
        run_id = params.get("run_id")
        row = self.db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (run_id,))
        if row is None:
            raise AppError(INVALID_PARAMS, f"run {run_id} does not exist")
        if row["status"] in ("QUEUED", "RUNNING"):
            raise AppError(
                RESULT_INCOMPATIBLE,
                f"run {run_id} is {row['status']} — cancel its job first",
            )

        analyses = self.db.query(
            "SELECT id, result_path FROM analysis_runs WHERE descriptor_run_id = ?", (run_id,)
        )
        # linked jobs first: they reference runs/analyses being deleted below
        self.db.execute(
            "DELETE FROM jobs WHERE descriptor_run_id = ? OR analysis_run_id IN"
            " (SELECT id FROM analysis_runs WHERE descriptor_run_id = ?)",
            (run_id, run_id),
        )
        self.db.execute("DELETE FROM analysis_runs WHERE descriptor_run_id = ?", (run_id,))
        self.db.execute("DELETE FROM descriptor_runs WHERE id = ?", (run_id,))

        # disk cleanup is best-effort: the DB rows are the source of truth, and
        # dataset.remove already tolerates orphaned dirs on disk
        self._rmtree_quiet(row["result_path"])
        for ana in analyses:
            self._rmtree_quiet(ana["result_path"])
        return {"ok": True}

    @staticmethod
    def _rmtree_quiet(path: str | None) -> None:
        if path:
            shutil.rmtree(path, ignore_errors=True)

    # -- M5 helpers ------------------------------------------------------------
    def get_pca(self, params: dict) -> dict:
        analysis_id = params.get("analysis_id")
        if not analysis_id:
            raise AppError(INVALID_PARAMS, "'analysis_id' is required")
        row = self.db.query_one(
            "SELECT result_path FROM analysis_runs WHERE id = ? AND analysis_type = 'pca'",
            (analysis_id,),
        )
        if row is None or not row["result_path"]:
            raise AppError(INVALID_PARAMS, f"analysis {analysis_id} does not exist")
        return json.loads((Path(row["result_path"]) / "pca.json").read_text(encoding="utf-8"))

    def heatmap(self, params: dict) -> dict:
        """Atom-level values for ONE structure: N_atoms x min(features, 256)."""
        import numpy as np

        run_id = params.get("run_id")
        frame_index = params.get("frame_index")
        values, row = self.load_values(run_id)
        path = Path(row["result_path"])
        offsets_file = path / "row_offsets.npy"
        if not offsets_file.exists() or values.ndim != 2:
            raise AppError(
                RESULT_INCOMPATIBLE,
                "heatmap requires an atom/pair-level run with row_offsets",
            )
        offsets = np.load(offsets_file)
        n_struct = offsets.size - 1
        try:
            requested_frame = None if frame_index is None else int(frame_index)
        except (TypeError, ValueError) as exc:
            raise AppError(INVALID_PARAMS, f"frame index must be an integer: {frame_index!r}") from exc
        if row.get("scope") == "frame":
            actual_frame = int(row.get("frame_index") or 0)
            requested_frame = actual_frame if requested_frame is None else requested_frame
            if requested_frame != actual_frame:
                raise AppError(INVALID_PARAMS, f"frame index out of range: {requested_frame}")
            structure_index = 0
        else:
            structure_index = 0 if requested_frame is None else requested_frame
            if structure_index < 0 or structure_index >= n_struct:
                raise AppError(INVALID_PARAMS, f"frame index out of range: {structure_index}")
        if structure_index < 0 or structure_index >= n_struct:
            raise AppError(INVALID_PARAMS, f"frame index out of range: {structure_index}")
        lo, hi = int(offsets[structure_index]), int(offsets[structure_index + 1])
        block = values[lo:hi]
        try:
            max_features = int(params.get("max_features", 256))
        except (TypeError, ValueError):
            max_features = 256
        # hard cap: never stream the full matrix over IPC (design doc §25)
        max_features = max(1, min(max_features, 256))
        block = block[:, :max_features]
        return {
            "atomOffset": lo,
            "atoms": list(range(lo, hi)),
            "features": list(range(block.shape[1])),
            "values": [[round(float(v), 6) for v in r] for r in block.tolist()],
        }
