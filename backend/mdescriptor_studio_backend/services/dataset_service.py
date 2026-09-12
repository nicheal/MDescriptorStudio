"""DatasetService: registry, scan+statistics job, cached statistics, frames."""

from __future__ import annotations

import json
import hashlib
from collections import Counter
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..datasets import (
    compute_fingerprint,
    compute_legacy_fingerprint,
    compute_statistics,
    create_adapter,
    detect_format,
    is_v2_fingerprint,
)
from ..datasets.statistics import _frame_geometry
from ..errors import (
    AppError,
    DATASET_CHANGED,
    DATASET_NOT_FOUND,
    INVALID_DATASET,
    INVALID_PARAMS,
)
from ..mdescriptor_adapter import EngineAdapter
from ..security import UnsafePathError, escape_like, same_lexical_path, validate_local_path
from ..storage.database import Database
from .job_service import JobService

log = logging.getLogger(__name__)

_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731
DEFAULT_BOND_CUTOFF = 2.4
MIN_BOND_CUTOFF = 0.1
MAX_BOND_CUTOFF = 10.0
# max per-frame summary rows one dataset.findings call returns (the drawer
# pages through longer lists)
FINDINGS_ROW_LIMIT = 1000


def _frame_indices(value: object, number_of_frames: int) -> list[int]:
    """Validate an IPC list of frame indices (dedup, range-checked)."""
    if not isinstance(value, list) or not value:
        raise AppError(INVALID_PARAMS, "'indices' must be a non-empty list of frame indices")
    out: list[int] = []
    seen: set[int] = set()
    for v in value:
        if not isinstance(v, int) or isinstance(v, bool):
            raise AppError(INVALID_PARAMS, "'indices' must contain integers")
        if not 0 <= v < number_of_frames:
            raise AppError(
                INVALID_PARAMS,
                f"frame index {v} out of range (dataset has {number_of_frames} frames)",
            )
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _symbol(z: int) -> str:
    from ..datasets.deepmd_symbols import _Z_TO_SYMBOL

    return _Z_TO_SYMBOL.get(int(z), f"Z{z}")


class _CountingAdapter:
    """Forwarding adapter view whose iter_frames runs through a closure that
    tracks progress (register and the statistics recompute job)."""

    def __init__(self, adapter, iter_frames):
        self.format_name = adapter.format_name
        self.source_path = adapter.source_path
        self._adapter = adapter
        self._iter_frames = iter_frames

    def __len__(self):
        return len(self._adapter)

    def get_frame(self, index):
        return self._adapter.get_frame(index)

    def iter_frames(self):
        return self._iter_frames()


def formula_of(symbols: list[str]) -> str:
    return "".join(
        f"{sym}{n}" for sym, n in sorted(Counter(symbols).items(), key=lambda kv: (-kv[1], kv[0]))
    )


def _bond_cutoff(value: object) -> float:
    """Validate the optional display-bond cutoff sent by Explore."""
    if value is None:
        return DEFAULT_BOND_CUTOFF
    if isinstance(value, bool):
        raise AppError(INVALID_PARAMS, "'bond_cutoff' must be a finite number")
    try:
        cutoff = float(value)
    except (TypeError, ValueError) as exc:
        raise AppError(INVALID_PARAMS, "'bond_cutoff' must be a finite number") from exc
    if not np.isfinite(cutoff) or not MIN_BOND_CUTOFF <= cutoff <= MAX_BOND_CUTOFF:
        raise AppError(
            INVALID_PARAMS,
            f"'bond_cutoff' must be between {MIN_BOND_CUTOFF} and {MAX_BOND_CUTOFF} Å",
        )
    return cutoff


