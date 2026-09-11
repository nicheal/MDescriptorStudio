"""ResultService: run history and stored-result access (no big arrays over IPC)."""

from __future__ import annotations

import json
import hashlib
import logging
import re
from pathlib import Path

from ..errors import AppError, INVALID_PARAMS, RESULT_INCOMPATIBLE
from ..security import (
    UnsafePathError,
    ensure_no_reparse_points,
    remove_managed_tree,
    validate_managed_path,
)

log = logging.getLogger(__name__)
_ARTIFACT_ID_RE = re.compile(r"^(?:run|ana)_[A-Za-z0-9_-]{1,64}$")


class ResultService:
    def __init__(self, db, data_dir: Path | None = None):
        self.db = db
        self.data_dir = Path(data_dir or db.path.parent).resolve(strict=False)

    def _managed_result_path(self, run_id: str, stored: object) -> Path:
        if not _ARTIFACT_ID_RE.fullmatch(run_id or "") or not str(run_id).startswith("run_"):
            raise UnsafePathError("invalid descriptor run id")
        return validate_managed_path(self.data_dir / "results", stored, run_id)

    def _managed_analysis_path(self, analysis_id: str, stored: object) -> Path:
        if not _ARTIFACT_ID_RE.fullmatch(analysis_id or "") or not str(analysis_id).startswith("ana_"):
            raise UnsafePathError("invalid analysis id")
        return validate_managed_path(self.data_dir / "analysis", stored, analysis_id)

    def _managed_result_file(self, run_id: str, stored: object, name: str) -> Path:
        if name not in {"metadata.json", "values.npy", "row_offsets.npy", "pca.json"}:
            raise UnsafePathError("invalid descriptor artifact file")
        root = self._managed_result_path(run_id, stored)
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
        sql += " ORDER BY r.created_at DESC LIMIT 500"
        rows = self.db.query(sql, tuple(args))
        for row in rows:
            # Keep the list response small: expose only the computed array
            # shape from metadata, never the descriptor values themselves.
            metadata = self._read_result_metadata(row["id"], row.get("result_path"))
            row["shape"] = self._shape_text(metadata)
            row["feature_space_signature"] = self._feature_space_signature(row, metadata)
            row["feature_count"] = metadata.get("feature_count") if metadata else None
            row["row_semantics"] = metadata.get("row_semantics") if metadata else None
        return rows

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
        if row.get("status") not in ("COMPLETED", "STALE") or not metadata:
            return None
        try:
            parameters = json.loads(row.get("parameters_json") or "{}")
        except (TypeError, ValueError):
            parameters = row.get("parameters_json")
        shape = metadata.get("shape")
        feature_count = metadata.get("feature_count")
        if feature_count is None and isinstance(shape, list) and len(shape) >= 2:
            feature_count = shape[-1]
        payload = {
            "descriptor": row.get("descriptor_name"),
            "descriptor_version": row.get("descriptor_version"),
            "engine_version": row.get("engine_version"),
            "parameters": parameters,
            "row_semantics": metadata.get("row_semantics") or metadata.get("level"),
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

        analyses = self.db.query(
            "SELECT id, result_path FROM analysis_runs WHERE descriptor_run_id = ?", (run_id,)
        )
        try:
            result_path = self._managed_result_path(str(run_id), row["result_path"]) if row["result_path"] else None
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
        self.db.execute(
            "DELETE FROM jobs WHERE descriptor_run_id = ? OR analysis_run_id IN"
            " (SELECT id FROM analysis_runs WHERE descriptor_run_id = ?)",
            (run_id, run_id),
        )
        self.db.execute("DELETE FROM analysis_runs WHERE descriptor_run_id = ?", (run_id,))
        self.db.execute("DELETE FROM descriptor_runs WHERE id = ?", (run_id,))

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
