"""DatasetService: registry, scan+statistics job, cached statistics, frames."""

from __future__ import annotations

import json
from collections import Counter
import logging
import threading
import uuid
from pathlib import Path

import numpy as np

from ..datasets import (
    compute_fingerprint,
    compute_legacy_fingerprint,
    compute_statistics,
    create_adapter,
    detect_format,
    is_versioned_fingerprint,
)
from ..datasets.fingerprint import FINGERPRINT_VERSION
from ..datasets.statistics import STATS_VERSION, frame_force_max
from ..datasets.deepmd_symbols import _Z_TO_SYMBOL
from ..errors import (
    AppError,
    DATASET_BUSY,
    DATASET_CHANGED,
    DATASET_NOT_FOUND,
    INVALID_DATASET,
    INVALID_PARAMS,
)
from ..mdescriptor_adapter import EngineAdapter
from ..security import (
    UnsafePathError,
    json_membership,
    remove_managed_tree,
    same_lexical_path,
    validate_local_path,
    validate_managed_path,
)
from ..storage.database import Database
from .analysis_helpers import MANAGED_ID_RE, _NOW
from .job_service import JobService

log = logging.getLogger(__name__)

# max per-frame summary rows one dataset.findings call returns; longer lists are
# reported by their exact count in "total" and truncated in "rows" (the drawer
# says so) - there is no offset, so frame 1001 is not reachable today
FINDINGS_ROW_LIMIT = 1000


def symbol_of(z: int) -> str:
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



