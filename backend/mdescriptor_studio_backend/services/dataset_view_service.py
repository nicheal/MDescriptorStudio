"""DatasetViewService: immutable frame-selection views over a registered dataset.

Split out of DatasetService (design doc Epic-001): view CRUD, ratio splitting and
materialization are a separate concern from dataset lifecycle, but they resolve
datasets through it, so they take the owning service as their only dependency.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import numpy as np

from ..datasets import detect_format
from ..errors import (
    AppError,
    DATASET_CHANGED,
    DATASET_NOT_FOUND,
    INVALID_DATASET,
    INVALID_PARAMS,
)
from ..security import UnsafePathError, validate_local_path
from .dataset_service import _NOW, _frame_indices

_VIEW_ROLES = ("train", "validation", "test", "selection", "filtered")
_INSERT_VIEW = (
    "INSERT INTO dataset_views (id, dataset_id, name, role, filter_json, frame_indices_json,"
    " selection_hash, dataset_fingerprint, created_at, updated_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


class DatasetViewService:
    def __init__(self, datasets):
        self.datasets = datasets
        self.db = datasets.db
        self.jobs = datasets.jobs

    # -- view records --------------------------------------------------------
    def _row(self, view_id: object) -> dict:
        row = self.db.query_one("SELECT * FROM dataset_views WHERE id = ?", (view_id,))
        if row is None:
            raise AppError(DATASET_NOT_FOUND, f"dataset view {view_id} does not exist")
        return row

    def _meta(self, row: dict) -> dict:
        dataset = self.datasets._row(row["dataset_id"])
        dataset_meta = self.datasets._meta(dataset)
        indices = json.loads(row["frame_indices_json"])
        return {
            "id": row["id"],
            "dataset_id": row["dataset_id"],
            "dataset_name": dataset["name"],
            "name": row["name"],
            "role": row.get("role"),
            "filter": json.loads(row["filter_json"]),
            "frame_indices": indices,
            "number_of_frames": len(indices),
            "selection_hash": row["selection_hash"],
            "dataset_fingerprint": row["dataset_fingerprint"],
            "stale": not dataset_meta["cache_valid"] or row["dataset_fingerprint"] != dataset["fingerprint"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _selection_hash(self, fingerprint: str, indices: list[int]) -> str:
        payload = json.dumps(
            {"dataset_fingerprint": fingerprint, "indices": indices}, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _insert(self, dataset: dict, name: str, role, filter_spec: dict, indices: list[int]) -> str:
        """Validate one view's fields and insert it, returning the new id."""
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            raise AppError(INVALID_PARAMS, "dataset view name is invalid")
        if role is not None and role not in _VIEW_ROLES:
            raise AppError(INVALID_PARAMS, "dataset view role is invalid")
        if not isinstance(filter_spec, dict):
            raise AppError(INVALID_PARAMS, "dataset view filter must be an object")
        try:
            filter_json = json.dumps(filter_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise AppError(INVALID_PARAMS, "dataset view filter must be JSON serializable") from exc
        now = _NOW()
        view_id = f"view_{uuid.uuid4().hex[:12]}"
        try:
            self.db.execute(
                _INSERT_VIEW,
                (
                    view_id,
                    dataset["id"],
                    name.strip(),
                    role,
                    filter_json,
                    json.dumps(indices, separators=(",", ":")),
                    self._selection_hash(dataset["fingerprint"], indices),
                    dataset["fingerprint"],
                    now,
                    now,
                ),
            )
        except Exception as exc:
            if "UNIQUE constraint failed: dataset_views.dataset_id, dataset_views.name" in str(exc):
                raise AppError(INVALID_PARAMS, "a dataset view with this name already exists") from exc
            raise
        return view_id

    # -- RPC ---------------------------------------------------------------
    def list(self, params: dict) -> list[dict]:
        dataset_id = params.get("dataset_id")
        if dataset_id:
            self.datasets._row(dataset_id)
            rows = self.db.query(
                "SELECT * FROM dataset_views WHERE dataset_id = ? ORDER BY created_at",
                (dataset_id,),
            )
        else:
            rows = self.db.query("SELECT * FROM dataset_views ORDER BY created_at")
        return [self._meta(row) for row in rows]

    def create(self, params: dict) -> dict:
        dataset = self.datasets._row(params.get("dataset_id"))
        if not self.datasets._meta(dataset)["cache_valid"]:
            raise AppError(DATASET_CHANGED, "dataset must be current before creating a view")
        indices = sorted(_frame_indices(params.get("indices"), dataset["number_of_frames"]))
        view_id = self._insert(
            dataset,
            params.get("name"),
            params.get("role"),
            params.get("filter") or {"type": "explicit_indices"},
            indices,
        )
        return self._meta(self._row(view_id))

    def rename(self, params: dict) -> dict:
        row = self._row(params.get("id"))
        name = params.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            raise AppError(INVALID_PARAMS, "dataset view name is invalid")
        try:
            self.db.execute(
                "UPDATE dataset_views SET name = ?, updated_at = ? WHERE id = ?",
                (name.strip(), _NOW(), row["id"]),
            )
        except Exception as exc:
            if "UNIQUE constraint failed: dataset_views.dataset_id, dataset_views.name" in str(exc):
                raise AppError(INVALID_PARAMS, "a dataset view with this name already exists") from exc
            raise
        return self._meta(self._row(row["id"]))

    def remove(self, params: dict) -> dict:
        row = self._row(params.get("id"))
        self.db.execute("DELETE FROM dataset_views WHERE id = ?", (row["id"],))
        return {"ok": True}

    def split(self, params: dict) -> dict:
        """Deterministic train/validation/test split, optionally of one source view."""
        dataset = self.datasets._row(params.get("dataset_id"))
        if not self.datasets._meta(dataset)["cache_valid"]:
            raise AppError(DATASET_CHANGED, "dataset must be current before creating a split")
        source_view_id = params.get("view_id")
        if source_view_id:
            source = self._row(source_view_id)
            if source["dataset_id"] != dataset["id"]:
                raise AppError(INVALID_PARAMS, "source view does not belong to the dataset")
            if source["dataset_fingerprint"] != dataset["fingerprint"]:
                raise AppError(DATASET_CHANGED, "source view is stale")
            indices = np.asarray(json.loads(source["frame_indices_json"]), dtype=np.int64)
            source_name = source["name"]
        else:
            indices = np.arange(dataset["number_of_frames"], dtype=np.int64)
            source_name = dataset["name"]
        if indices.size < 3:
            raise AppError(INVALID_PARAMS, "a train/validation/test split needs at least three frames")
        try:
            train_ratio = float(params.get("train_ratio", 0.8))
            validation_ratio = float(params.get("validation_ratio", 0.1))
            seed = int(params.get("seed", 42))
        except (TypeError, ValueError) as exc:
            raise AppError(INVALID_PARAMS, "split ratios and seed must be numeric") from exc
        test_ratio = 1.0 - train_ratio - validation_ratio
        if (
            not np.isfinite([train_ratio, validation_ratio, test_ratio]).all()
            or min(train_ratio, validation_ratio, test_ratio) <= 0
        ):
            raise AppError(INVALID_PARAMS, "train, validation, and test ratios must all be positive and sum to 1")
        prefix = params.get("name_prefix") or source_name
        if not isinstance(prefix, str) or not prefix.strip() or len(prefix.strip()) > 160:
            raise AppError(INVALID_PARAMS, "split name prefix is invalid")
        names = [f"{prefix.strip()} / Train", f"{prefix.strip()} / Validation", f"{prefix.strip()} / Test"]
        placeholders = ",".join("?" for _ in names)
        if self.db.query_one(
            f"SELECT id FROM dataset_views WHERE dataset_id = ? AND name IN ({placeholders}) LIMIT 1",
            (dataset["id"], *names),
        ):
            raise AppError(INVALID_PARAMS, "one or more split view names already exist")

        shuffled = indices.copy()
        np.random.default_rng(seed).shuffle(shuffled)
        n = shuffled.size
        train_end = max(1, min(n - 2, int(round(n * train_ratio))))
        validation_end = max(train_end + 1, min(n - 1, train_end + int(round(n * validation_ratio))))
        groups = (shuffled[:train_end], shuffled[train_end:validation_end], shuffled[validation_end:])
        filter_spec = {
            "type": "split",
            "seed": seed,
            "train_ratio": train_ratio,
            "validation_ratio": validation_ratio,
            "source_view_id": source_view_id,
        }
        created = []
        for name, role, group in zip(names, ("train", "validation", "test"), groups):
            created.append(
                self._insert(
                    dataset,
                    name,
                    role,
                    filter_spec,
                    [int(value) for value in np.sort(group).tolist()],
                )
            )
        return {"views": [self._meta(self._row(view_id)) for view_id in created]}

    # -- materialization -----------------------------------------------------
    def _export_destination(self, row: dict, params: dict, verb: str, extxyz_msg: str):
        """Validate a materialized-view destination and choose its writer."""
        raw_dest = params.get("dest_path")
        if not raw_dest or not isinstance(raw_dest, str):
            raise AppError(INVALID_PARAMS, "'dest_path' (string) is required")
        try:
            dest = validate_local_path(raw_dest, field=f"{verb} destination path")
        except UnsafePathError as exc:
            raise AppError(INVALID_PARAMS, f"{verb} destination must be an absolute local path") from exc
        if dest.exists():
            raise AppError(INVALID_DATASET, f"{verb} destination already exists: {dest}")
        source = validate_local_path(row["source_path"], field="dataset source path")
        if dest == source or source in dest.parents or dest in source.parents:
            raise AppError(INVALID_DATASET, f"{verb} destination must be outside the source dataset path")
        fmt = detect_format(source) if source.exists() else row["format"]
        if fmt == "extxyz":
            if dest.suffix.lower() not in (".xyz", ".extxyz"):
                raise AppError(INVALID_DATASET, extxyz_msg)
            from ..datasets.exporters import write_extxyz as writer
        else:
            from ..datasets.exporters import write_deepmd as writer
        return dest, fmt, writer

    def materialize(self, params: dict) -> dict:
        view = self._row(params.get("view_id"))
        dataset = self.datasets._row(view["dataset_id"])
        if view["dataset_fingerprint"] != dataset["fingerprint"] or not self.datasets._meta(dataset)["cache_valid"]:
            raise AppError(DATASET_CHANGED, "dataset view is stale")
        dest, fmt, writer = self._export_destination(
            dataset, params, "materialize",
            "materializing an extxyz view needs a .xyz / .extxyz destination",
        )
        indices = [int(value) for value in json.loads(view["frame_indices_json"])]

        def runner(ctx):
            adapter = self.datasets._adapter_for(dataset)

            def frames():
                for position, frame_index in enumerate(indices, 1):
                    if position % 250 == 0 or position == len(indices):
                        ctx.progress(position, max(len(indices), 1), "writing view frames")
                    yield adapter.get_frame(frame_index)

            written = writer(dest, frames())
            ctx.progress(max(len(indices), 1), max(len(indices), 1), "done")
            return {
                "path": str(dest),
                "frames_written": written,
                "format": fmt,
                "name": view["name"],
                "lineage": {
                    "parent_dataset_id": dataset["id"],
                    "source_view_id": view["id"],
                    "operation": "materialize_view",
                    "selection_hash": view["selection_hash"],
                },
            }

        job_id = self.jobs.submit("dataset.view.materialize", runner, dataset_id=dataset["id"])
        return {"job_id": job_id, "dest_path": str(dest)}
