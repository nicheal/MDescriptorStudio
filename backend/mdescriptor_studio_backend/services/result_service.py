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
