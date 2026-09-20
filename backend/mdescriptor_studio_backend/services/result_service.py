"""ResultService: run history and stored-result access (no big arrays over IPC)."""

from __future__ import annotations

import json
import hashlib
import logging
from pathlib import Path

from ..errors import AppError, INVALID_PARAMS, RESULT_INCOMPATIBLE
from ..security import (
    UnsafePathError,
    json_membership,
    ensure_no_reparse_points,
    remove_managed_tree,
    validate_managed_path,
)
from .analysis_helpers import MANAGED_ID_RE, _MAX_CHUNK_VALUES

log = logging.getLogger(__name__)


class ResultService:
    def __init__(self, db, data_dir: Path | None = None):
        self.db = db
        self.data_dir = Path(data_dir or db.path.parent).resolve(strict=False)

    def sweep_abandoned(self) -> None:
        """Reclaim directories no row can ever point at again.

        A run that failed while writing leaves result_path NULL, so neither
        result.remove nor dataset.remove can reach the directory it created, and
        a hard exit mid-commit leaves an analysis staging directory behind. Both
        are unreachable by construction, so startup is the only safe moment to
        delete them: no job is running yet and no live row references them.
        """
        for table, root in (("descriptor_runs", "results"), ("analysis_runs", "analysis")):
            for row in self.db.query(
                f"SELECT id FROM {table} WHERE result_path IS NULL AND status IN ('FAILED', 'CANCELLED')"
            ):
                remove_managed_tree(self.data_dir / root / row["id"])
        staging_root = self.data_dir / "analysis"
        if staging_root.is_dir():
            for staging in staging_root.glob(".*.tmp-*"):
                remove_managed_tree(staging)

    def managed_result_path(self, run_id: str, stored: object) -> Path:
        if not MANAGED_ID_RE.fullmatch(run_id or "") or not str(run_id).startswith("run_"):
            raise UnsafePathError("invalid descriptor run id")
        return validate_managed_path(self.data_dir / "results", stored, run_id)

    def _managed_analysis_path(self, analysis_id: str, stored: object) -> Path:
        if not MANAGED_ID_RE.fullmatch(analysis_id or "") or not str(analysis_id).startswith("ana_"):
            raise UnsafePathError("invalid analysis id")
        return validate_managed_path(self.data_dir / "analysis", stored, analysis_id)

    def _managed_result_file(self, run_id: str, stored: object, name: str) -> Path:
        if name not in {"metadata.json", "values.npy", "row_offsets.npy", "pca.json"}:
            raise UnsafePathError("invalid descriptor artifact file")
        root = self.managed_result_path(run_id, stored)
        path = root / name
        ensure_no_reparse_points(path)
        if not path.is_file():
            raise OSError(f"descriptor artifact file is missing: {name}")
        return path

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
        # rowid breaks created_at ties: the timestamp only has second resolution
        # (`analysis_helpers._NOW`), so a batch of runs submitted inside one
        # second otherwise ordered arbitrarily and could reshuffle between
        # refreshes. Same rule job_service.list_jobs and analysis.list follow.
        sql += " ORDER BY r.created_at DESC, r.rowid DESC LIMIT 500"
        rows = self.db.query(sql, tuple(args))
        for row in rows:
            # Keep the list response small: expose only the computed array
            # shape, never the descriptor values themselves. A completed run
            # carries that (plus feature count and row semantics) in columns, so
            # a 500-row page no longer costs 500 file reads and JSON parses;
            # only runs predating that migration still read metadata.json.
            metadata = self._listed_metadata(row)
            row["shape"] = row.get("result_shape_json") or self._shape_text(metadata)
            row["feature_space_signature"] = self._feature_space_signature(row, metadata)
            row["feature_count"] = row.get("feature_count") or (
                metadata.get("feature_count") if metadata else None
            )
            row["row_semantics"] = row.get("row_semantics") or (
                metadata.get("row_semantics") if metadata else None
            )
            row.pop("result_shape_json", None)
        return rows

    def _listed_metadata(self, row: dict) -> dict:
        """Metadata for one listed run, read only when the columns cannot answer."""
        if row.get("result_shape_json") is not None and row.get("row_semantics") is not None:
            return {}
        return self._read_result_metadata(row["id"], row.get("result_path"))

    def _read_result_metadata(self, run_id: str, result_path: str | None) -> dict:
        if not result_path:
            return {}
        try:
            metadata = json.loads(
                self._managed_result_file(run_id, result_path, "metadata.json")
                .read_text(encoding="utf-8")
            )
        except (OSError, TypeError, ValueError, UnsafePathError):
            # Pending/legacy runs may not have result metadata yet; they still
            # belong in the run history with an empty shape.
            return {}
        return metadata if isinstance(metadata, dict) else {}

    @staticmethod
    def _shape_text(metadata: dict) -> str | None:
        shape = metadata.get("shape") if isinstance(metadata, dict) else None
        if isinstance(shape, str):
            return shape
        if isinstance(shape, list):
            return json.dumps(shape, ensure_ascii=False)
        return None

    @staticmethod
    def _feature_space_signature(row: dict, metadata: dict) -> str | None:
        if row.get("status") not in ("COMPLETED", "STALE"):
            return None
        if not metadata and row.get("result_shape_json") is None:
            return None
        try:
            parameters = json.loads(row.get("parameters_json") or "{}")
        except (TypeError, ValueError):
            parameters = row.get("parameters_json")
        # Columns first, metadata second: the hash must not change depending on
        # which of the two a row's values came from.
        feature_count = row.get("feature_count")
        if feature_count is None:
            feature_count = metadata.get("feature_count")
        if feature_count is None:
            shape = metadata.get("shape")
            if isinstance(shape, list) and len(shape) >= 2:
                feature_count = shape[-1]
        row_semantics = row.get("row_semantics")
        if row_semantics is None:
            row_semantics = metadata.get("row_semantics") or metadata.get("level")
        payload = {
            "descriptor": row.get("descriptor_name"),
            "descriptor_version": row.get("descriptor_version"),
            "engine_version": row.get("engine_version"),
            "parameters": parameters,
            "row_semantics": row_semantics,
            "feature_count": feature_count,
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def feature_space_signature(self, run_id: str) -> tuple[str | None, dict]:
        row = self.get({"run_id": run_id})
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        return self._feature_space_signature(row, metadata), metadata

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
        try:
            meta = json.loads(
                self._managed_result_file(str(run_id), row["result_path"], "metadata.json")
                .read_text(encoding="utf-8")
            )
        except (OSError, TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(RESULT_INCOMPATIBLE, "descriptor result artifact is unavailable") from exc
        dataset = self.db.query_one("SELECT name FROM datasets WHERE id = ?", (row["dataset_id"],))
        return {**row, "dataset_name": dataset["name"] if dataset else None, "metadata": meta}

    def load_values(self, run_id: str, *, mmap: bool = False):
        """Internal helper for analysis (never serialized to IPC).

        `mmap=True` returns a read-only memory map for callers that only slice
        a few rows (heatmap per frame) instead of loading the whole matrix on
        an RPC worker per request.
        """
        import numpy as np

        row = self.get({"run_id": run_id})
        try:
            values_path = self._managed_result_file(str(run_id), row["result_path"], "values.npy")
        except (OSError, TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(RESULT_INCOMPATIBLE, "descriptor result values are unavailable") from exc
        values = np.load(values_path, mmap_mode="r" if mmap else None, allow_pickle=False)
        return values, {**row, "result_path": str(values_path.parent)}

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

        membership, pattern = json_membership("input_run_ids_json", run_id)
        analyses = self.db.query(
            "SELECT id, result_path FROM analysis_runs"
            f" WHERE descriptor_run_id = ? OR {membership}",
            (run_id, pattern),
        )
        analysis_ids = {str(analysis["id"]) for analysis in analyses}
        active_jobs = self.db.query(
            "SELECT descriptor_run_id, analysis_run_id FROM jobs WHERE status IN ('QUEUED', 'RUNNING')"
        )
        if any(
            job.get("descriptor_run_id") == run_id or job.get("analysis_run_id") in analysis_ids
            for job in active_jobs
        ):
            raise AppError(RESULT_INCOMPATIBLE, f"run {run_id} has active jobs — cancel them first")
        try:
            result_path = self.managed_result_path(str(run_id), row["result_path"]) if row["result_path"] else None
            analysis_paths = [
                self._managed_analysis_path(str(ana["id"]), ana["result_path"])
                for ana in analyses
                if ana["result_path"]
            ]
        except (TypeError, ValueError, UnsafePathError) as exc:
            # Validate every target before mutating the database. A poisoned
            # result_path therefore cannot turn result.remove into arbitrary
            # directory deletion.
            raise AppError(RESULT_INCOMPATIBLE, "stored result paths are invalid") from exc
        # linked jobs first: they reference runs/analyses being deleted below
        job_conditions = ["descriptor_run_id = ?"]
        job_args: list[object] = [run_id]
        if analysis_ids:
            placeholders = ", ".join("?" for _ in analysis_ids)
            job_conditions.append(f"analysis_run_id IN ({placeholders})")
            job_args.extend(sorted(analysis_ids))
        with self.db.transaction() as conn:
            conn.execute(f"DELETE FROM jobs WHERE {' OR '.join(job_conditions)}", tuple(job_args))
            if analysis_ids:
                placeholders = ", ".join("?" for _ in analysis_ids)
                conn.execute(
                    f"DELETE FROM analysis_runs WHERE id IN ({placeholders})",
                    tuple(sorted(analysis_ids)),
                )
            conn.execute("DELETE FROM descriptor_runs WHERE id = ?", (run_id,))

        # disk cleanup is best-effort: the DB rows are the source of truth, and
        # dataset.remove already tolerates orphaned dirs on disk
        self._rmtree_quiet(result_path)
        for path in analysis_paths:
            self._rmtree_quiet(path)
        return {"ok": True}

    def _rmtree_quiet(self, path: Path | None) -> None:
        if path:
            try:
                remove_managed_tree(path)
            except (OSError, UnsafePathError):
                log.warning("could not remove managed artifact", exc_info=True)

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
        try:
            root = self._managed_analysis_path(str(analysis_id), row["result_path"])
            target = root / "pca.json"
            ensure_no_reparse_points(target)
            return json.loads(target.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(RESULT_INCOMPATIBLE, "analysis artifact is unavailable") from exc

    def heatmap(self, params: dict) -> dict:
        """Atom-level values for ONE structure: N_atoms x min(features, 256)."""
        import numpy as np

        run_id = params.get("run_id")
        frame_index = params.get("frame_index")
        values, row = self.load_values(run_id, mmap=True)
        try:
            offsets_file = self._managed_result_file(
                str(run_id), row["result_path"], "row_offsets.npy"
            )
        except (OSError, TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(RESULT_INCOMPATIBLE, "descriptor result artifact is unavailable") from exc
        if values.ndim != 2:
            raise AppError(
                RESULT_INCOMPATIBLE,
                "heatmap requires an atom/pair-level run with row_offsets",
            )
        offsets = np.load(offsets_file, allow_pickle=False)
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
        lo, hi = int(offsets[structure_index]), int(offsets[structure_index + 1])
        block = values[lo:hi]
        try:
            max_features = int(params.get("max_features", 256))
        except (TypeError, ValueError):
            max_features = 256
        max_features = max(1, min(max_features, 256))
        block = block[:, :max_features]
        # The product is what has to be bounded: columns are capped above, and
        # rows are the one structure's atoms, which a 1e6-atom frame happily
        # exceeds. Without this the reply is refused by Server._encode and the
        # caller gets an error frame instead of a page; with it the reply is
        # always sendable, and `truncated` says atoms are missing.
        allowed_atoms = max(1, _MAX_CHUNK_VALUES // block.shape[1])
        truncated = int(block.shape[0]) > allowed_atoms
        block = block[:allowed_atoms]
        return {
            "atomOffset": lo,
            "atoms": list(range(lo, lo + int(block.shape[0]))),
            "features": list(range(block.shape[1])),
            "values": [[round(float(v), 6) for v in r] for r in block.tolist()],
            "truncated": truncated,
        }