class DatasetService:
    def __init__(self, db: Database, adapter: EngineAdapter, jobs: JobService):
        self.db = db
        self.adapter = adapter
        self.jobs = jobs
        self._adapters: dict[str, object] = {}
        # one in-flight scan per dataset: Overview + health rail both call
        # dataset.statistics on stale caches and must share a single job
        self._scan_lock = threading.Lock()
        self._active_scans: dict[str, str] = {}

    # -- helpers -----------------------------------------------------------
    def _row(self, dataset_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (dataset_id,))
        if row is None:
            raise AppError(DATASET_NOT_FOUND, f"dataset {dataset_id} does not exist")
        return row

    def _meta(self, row: dict, fingerprint_valid: bool | None = None) -> dict:
        try:
            source = validate_local_path(row["source_path"], field="dataset source path")
        except (TypeError, UnsafePathError):
            source = None
        try:
            current = (
                compute_fingerprint(source, row["number_of_frames"])
                if source is not None
                else None
            )
        except (OSError, TypeError, ValueError):
            current = None
        legacy = not is_v2_fingerprint(row.get("fingerprint"))
        if legacy and source is not None:
            try:
                legacy_current = compute_legacy_fingerprint(source, row["number_of_frames"])
            except (OSError, TypeError, ValueError):
                legacy_current = None
            if legacy_current is not None and legacy_current != row["fingerprint"]:
                self._mark_runs_stale(row["id"], "source metadata changed before fingerprint migration")
        elif current is not None and current != row["fingerprint"]:
            # Make the invalidation visible as soon as the registry is read;
            # rescan/recompute later updates the dataset fingerprint but never
            # silently resurrects results calculated from the old bytes.
            self._mark_runs_stale(row["id"], f"source fingerprint changed ({row['fingerprint']} -> {current})")
        lineage = self.db.query_one(
            "SELECT parent_dataset_id, source_view_id, operation, selection_hash, created_at"
            " FROM dataset_lineage WHERE child_dataset_id = ?",
            (row["id"],),
        )
        return {
            "id": row["id"],
            "name": row["name"],
            "format": row["format"],
            "source_path": row["source_path"],
            "number_of_frames": row["number_of_frames"],
            "elements": json.loads(row["elements"]),
            "properties": json.loads(row["properties"]),
            "periodicity": json.loads(row["periodicity"]),
            "fingerprint": row["fingerprint"],
            "file_size": row["file_size"],
            "created_at": row["created_at"],
            "last_scan_at": row["last_scan_at"],
            "cache_valid": False if legacy or current is None else (fingerprint_valid if fingerprint_valid is not None else current == row["fingerprint"]),
            "fingerprint_status": (
                "MIGRATING"
                if legacy
                else ("UNAVAILABLE" if current is None else ("CURRENT" if current == row["fingerprint"] else "STALE"))
            ),
            "lineage": lineage,
        }

    def _adapter_for(self, row: dict):
        # cache keyed by the registry fingerprint: a recompute that converges the
        # fingerprint naturally rebuilds; otherwise entries live for the session
        try:
            path = validate_local_path(row["source_path"], field="dataset source path")
        except UnsafePathError as exc:
            raise AppError(INVALID_DATASET, "dataset source path is not a safe local path") from exc
        cached = self._adapters.get(row["id"])
        cached_source = getattr(cached[1], "source_path", None) if cached is not None else None
        if (
            cached is not None
            and cached[0] == row["fingerprint"]
            and (cached_source is None or same_lexical_path(Path(cached_source), path))
        ):
            return cached[1]
        if not path.exists():
            raise AppError(INVALID_DATASET, "dataset source path is unavailable")
        detected = detect_format(path)
        if detected != row["format"]:
            raise AppError(INVALID_DATASET, "dataset format no longer matches its source")
        adapter = create_adapter(path, detected)
        self._adapters[row["id"]] = (row["fingerprint"], adapter)
        return adapter

    # -- IPC methods -----------------------------------------------------------
    def list(self, params: dict) -> list[dict]:
        rows = self.db.query("SELECT * FROM datasets ORDER BY created_at")
        return [self._meta(r) for r in rows]

    def register(self, params: dict) -> dict:
        raw_path = params.get("path")
        if not raw_path or not isinstance(raw_path, str):
            raise AppError(INVALID_PARAMS, "'path' (string) is required")
        try:
            path = validate_local_path(raw_path, field="dataset path")
        except UnsafePathError as exc:
            raise AppError(INVALID_PARAMS, "dataset path must be an absolute local path") from exc
        if not path.exists():
            raise AppError(INVALID_DATASET, "dataset path is unavailable")
        try:
            # The on-disk layout is authoritative; never let a caller force a
            # parser for another format.
            fmt = detect_format(path)
        except AppError:
            raise
        name = params.get("name") or path.stem or path.name
        if not isinstance(name, str) or not name.strip() or len(name) > 200 or any(ord(ch) < 0x20 for ch in name):
            raise AppError(INVALID_PARAMS, "dataset name is invalid")
        name = name.strip()
        lineage = params.get("lineage")
        if lineage is not None:
            if not isinstance(lineage, dict):
                raise AppError(INVALID_PARAMS, "'lineage' must be an object")
            parent_dataset_id = lineage.get("parent_dataset_id")
            source_view_id = lineage.get("source_view_id")
            operation = lineage.get("operation") or "materialize"
            selection_hash = lineage.get("selection_hash")
            if not isinstance(parent_dataset_id, str) or self.db.query_one(
                "SELECT id FROM datasets WHERE id = ?", (parent_dataset_id,)
            ) is None:
                raise AppError(INVALID_PARAMS, "lineage parent dataset does not exist")
            if not isinstance(operation, str) or not operation.strip() or len(operation) > 80:
                raise AppError(INVALID_PARAMS, "lineage operation is invalid")
            if source_view_id is not None and not isinstance(source_view_id, str):
                raise AppError(INVALID_PARAMS, "lineage source_view_id is invalid")
            if selection_hash is not None and not isinstance(selection_hash, str):
                raise AppError(INVALID_PARAMS, "lineage selection_hash is invalid")
            if source_view_id is not None:
                source_view = self.db.query_one(
                    "SELECT dataset_id, selection_hash FROM dataset_views WHERE id = ?",
                    (source_view_id,),
                )
                if source_view is None or source_view["dataset_id"] != parent_dataset_id:
                    raise AppError(INVALID_PARAMS, "lineage source view does not belong to its parent dataset")
                if selection_hash != source_view["selection_hash"]:
                    raise AppError(INVALID_PARAMS, "lineage selection hash does not match its source view")
        dup = self.db.query_one("SELECT id FROM datasets WHERE source_path = ?", (str(path),))
        if dup:
            raise AppError(INVALID_DATASET, "dataset is already registered", {"dataset_id": dup["id"]})

        def runner(ctx):
            ctx.progress(0, 1, "scanning dataset")
            adapter = create_adapter(path, fmt)
            scan = adapter.scan()
            total = max(scan.number_of_frames, 1)
            # statistics pass drives progress
            frames_seen = 0

            def counting_iter():
                nonlocal frames_seen
                for frame in adapter.iter_frames():
                    frames_seen += 1
                    if frames_seen % 250 == 0 or frames_seen == total:
                        ctx.progress(frames_seen, total, "computing statistics")
                    yield frame

            stats = compute_statistics(_CountingAdapter(adapter, counting_iter))
            # fresh registration has no exclusions; the key keeps the stats
            # payload shape stable for the cache-upgrade checks
            stats["excluded_frames"] = {"count": 0, "indices": []}
            ctx.check_cancelled()
            # Registration establishes the source identity; do not use the
            # short-lived UI fingerprint cache at this commit point.
            fingerprint = compute_fingerprint(path, scan.number_of_frames, use_cache=False)
            ds_id = f"ds_{uuid.uuid4().hex[:12]}"
            if not stats["elements"] and scan.elements:
                elements = scan.elements
            elif scan.elements:
                elements = sorted(set(scan.elements) | {e["symbol"] for e in stats["elements"]})
            else:
                elements = [e["symbol"] for e in stats["elements"]]
            periodicity = scan.periodicity if scan.periodicity["flags"] else stats["periodicity"]
            try:
                self.db.execute(
                    "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements,"
                    " properties, periodicity, fingerprint, file_size, created_at, last_scan_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ds_id,
                        name,
                        fmt,
                        str(path),
                        scan.number_of_frames,
                        json.dumps(elements),
                        json.dumps(stats["properties"]),
                        json.dumps(periodicity),
                        fingerprint,
                        scan.file_size,
                        _NOW(),
                        _NOW(),
                    ),
                )
            except Exception as exc:
                # a concurrent register of the same path loses the source_path
                # UNIQUE race; surface the domain error, not the raw constraint
                if "UNIQUE constraint failed: datasets.source_path" in str(exc):
                    raise AppError(
                        INVALID_DATASET, "dataset is already registered"
                    ) from exc
                raise
            self.db.execute(
                "INSERT INTO dataset_statistics (dataset_id, fingerprint, stats_json, created_at)"
                " VALUES (?, ?, ?, ?)",
                (ds_id, fingerprint, json.dumps(stats), _NOW()),
            )
            if lineage is not None:
                self.db.execute(
                    "INSERT INTO dataset_lineage (child_dataset_id, parent_dataset_id, source_view_id, operation, selection_hash, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        ds_id,
                        lineage["parent_dataset_id"],
                        lineage.get("source_view_id"),
                        str(lineage.get("operation") or "materialize").strip(),
                        lineage.get("selection_hash"),
                        _NOW(),
                    ),
                )
            ctx.progress(total, total, "done")
            return {"dataset_id": ds_id, "number_of_frames": scan.number_of_frames}

        job_id = self.jobs.submit("dataset.register", runner)
        return {"job_id": job_id}

    # -- immutable dataset views ---------------------------------------------
    def _view_row(self, view_id: object) -> dict:
        row = self.db.query_one("SELECT * FROM dataset_views WHERE id = ?", (view_id,))
        if row is None:
            raise AppError(DATASET_NOT_FOUND, f"dataset view {view_id} does not exist")
        return row

    def _view_meta(self, row: dict) -> dict:
        dataset = self._row(row["dataset_id"])
        dataset_meta = self._meta(dataset)
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

    def view_list(self, params: dict) -> list[dict]:
        dataset_id = params.get("dataset_id")
        if dataset_id:
            self._row(dataset_id)
            rows = self.db.query(
                "SELECT * FROM dataset_views WHERE dataset_id = ? ORDER BY created_at",
                (dataset_id,),
            )
        else:
            rows = self.db.query("SELECT * FROM dataset_views ORDER BY created_at")
        return [self._view_meta(row) for row in rows]

    def view_create(self, params: dict) -> dict:
        dataset = self._row(params.get("dataset_id"))
        if not self._meta(dataset)["cache_valid"]:
            raise AppError(DATASET_CHANGED, "dataset must be current before creating a view")
        name = params.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
            raise AppError(INVALID_PARAMS, "dataset view name is invalid")
        role = params.get("role")
        if role is not None and role not in ("train", "validation", "test", "selection", "filtered"):
            raise AppError(INVALID_PARAMS, "dataset view role is invalid")
        indices = _frame_indices(params.get("indices"), dataset["number_of_frames"])
        indices = sorted(indices)
        filter_spec = params.get("filter") or {"type": "explicit_indices"}
        if not isinstance(filter_spec, dict):
            raise AppError(INVALID_PARAMS, "dataset view filter must be an object")
        try:
            filter_json = json.dumps(filter_spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise AppError(INVALID_PARAMS, "dataset view filter must be JSON serializable") from exc
        selection_hash = hashlib.sha256(
            json.dumps(
                {"dataset_fingerprint": dataset["fingerprint"], "indices": indices},
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        now = _NOW()
        view_id = f"view_{uuid.uuid4().hex[:12]}"
        try:
            self.db.execute(
                "INSERT INTO dataset_views (id, dataset_id, name, role, filter_json, frame_indices_json, selection_hash, dataset_fingerprint, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    view_id,
                    dataset["id"],
                    name.strip(),
                    role,
                    filter_json,
                    json.dumps(indices, separators=(",", ":")),
                    selection_hash,
                    dataset["fingerprint"],
                    now,
                    now,
                ),
            )
        except Exception as exc:
            if "UNIQUE constraint failed: dataset_views.dataset_id, dataset_views.name" in str(exc):
                raise AppError(INVALID_PARAMS, "a dataset view with this name already exists") from exc
            raise
        return self._view_meta(self._view_row(view_id))

    def view_rename(self, params: dict) -> dict:
        row = self._view_row(params.get("id"))
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
        return self._view_meta(self._view_row(row["id"]))

    def view_remove(self, params: dict) -> dict:
        row = self._view_row(params.get("id"))
        self.db.execute("DELETE FROM dataset_views WHERE id = ?", (row["id"],))
        return {"ok": True}

    def view_split(self, params: dict) -> dict:
        dataset = self._row(params.get("dataset_id"))
        if not self._meta(dataset)["cache_valid"]:
            raise AppError(DATASET_CHANGED, "dataset must be current before creating a split")
        source_view_id = params.get("view_id")
        if source_view_id:
            source = self._view_row(source_view_id)
            if source["dataset_id"] != dataset["id"]:
                raise AppError(INVALID_PARAMS, "source view does not belong to the dataset")
            if source["dataset_fingerprint"] != dataset["fingerprint"]:
                raise AppError(DATASET_CHANGED, "source view is stale")
            indices = np.asarray(json.loads(source["frame_indices_json"]), dtype=np.int64)
        else:
            indices = np.arange(dataset["number_of_frames"], dtype=np.int64)
        if indices.size < 3:
            raise AppError(INVALID_PARAMS, "a train/validation/test split needs at least three frames")
        try:
            train_ratio = float(params.get("train_ratio", 0.8))
            validation_ratio = float(params.get("validation_ratio", 0.1))
            seed = int(params.get("seed", 42))
        except (TypeError, ValueError) as exc:
            raise AppError(INVALID_PARAMS, "split ratios and seed must be numeric") from exc
        test_ratio = 1.0 - train_ratio - validation_ratio
        if not np.isfinite([train_ratio, validation_ratio, test_ratio]).all() or min(train_ratio, validation_ratio, test_ratio) <= 0:
            raise AppError(INVALID_PARAMS, "train, validation, and test ratios must all be positive and sum to 1")
        prefix = params.get("name_prefix") or (source["name"] if source_view_id else dataset["name"])
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
        groups = [np.sort(shuffled[:train_end]), np.sort(shuffled[train_end:validation_end]), np.sort(shuffled[validation_end:])]
        roles = ("train", "validation", "test")
        now = _NOW()
        records = []
        for name, role, group in zip(names, roles, groups):
            group_list = [int(value) for value in group.tolist()]
            selection_hash = hashlib.sha256(
                json.dumps({"dataset_fingerprint": dataset["fingerprint"], "indices": group_list}, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            records.append(
                (
                    f"view_{uuid.uuid4().hex[:12]}", dataset["id"], name, role,
                    json.dumps({"type": "split", "seed": seed, "train_ratio": train_ratio, "validation_ratio": validation_ratio, "source_view_id": source_view_id}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    json.dumps(group_list, separators=(",", ":")), selection_hash,
                    dataset["fingerprint"], now, now,
                )
            )
        self.db.executemany(
            "INSERT INTO dataset_views (id, dataset_id, name, role, filter_json, frame_indices_json, selection_hash, dataset_fingerprint, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            records,
        )
        return {"views": [self._view_meta(self._view_row(record[0])) for record in records]}

    def _export_destination(self, row: dict, params: dict, verb: str, extxyz_msg: str):
        """Shared destination pipeline for view.materialize and export_cleaned:
        validate/reject dest_path, keep it outside the source, and pick the
        writer from the on-disk format. Returns (dest, fmt, writer)."""
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

    def view_materialize(self, params: dict) -> dict:
        view = self._view_row(params.get("view_id"))
        dataset = self._row(view["dataset_id"])
        if view["dataset_fingerprint"] != dataset["fingerprint"] or not self._meta(dataset)["cache_valid"]:
            raise AppError(DATASET_CHANGED, "dataset view is stale")
        dest, fmt, writer = self._export_destination(
            dataset, params, "materialize",
            "materializing an extxyz view needs a .xyz / .extxyz destination",
        )
        indices = [int(value) for value in json.loads(view["frame_indices_json"])]

        def runner(ctx):
            adapter = self._adapter_for(dataset)

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

    def rename(self, params: dict) -> dict:
        name = params.get("name")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name.strip()) > 200
            or any(ord(ch) < 0x20 for ch in name)
        ):
            raise AppError(INVALID_PARAMS, "dataset name is invalid")
        row = self._row(params.get("id"))
        self.db.execute(
            "UPDATE datasets SET name = ? WHERE id = ?", (name.strip(), row["id"])
        )
        return self._meta(self._row(row["id"]))

    def remove(self, params: dict) -> dict:
        ds_id = params.get("id")
        self._row(ds_id)
        # explicit cleanup: FK covers statistics; runs/jobs have no FK rows
        self.db.execute(
            "DELETE FROM analysis_runs WHERE descriptor_run_id IN"
            " (SELECT id FROM descriptor_runs WHERE dataset_id = ?)",
            (ds_id,),
        )
        self.db.execute("DELETE FROM descriptor_runs WHERE dataset_id = ?", (ds_id,))
        self.db.execute("DELETE FROM jobs WHERE dataset_id = ?", (ds_id,))
        self.db.execute("DELETE FROM datasets WHERE id = ?", (ds_id,))
        self._adapters.pop(ds_id, None)
        return {"ok": True}

    def get(self, params: dict) -> dict:
        row = self._row(params.get("id"))
        meta = self._meta(row)
        stats_row = self.db.query_one(
            "SELECT stats_json FROM dataset_statistics WHERE dataset_id = ?", (row["id"],)
        )
        meta["stats"] = json.loads(stats_row["stats_json"]) if stats_row else None
        return meta

    def statistics(self, params: dict) -> dict:
        row = self._row(params.get("id"))
        cached = self._cached_stats(row)
        if cached is not None:
            return {"recalculating": False, "job_id": None, "stats": cached}
        job_id = self._submit_recompute(row["id"])
        return {"recalculating": True, "job_id": job_id, "stats": None}

    def rescan(self, params: dict) -> dict:
        """Force a full rescan (health panel button), even on a valid cache."""
        row = self._row(params.get("id"))
        return {"job_id": self._submit_recompute(row["id"])}

    def _cached_stats(self, row: dict) -> dict | None:
        try:
            source = validate_local_path(row["source_path"], field="dataset source path")
            # Cache is useful for repeated UI reads, but a cache hit must never
            # decide whether a descriptor/statistics result is still current.
            current = compute_fingerprint(
                source, row["number_of_frames"], use_cache=False
            )
        except (OSError, TypeError, ValueError, UnsafePathError):
            return None
        stats_row = self.db.query_one(
            "SELECT fingerprint, stats_json FROM dataset_statistics WHERE dataset_id = ?",
            (row["id"],),
        )
        if stats_row is None or stats_row["fingerprint"] != current:
            return None
        stats = json.loads(stats_row["stats_json"])
        # The stats payload gained keys across generations; a cache missing any
        # required key is a pre-upgrade shape that one recompute fills in.
        required = {
            "health", "min_distance", "compositions", "formulas",
            "element_atom_counts", "health_findings", "excluded_frames",
        }
        if not isinstance(stats, dict) or not required <= stats.keys():
            return None
        health = stats["health"]
        if not isinstance(health, dict) or not {"nonphysical_structures", "net_force"} <= health.keys():
            return None
        if "duplicate_structures_of" not in (stats.get("health_findings") or {}):
            # per-copy first-occurrence mapping behind the findings table's
            # "duplicate of" column
            return None
        return stats

    # -- health findings & excluded frames ------------------------------------
    def _excluded_set(self, ds_id: str) -> set[int]:
        rows = self.db.query(
            "SELECT frame_index FROM dataset_excluded_frames WHERE dataset_id = ?",
            (ds_id,),
        )
        return {int(r["frame_index"]) for r in rows}

    def exclude(self, params: dict) -> dict:
        """Soft-delete frames: statistics and exports skip them; the source
        file is never modified and frames stay previewable/restorable."""
        row = self._row(params.get("id"))
        indices = _frame_indices(params.get("indices"), row["number_of_frames"])
        if not indices:
            raise AppError(INVALID_PARAMS, "'indices' must contain at least one frame index")
        reason = params.get("reason")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 200):
            raise AppError(INVALID_PARAMS, "'reason' must be a short string")
        now = _NOW()
        self.db.executemany(
            "INSERT INTO dataset_excluded_frames (dataset_id, frame_index, reason, created_at)"
            " VALUES (?, ?, ?, ?) ON CONFLICT(dataset_id, frame_index) DO NOTHING",
            [(row["id"], i, reason or "health", now) for i in indices],
        )
        # statistics/histograms describe the effective (non-excluded) dataset
        return {"excluded": len(indices), "job_id": self._submit_recompute(row["id"])}

    def restore(self, params: dict) -> dict:
        row = self._row(params.get("id"))
        indices = _frame_indices(params.get("indices"), row["number_of_frames"])
        if not indices:
            raise AppError(INVALID_PARAMS, "'indices' must contain at least one frame index")
        marks = ",".join("?" for _ in indices)
        self.db.execute(
            f"DELETE FROM dataset_excluded_frames WHERE dataset_id = ? AND frame_index IN ({marks})",
            (row["id"], *indices),
        )
        return {"restored": len(indices), "job_id": self._submit_recompute(row["id"])}

    def excluded(self, params: dict) -> dict:
        row = self._row(params.get("id"))
        return {
            "indices": sorted(self._excluded_set(row["id"])),
            "number_of_frames": row["number_of_frames"],
        }

    def findings(self, params: dict) -> dict:
        """Per-frame summary rows behind one health check (or explicit indices).

        Rows carry the original file index, so the UI can preview any flagged
        frame with dataset.frame and soft-delete it with dataset.exclude.
        """
        row = self._row(params.get("id"))
        check = params.get("check")
        known = {
            "missing_values",
            "invalid_cell",
            "duplicate_structures",
            "extreme_force",
            "nonphysical_structures",
            "net_force",
        }
        if check is not None and check not in known:
            raise AppError(INVALID_PARAMS, f"'check' must be one of {sorted(known)}")
        cached = self._cached_stats(row)
        if cached is None:
            return {"recalculating": True, "job_id": self._submit_recompute(row["id"]), "rows": [], "total": 0, "returned": 0}
        excluded = self._excluded_set(row["id"])
        # declared properties (carried by at least one frame) per the cached
        # stats — the same set the missing-values check watches
        props_meta = cached.get("properties") or {}
        declared_props = [
            name
            for name, flag in (
                ("energy", (props_meta.get("energy") or {}).get("per_structure")),
                ("forces", (props_meta.get("forces") or {}).get("per_atom")),
                ("virial", (props_meta.get("virial") or {}).get("per_structure")),
            )
            if flag
        ]
        if check is not None:
            indices = list(cached.get("health_findings", {}).get(check) or [])
        else:
            params_indices = params.get("indices")
            if params_indices is None:
                raise AppError(INVALID_PARAMS, "'check' or 'indices' is required")
            indices = _frame_indices(params_indices, row["number_of_frames"])
        limit = params.get("limit", FINDINGS_ROW_LIMIT)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= FINDINGS_ROW_LIMIT:
            raise AppError(INVALID_PARAMS, f"'limit' must be an integer in [1, {FINDINGS_ROW_LIMIT}]")
        adapter = self._adapter_for(row)
        # the shortest interatomic distance only pays for itself on the
        # non-physical tab (the check's metric); other tabs skip the NN pass
        want_min_distance = check == "nonphysical_structures"
        rows = []
        for idx in indices[:limit]:
            try:
                f = adapter.get_frame(idx)
            except Exception:  # noqa: BLE001 - unreadable frame: skip the row
                continue
            symbols = [_symbol(z) for z in f.numbers.tolist()]
            force_max = None
            if f.forces is not None and f.forces.size:
                mags = np.linalg.norm(np.asarray(f.forces, dtype=np.float64), axis=1)
                force_max = round(float(mags.max()), 5)
            cell = np.asarray(f.cell, dtype=np.float64)
            det = abs(float(np.linalg.det(cell))) if np.isfinite(cell).all() else 0.0
            present = {
                "energy": f.energy is not None,
                "forces": f.forces is not None,
                "virial": f.virial is not None,
            }
            min_distance = None
            if want_min_distance:
                min_d, _ = _frame_geometry(f.positions, f.numbers, cell, f.pbc)
                if min_d is not None:
                    min_distance = round(float(min_d), 5)
            rows.append(
                {
                    "index": idx,
                    "natoms": len(symbols),
                    "formula": formula_of(symbols),
                    "force_max": force_max,
                    "volume": round(det, 4) if det > 1e-8 else None,
                    "min_distance": min_distance,
                    "missing_props": [name for name in declared_props if not present[name]],
                    "excluded": idx in excluded,
                }
            )
        return {
            "recalculating": False,
            "total": len(indices),
            "returned": len(rows),
            "rows": rows,
        }

    def export_cleaned(self, params: dict) -> dict:
        """Write a new dataset file/dir that skips excluded frames (the source
        is never modified); the UI registers the result via dataset.register."""
        row = self._row(params.get("id"))
        dest, fmt, writer = self._export_destination(
            row, params, "export",
            "exporting an extxyz dataset needs a .xyz / .extxyz destination",
        )

        def runner(ctx):
            excluded = self._excluded_set(row["id"])
            adapter = self._adapter_for(row)
            total = max(len(adapter), 1)
            count = 0

            def frames():
                nonlocal count
                for i in range(len(adapter)):
                    if i in excluded:
                        continue
                    count += 1
                    if count % 250 == 0:
                        ctx.progress(count, total, "writing frames")
                    yield adapter.get_frame(i)

            # the writer consumes lazily, so huge datasets never buffer fully
            written = writer(dest, frames())
            ctx.progress(total, total, "done")
            return {"path": str(dest), "frames_written": written, "format": fmt}

        job_id = self.jobs.submit("dataset.export", runner, dataset_id=row["id"])
        return {"job_id": job_id, "dest_path": str(dest)}

    def _submit_recompute(self, ds_id: str) -> str:
        with self._scan_lock:
            active = self._active_scans.get(ds_id)
            if active is not None:
                return active
            row = self._row(ds_id)
            # the on-disk files may have changed since registration: never reuse the
            # adapter built against the old content
            self._adapters.pop(ds_id, None)

            def runner(ctx):
                old_fingerprint = row["fingerprint"]
                try:
                    source = validate_local_path(row["source_path"], field="dataset source path")
                except UnsafePathError as exc:
                    raise AppError(INVALID_DATASET, "dataset source path is not a safe local path") from exc
                adapter = self._adapter_for(row)
                excluded = self._excluded_set(ds_id)
                total = max(len(adapter) - len(excluded), 1)
                count = 0

                def counting_iter():
                    nonlocal count
                    for pos, frame in enumerate(adapter.iter_frames()):
                        if pos in excluded:
                            continue
                        count += 1
                        if count % 250 == 0 or count == total:
                            ctx.progress(count, total, "computing statistics")
                        yield frame

                stats = compute_statistics(_CountingAdapter(adapter, counting_iter))
                # exclusions are service state, not file state: the effective
                # dataset is the scan minus the excluded frames
                stats["excluded_frames"] = {
                    "count": len(excluded),
                    "indices": sorted(excluded),
                }
                fingerprint = compute_fingerprint(source, len(adapter), use_cache=False)
                if (
                    not is_v2_fingerprint(old_fingerprint)
                    or fingerprint != old_fingerprint
                ):
                    reason = (
                        "dataset fingerprint upgraded; recompute descriptor results"
                        if not is_v2_fingerprint(old_fingerprint)
                        else "source changed while fingerprint was being refreshed"
                    )
                    self._mark_runs_stale(ds_id, reason)
                self._adapters[ds_id] = (fingerprint, adapter)
                self.db.execute(
                    "INSERT INTO dataset_statistics (dataset_id, fingerprint, stats_json, created_at)"
                    " VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(dataset_id) DO UPDATE SET fingerprint = excluded.fingerprint,"
                    " stats_json = excluded.stats_json, created_at = excluded.created_at",
                    (ds_id, fingerprint, json.dumps(stats), _NOW()),
                )
                self.db.execute(
                    "UPDATE datasets SET number_of_frames = ?, fingerprint = ?, last_scan_at = ? WHERE id = ?",
                    (len(adapter), fingerprint, _NOW(), ds_id),
                )
                return {"dataset_id": ds_id}

            def guarded(ctx):
                try:
                    return runner(ctx)
                finally:
                    with self._scan_lock:
                        self._active_scans.pop(ds_id, None)

            job_id = self.jobs.submit("dataset.statistics", guarded, dataset_id=ds_id)
            self._active_scans[ds_id] = job_id
            return job_id

    def refresh_if_changed(self, row: dict) -> None:
        try:
            source = validate_local_path(row["source_path"], field="dataset source path")
            current = compute_fingerprint(source, row["number_of_frames"], use_cache=False)
        except (OSError, TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(INVALID_DATASET, "dataset source is unavailable for fingerprint verification") from exc
        if not is_v2_fingerprint(row.get("fingerprint")):
            try:
                legacy_current = compute_legacy_fingerprint(source, row["number_of_frames"])
            except (OSError, TypeError, ValueError) as exc:
                raise AppError(INVALID_DATASET, "dataset fingerprint migration cannot inspect the source") from exc
            if legacy_current != row["fingerprint"]:
                self._mark_runs_stale(row["id"], "source metadata changed before fingerprint migration")
                raise AppError(DATASET_CHANGED, "dataset changed on disk; rescan it before continuing")
            raise AppError(DATASET_CHANGED, "dataset fingerprint migration is in progress; rescan it before continuing")
        if current != row["fingerprint"]:
            self._mark_runs_stale(row["id"], f"source fingerprint changed ({row['fingerprint']} -> {current})")
            raise AppError(
                DATASET_CHANGED,
                f"dataset changed on disk: {row['source_path']}",
                {"dataset_id": row["id"]},
            )

    def _mark_runs_stale(self, dataset_id: str, reason: str) -> None:
        """Invalidate old descriptor/analysis runs without deleting history."""
        now = _NOW()
        self.db.execute(
            "UPDATE descriptor_runs SET status = 'STALE', error_message = ?, finished_at = ?"
            " WHERE dataset_id = ? AND status = 'COMPLETED'",
            (reason, now, dataset_id),
        )
        # The JSON column is available after migration 3; the descriptor_run
        # fallback keeps legacy PCA rows visible and stale as well.
        self.db.execute(
            "UPDATE analysis_runs SET status = 'STALE', stale_reason = ?, finished_at = ?, updated_at = ?"
            " WHERE status = 'COMPLETED' AND (descriptor_run_id IN"
            " (SELECT id FROM descriptor_runs WHERE dataset_id = ?) OR dataset_ids_json LIKE ? ESCAPE '!')",
            (reason, now, now, dataset_id, f'%"{escape_like(dataset_id)}"%'),
        )

    # -- frame access (M2) --------------------------------------------------
    def frame(self, params: dict) -> dict:
        ds_id, index = params.get("id"), params.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise AppError(INVALID_PARAMS, "'index' (int) is required")
        bond_cutoff = _bond_cutoff(params.get("bond_cutoff"))
        row = self._row(ds_id)
        adapter = self._adapter_for(row)
        try:
            f = adapter.get_frame(index)
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001 - parse/data errors are dataset faults
            raise AppError(INVALID_DATASET, f"cannot read frame {index}: {exc}") from exc
        symbols = [_symbol(z) for z in f.numbers.tolist()]
        positions = np.asarray(f.positions)
        rows = []
        for i, (s, pos) in enumerate(zip(symbols, positions)):
            entry = {
                "i": i,
                "el": s,
                "x": round(float(pos[0]), 5),
                "y": round(float(pos[1]), 5),
                "z": round(float(pos[2]), 5),
                "fx": None,
                "fy": None,
                "fz": None,
                "f": None,
            }
            if f.forces is not None:
                fx, fy, fz = (float(v) for v in f.forces[i])
                entry.update(
                    fx=round(fx, 5), fy=round(fy, 5), fz=round(fz, 5),
                    f=round((fx * fx + fy * fy + fz * fz) ** 0.5, 5),
                )
            rows.append(entry)
        cell = np.asarray(f.cell)
        det = abs(float(np.linalg.det(cell)))
        periodic = bool(np.all(np.asarray(f.pbc)) and det > 1e-8)
        volume = det if det > 1e-8 else None
        header = f"frame {index} of dataset {row['name']}"
        if periodic:
            lat = " ".join(f"{v:.6f}" for v in cell.reshape(-1))
            header += f' Lattice="{lat}"'
        # periodic images appended to the SAME model so cross-boundary bonds form
        ghosts = periodic_boundary_ghosts(symbols, positions, cell, cutoff=bond_cutoff) if periodic else []
        display_symbols = symbols + [g[0] for g in ghosts]
        display_positions = (
            np.vstack([positions, np.array([g[1] for g in ghosts])]) if ghosts else positions
        )
        if ghosts:
            header += f" +{len(ghosts)} periodic images"
        xyz_lines = [str(len(display_symbols)), header]
        for s, pos in zip(display_symbols, display_positions):
            xyz_lines.append(f"{s} {pos[0]:.6f} {pos[1]:.6f} {pos[2]:.6f}")
        energy_per_atom = None
        if f.energy is not None and symbols:
            energy_per_atom = round(float(f.energy) / len(symbols), 6)
        force_max = None
        if f.forces is not None and len(rows):
            vals = [r["f"] for r in rows if r["f"] is not None]
            force_max = round(max(vals), 5) if vals else None
        # row-major 3×3 as stored by the source (extxyz comment / deepmd set);
        # sign conventions differ between ecosystems, so the GUI shows it raw
        virial = None
        if f.virial is not None:
            virial = [round(float(v), 6) for v in np.asarray(f.virial, dtype=np.float64).reshape(-1)]
        return {
            "index": index,
            "natoms": len(symbols),
            "formula": formula_of(symbols),
            "xyz": "\n".join(xyz_lines),
            "atom_rows": rows,
            "energy": f.energy,
            "energy_per_atom": energy_per_atom,
            "force_max": force_max,
            "virial_present": f.virial is not None,
            "virial": virial,
            "volume": round(volume, 4) if volume else None,
            "pbc": "".join("XYZ"[i] for i, v in enumerate(f.pbc) if v) or "—",
            "cell": cell.reshape(-1).tolist() if periodic else None,
            "ghost_count": len(ghosts),
            "ghost_parents": [g[2] for g in ghosts],
            "bond_cutoff": bond_cutoff,
        }


def periodic_boundary_ghosts(
    symbols: list[str], positions: np.ndarray, cell: np.ndarray,
    cutoff: float = DEFAULT_BOND_CUTOFF, max_ghosts: int = 3000,
) -> list[tuple[str, np.ndarray, int]]:
    """Periodic-image atoms that complete bonds cut by the cell boundary.

    An atom whose *wrapped* fractional position lies within `cutoff` of a
    cell face contributes candidate images at the lattice shifts required by
    that cutoff (not only ±1); a candidate is kept only when it lands within
    `cutoff` of a displayed atom (and does not coincide with one), so only
    bond-completing images survive. The distance check is what keeps unwrapped
    frames (deepmd sets are often centered on the origin, with negative
    coordinates) from spraying stray atoms outside the structure. Returns
    (element, position, parent_index) triples — parent_index is the real atom
    the image mirrors — capped at max_ghosts.
    """
    pos = np.asarray(positions, dtype=np.float64)
    if len(symbols) == 0:
        return []
    try:
        a_inv = np.linalg.inv(cell)
    except np.linalg.LinAlgError:
        return []  # singular cell: no invertible lattice, so no well-defined images
    frac = pos @ a_inv
    frac_w = frac - np.floor(frac)  # face test needs in-cell fraction
    spacing = 1.0 / np.linalg.norm(a_inv, axis=0)  # interplanar distance per axis
    near_face = (frac_w * spacing < cutoff) | ((1.0 - frac_w) * spacing < cutoff)
    cand = np.nonzero(near_face.any(axis=1))[0]
    if cand.size == 0:
        return []
    # The inverse-cell columns bound the lattice coefficients of any
    # displacement with norm <= cutoff.  The extra one accounts for the
    # wrapped fractional separation between two atoms. This keeps local-shell
    # visualization correct when the cutoff spans more than one unit cell.
    shift_limits = [max(1, int(np.ceil(cutoff * np.linalg.norm(a_inv[:, axis]))) + 1) for axis in range(3)]
    shifts = np.array(
        [
            (dx, dy, dz)
            for dx in range(-shift_limits[0], shift_limits[0] + 1)
            for dy in range(-shift_limits[1], shift_limits[1] + 1)
            for dz in range(-shift_limits[2], shift_limits[2] + 1)
            if (dx, dy, dz) != (0, 0, 0)
        ],
        dtype=np.float64,
    ) @ cell
    pos_sq = (pos * pos).sum(axis=1)
    out: list[tuple[str, np.ndarray, int]] = []
    chunk = max(1, int(4_000_000 // max(len(symbols), 1)))
    for start in range(0, cand.size, chunk):
        idx = cand[start:start + chunk]
        imgs = (pos[idx][:, None, :] + shifts[None, :, :]).reshape(-1, 3)
        d2 = (imgs * imgs).sum(axis=1)[:, None] - 2.0 * (imgs @ pos.T) + pos_sq[None, :]
        d2min = np.maximum(d2.min(axis=1), 0.0)
        # bonded to something on screen, and not exactly standing on an atom
        keep = (d2min <= cutoff * cutoff) & (d2min > 1e-6)
        if not keep.any():
            continue
        src = np.repeat(idx, len(shifts))[keep]
        for i, p in zip(src, imgs[keep]):
            # parent index lets the GUI map a click on an image atom back to
            # the real atom it mirrors (real atoms occupy 0..len(symbols)-1).
            out.append((symbols[i], p, int(i)))
            if len(out) >= max_ghosts:
                return out
    return out
