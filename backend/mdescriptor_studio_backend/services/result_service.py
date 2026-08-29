"""ResultService: run history and stored-result access (no big arrays over IPC)."""

from __future__ import annotations

import json
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
        return self.db.query(sql, tuple(args))

    def get(self, params: dict) -> dict:
        run_id = params.get("run_id")
        row = self.db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (run_id,))
        if row is None:
            raise AppError(INVALID_PARAMS, f"run {run_id} does not exist")
        if row["status"] != "COMPLETED" or not row["result_path"]:
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
        frame_index = params.get("frame_index", 0)
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
        if frame_index < 0 or frame_index >= n_struct:
            raise AppError(INVALID_PARAMS, f"frame index out of range: {frame_index}")
        lo, hi = int(offsets[frame_index]), int(offsets[frame_index + 1])
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