class DatasetService:
    def __init__(self, db: Database, adapter: EngineAdapter, jobs: JobService, data_dir: Path | None = None):
        self.db = db
        self.adapter = adapter
        self.jobs = jobs
        self.data_dir = Path(data_dir or db.path.parent).resolve(strict=False)
        self._adapters: dict[str, object] = {}
        self._adapter_lock = threading.Lock()
        # one in-flight scan per dataset: Overview + health rail both call
        # dataset.statistics on stale caches and must share a single job
        self._scan_lock = threading.Lock()
        self._active_scans: dict[str, str] = {}

    # -- helpers -----------------------------------------------------------
    def row_or_raise(self, dataset_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (dataset_id,))
        if row is None:
            raise AppError(DATASET_NOT_FOUND, f"dataset {dataset_id} does not exist")
        return row

    def meta(self, row: dict) -> dict:
        try:
            source = validate_local_path(row["source_path"], field="dataset source path")
        except (TypeError, UnsafePathError):
            source = None
        legacy = not is_versioned_fingerprint(row.get("fingerprint"))
        current: str | None = None
        # A pre-versioning row is answered entirely by the legacy branch below,
        # so measuring the source for the versioned fingerprint would be a full
        # directory walk and 32 MB sample that nothing reads.
        if not legacy and source is not None:
            try:
                current = compute_fingerprint(source, row["number_of_frames"])
            except (OSError, TypeError, ValueError):
                current = None
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
            "cache_valid": False if legacy or current is None else current == row["fingerprint"],
            "fingerprint_status": (
                "MIGRATING"
                if legacy
                else ("UNAVAILABLE" if current is None else ("CURRENT" if current == row["fingerprint"] else "STALE"))
            ),
            "lineage": lineage,
        }

    def adapter_for(self, row: dict):
        # cache keyed by the registry fingerprint: a recompute that converges the
        # fingerprint naturally rebuilds; otherwise entries live for the session
        try:
            path = validate_local_path(row["source_path"], field="dataset source path")
        except UnsafePathError as exc:
            raise AppError(INVALID_DATASET, "dataset source path is not a safe local path") from exc
        def reuse(entry):
            # The one rule for a cached adapter worth returning: the fingerprint
            # the row now carries, and a source that is still the same file.
            if entry is None or entry[0] != row["fingerprint"]:
                return None
            cached_source = getattr(entry[1], "source_path", None)
            if cached_source is not None and not same_lexical_path(Path(cached_source), path):
                return None
            return entry[1]

        adapter = reuse(self._adapters.get(row["id"]))
        if adapter is not None:
            return adapter
        if not path.exists():
            raise AppError(INVALID_DATASET, "dataset source path is unavailable")
        detected = detect_format(path)
        if detected != row["format"]:
            raise AppError(INVALID_DATASET, "dataset format no longer matches its source")
        # Built under a lock, and re-checked inside it. A cold dataset can be
        # touched by an analysis job and the viewer in the same instant, and
        # `create_adapter` reads the source in - a DeepMD set eagerly and in
        # full - so without this each caller built its own copy and every loser
        # but the last left memory behind that nothing could reach.
        with self._adapter_lock:
            adapter = reuse(self._adapters.get(row["id"]))
            if adapter is None:
                adapter = create_adapter(path, detected)
                self._adapters[row["id"]] = (row["fingerprint"], adapter)
            return adapter

    # -- IPC methods -----------------------------------------------------------
    def list(self, params: dict) -> list[dict]:
        rows = self.db.query("SELECT * FROM datasets ORDER BY created_at")
        return [self.meta(r) for r in rows]

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
                        ctx.check_cancelled()
                        ctx.progress(frames_seen, total, "computing statistics")
                    yield frame

            stats = compute_statistics(_CountingAdapter(adapter, counting_iter))
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
                (ds_id, fingerprint, json.dumps(stats, allow_nan=False), _NOW()),
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

    def rename(self, params: dict) -> dict:
        name = params.get("name")
        if (
            not isinstance(name, str)
            or not name.strip()
            or len(name.strip()) > 200
            or any(ord(ch) < 0x20 for ch in name)
        ):
            raise AppError(INVALID_PARAMS, "dataset name is invalid")
        row = self.row_or_raise(params.get("id"))
        self.db.execute(
            "UPDATE datasets SET name = ? WHERE id = ?", (name.strip(), row["id"])
        )
        return self.meta(self.row_or_raise(row["id"]))

    def remove(self, params: dict) -> dict:
        ds_id = params.get("id")
        self.row_or_raise(ds_id)
        runs = self.db.query(
            "SELECT id, result_path FROM descriptor_runs WHERE dataset_id = ?", (ds_id,)
        )
        run_ids = {str(run["id"]) for run in runs}
        analyses = []
        for analysis in self.db.query(
            "SELECT id, descriptor_run_id, input_run_ids_json, dataset_ids_json, result_path FROM analysis_runs"
        ):
            input_ids = self._json_list(analysis.get("input_run_ids_json"))
            dataset_ids = self._json_list(analysis.get("dataset_ids_json"))
            if (
                analysis.get("descriptor_run_id") in run_ids
                or run_ids.intersection(input_ids)
                or ds_id in dataset_ids
            ):
                analyses.append(analysis)
        analysis_ids = {str(analysis["id"]) for analysis in analyses}

        for job in self.db.query(
            "SELECT dataset_id, descriptor_run_id, analysis_run_id FROM jobs"
            " WHERE status IN ('QUEUED', 'RUNNING')"
        ):
            if (
                job.get("dataset_id") == ds_id
                or job.get("descriptor_run_id") in run_ids
                or job.get("analysis_run_id") in analysis_ids
            ):
                raise AppError(DATASET_BUSY, f"dataset {ds_id} has active jobs")

        try:
            artifact_paths = [
                self._managed_artifact_path("results", str(run["id"]), run["result_path"])
                for run in runs
                if run.get("result_path")
            ] + [
                self._managed_artifact_path("analysis", str(analysis["id"]), analysis["result_path"])
                for analysis in analyses
                if analysis.get("result_path")
            ]
        except (OSError, TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(INVALID_DATASET, "stored dataset artifact paths are invalid") from exc

        run_placeholders = ", ".join("?" for _ in run_ids)
        analysis_placeholders = ", ".join("?" for _ in analysis_ids)
        with self.db.transaction() as conn:
            job_conditions = ["dataset_id = ?"]
            job_args: list[object] = [ds_id]
            if run_ids:
                job_conditions.append(f"descriptor_run_id IN ({run_placeholders})")
                job_args.extend(sorted(run_ids))
            if analysis_ids:
                job_conditions.append(f"analysis_run_id IN ({analysis_placeholders})")
                job_args.extend(sorted(analysis_ids))
            conn.execute(f"DELETE FROM jobs WHERE {' OR '.join(job_conditions)}", tuple(job_args))
            if analysis_ids:
                conn.execute(
                    f"DELETE FROM analysis_runs WHERE id IN ({analysis_placeholders})",
                    tuple(sorted(analysis_ids)),
                )
            conn.execute("DELETE FROM descriptor_runs WHERE dataset_id = ?", (ds_id,))
            conn.execute("DELETE FROM datasets WHERE id = ?", (ds_id,))
        self._adapters.pop(ds_id, None)
        for path in artifact_paths:
            try:
                remove_managed_tree(path)
            except (OSError, UnsafePathError):
                log.warning("could not remove managed dataset artifact", exc_info=True)
        return {"ok": True}

    @staticmethod
    def _json_list(raw: object) -> set[str]:
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return set()
        return {str(item) for item in value} if isinstance(value, list) else set()

    def _managed_artifact_path(self, kind: str, artifact_id: str, stored: object) -> Path:
        if not MANAGED_ID_RE.fullmatch(artifact_id):
            raise UnsafePathError("invalid artifact id")
        return validate_managed_path(self.data_dir / kind, stored, artifact_id)

    def get(self, params: dict) -> dict:
        row = self.row_or_raise(params.get("id"))
        meta = self.meta(row)
        stats_row = self.db.query_one(
            "SELECT stats_json FROM dataset_statistics WHERE dataset_id = ?", (row["id"],)
        )
        meta["stats"] = json.loads(stats_row["stats_json"]) if stats_row else None
        return meta

    def statistics(self, params: dict) -> dict:
        row = self.row_or_raise(params.get("id"))
        cached = self._cached_stats(row)
        if cached is not None:
            return {"recalculating": False, "job_id": None, "stats": cached}
        job_id = self._submit_recompute(row["id"])
        return {"recalculating": True, "job_id": job_id, "stats": None}

    def rescan(self, params: dict) -> dict:
        """Force a full rescan (health panel button), even on a valid cache."""
        row = self.row_or_raise(params.get("id"))
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
            "element_atom_counts", "health_findings",
            "stats_version",
        }
        if not isinstance(stats, dict) or not required <= stats.keys():
            return None
        if stats["stats_version"] != STATS_VERSION:
            return None
        health = stats["health"]
        if not isinstance(health, dict) or not {"energy_anomaly", "nonphysical_structures", "net_force"} <= health.keys():
            return None
        if "duplicate_structures_of" not in (stats.get("health_findings") or {}):
            # per-copy first-occurrence mapping behind the findings table's
            # "duplicate of" column
            return None
        return stats

    # -- health findings -----------------------------------------------------
    def findings(self, params: dict) -> dict:
        """Per-frame summary rows behind one health check.

        Rows carry the original file index, so the UI can preview any flagged
        frame with dataset.frame and save it as a dataset view.
        """
        row = self.row_or_raise(params.get("id"))
        check = params.get("check")
        known = {
            "missing_values",
            "energy_anomaly",
            "invalid_cell",
            "duplicate_structures",
            "extreme_force",
            "nonphysical_structures",
            "net_force",
        }
        if not isinstance(check, str) or check not in known:
            raise AppError(INVALID_PARAMS, f"'check' must be one of {sorted(known)}")
        cached = self._cached_stats(row)
        if cached is None:
            return {"recalculating": True, "job_id": self._submit_recompute(row["id"]), "rows": [], "total": 0, "returned": 0}
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
        indices = list(cached.get("health_findings", {}).get(check) or [])
        # Shortest distances recorded by the scan, parallel to the flagged frame
        # indices below HEALTH_FINDINGS_CAP.
        distances = list((cached.get("health_findings") or {}).get("nonphysical_distances") or [])
        limit = params.get("limit", FINDINGS_ROW_LIMIT)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= FINDINGS_ROW_LIMIT:
            raise AppError(INVALID_PARAMS, f"'limit' must be an integer in [1, {FINDINGS_ROW_LIMIT}]")
        adapter = self.adapter_for(row)
        # the shortest interatomic distance only pays for itself on the
        # non-physical tab (the check's metric); other tabs skip the NN pass
        want_min_distance = check == "nonphysical_structures"
        rows = []
        for position, idx in enumerate(indices[:limit]):
            try:
                f = adapter.get_frame(idx)
            except Exception:  # noqa: BLE001 - unreadable frame: skip the row
                continue
            symbols = [symbol_of(z) for z in f.numbers.tolist()]
            force_max = frame_force_max(f.forces)
            cell = np.asarray(f.cell, dtype=np.float64)
            det = abs(float(np.linalg.det(cell))) if np.isfinite(cell).all() else 0.0
            present = {
                "energy": f.energy is not None,
                "forces": f.forces is not None,
                "virial": f.virial is not None,
            }
            min_distance = (
                round(float(distances[position]), 5)
                if want_min_distance and position < len(distances)
                else None
            )
            energy_per_atom = None
            if f.energy is not None:
                energy_per_atom = round(float(f.energy) / max(len(symbols), 1), 5)
            rows.append(
                {
                    "index": idx,
                    "natoms": len(symbols),
                    "formula": formula_of(symbols),
                    "force_max": force_max,
                    "energy_per_atom": energy_per_atom,
                    "volume": round(det, 4) if det > 1e-8 else None,
                    "min_distance": min_distance,
                    "missing_props": [name for name in declared_props if not present[name]],
                }
            )
        return {
            "recalculating": False,
            # the check's exact count rather than len(indices): the index list
            # behind it is capped at HEALTH_FINDINGS_CAP, so a dataset with
            # 40 000 invalid cells reported 5 000 and the drawer presented that
            # as the whole truth.
            "total": int((cached.get("health") or {}).get(check, len(indices))),
            "returned": len(rows),
            "rows": rows,
        }

    def _submit_recompute(self, ds_id: str) -> str:
        with self._scan_lock:
            active = self._active_scans.get(ds_id)
            if active is not None:
                # The entry is normally cleared by the scan's own finally, but a
                # scan cancelled while still queued never reaches its runner at
                # all (JobService skips it once cancel() dropped the context),
                # so the id can name a job that is already settled. Honouring it
                # would hand the caller a dead job forever: every later
                # statistics/findings/rescan call would wait on a CANCELLED job
                # and the dataset could only recover by restarting the backend.
                job = self.jobs.get_job(active)
                if job is not None and job["status"] in ("QUEUED", "RUNNING"):
                    return active
                self._active_scans.pop(ds_id, None)
            row = self.row_or_raise(ds_id)
            # the on-disk files may have changed since registration: never reuse the
            # adapter built against the old content
            self._adapters.pop(ds_id, None)

            def runner(ctx):
                old_fingerprint = row["fingerprint"]
                try:
                    source = validate_local_path(row["source_path"], field="dataset source path")
                except UnsafePathError as exc:
                    raise AppError(INVALID_DATASET, "dataset source path is not a safe local path") from exc
                adapter = self.adapter_for(row)
                total = max(len(adapter), 1)
                count = 0

                def counting_iter():
                    nonlocal count
                    for pos, frame in enumerate(adapter.iter_frames()):
                        count += 1
                        if count % 250 == 0 or count == total:
                            # Without this the "cancelled" scan still decodes
                            # every frame, holds one of the two dataset slots
                            # and saturates the disk until it finishes.
                            ctx.check_cancelled()
                            ctx.progress(count, total, "computing statistics")
                        yield frame

                stats = compute_statistics(_CountingAdapter(adapter, counting_iter))
                ctx.check_cancelled()  # a cancelled scan commits nothing
                fingerprint = compute_fingerprint(source, len(adapter), use_cache=False)
                upgraded = not is_versioned_fingerprint(old_fingerprint) or (
                    old_fingerprint.partition(":")[0] != FINGERPRINT_VERSION
                )
                if upgraded or fingerprint != old_fingerprint:
                    self._mark_runs_stale(
                        ds_id,
                        "dataset fingerprint upgraded; recompute descriptor results"
                        if upgraded
                        else "source changed while fingerprint was being refreshed",
                    )
                self._adapters[ds_id] = (fingerprint, adapter)
                self.db.execute(
                    "INSERT INTO dataset_statistics (dataset_id, fingerprint, stats_json, created_at)"
                    " VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(dataset_id) DO UPDATE SET fingerprint = excluded.fingerprint,"
                    " stats_json = excluded.stats_json, created_at = excluded.created_at",
                    (ds_id, fingerprint, json.dumps(stats, allow_nan=False), _NOW()),
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

    def refresh_if_changed(self, row: dict) -> str:
        """Verify the stored fingerprint against the source, and return it.

        The caller gets the value it just paid for: walking the source and
        hashing a sampled 32 MB is the whole cost, and a submit that needed the
        fingerprint for its cache key used to measure the source a second time
        in the same request.
        """
        try:
            source = validate_local_path(row["source_path"], field="dataset source path")
            current = compute_fingerprint(source, row["number_of_frames"], use_cache=False)
        except (OSError, TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(INVALID_DATASET, "dataset source is unavailable for fingerprint verification") from exc
        if not is_versioned_fingerprint(row.get("fingerprint")):
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
        return current

    def _mark_runs_stale(self, dataset_id: str, reason: str) -> None:
        """Invalidate old descriptor/analysis runs without deleting history."""
        membership, pattern = json_membership("dataset_ids_json", dataset_id)
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
            f" (SELECT id FROM descriptor_runs WHERE dataset_id = ?) OR {membership})",
            (reason, now, now, dataset_id, pattern),
        )
