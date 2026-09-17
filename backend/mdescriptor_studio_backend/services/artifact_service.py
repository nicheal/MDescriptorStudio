"""Analysis service responsibilities split by concern.

The :class:`AnalysisService` facade inherits these mixins; each file owns one
responsibility (data loading, preview shaping, artifacts/export or job
execution) so the request surface stays free of numerical and filesystem
details.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ..analysis import (
    ANALYSIS_REGISTRY,
    AnalysisEngine,
    AtomDescriptorMatrix,
    DescriptorMatrix,
    StructureDescriptorMatrix,
)
from ..analysis.sampling import FeatureBlock, group_sizes, sqrt_quota
from ..errors import (
    ANALYSIS_INPUT_INVALID,
    ANALYSIS_INSUFFICIENT_SAMPLES,
    ANALYSIS_NOT_FOUND,
    ANALYSIS_STALE,
    ARTIFACT_INVALID,
    EXPORT_FAILED,
    AppError,
    INVALID_PARAMS,
    JOB_CANCELLED,
    RESULT_INCOMPATIBLE,
)
from ..security import (
    UnsafePathError,
    ensure_no_reparse_points,
    escape_like,
    open_text_for_write,
    validate_local_path,
)
from .analysis_artifact_store import AnalysisArtifactStore
from .analysis_helpers import (
    ANALYSIS_ALGORITHM_VERSION,
    COMPOSITE_BLOCKS,
    PHYSICAL_BLOCKS,
    _ANALYSIS_SCHEMA_VERSION,
    _MAX_PREVIEW_POINTS,
    _NOW,
    _PREVIEW_ARRAY_KEYS,
    _block_names,
    _cell_parameters,
    _composition_matrix,
    _descriptor_summary,
    _pool_rows,
    _require_finite,
)


class AnalysisArtifactMixin:
    """Managed analysis artifact access, caching keys and JSON helpers."""

    def _commit_artifact(self, analysis_id: str, analysis_type: str, input_ids: list[str], params: dict, result: dict, preview: dict, ctx) -> tuple[Path, dict]:
        return self._artifacts.commit(
            analysis_id,
            analysis_type,
            input_ids,
            params,
            result,
            preview,
            ctx,
            self._json_safe,
        )

    def _write_legacy_pca_compatibility(self, out_dir: Path, analysis_id: str, run_id: str, params: dict, preview: dict) -> None:
        points = []
        for point in preview.get("points", []):
            if not isinstance(point, dict):
                continue
            points.append({
                "i": int(point.get("i", len(points))),
                "frame": int(point.get("frame", 0)),
                **({"atom": int(point["row"])} if point.get("row") is not None else {}),
                "pc1": float(point.get("x", 0.0)),
                "pc2": float(point.get("y", 0.0)),
                "energy_per_atom": point.get("energy_per_atom"),
                "force_max": point.get("force_max"),
                "volume": point.get("volume"),
            })
        payload = {
            "analysis_id": analysis_id,
            "run_id": run_id,
            "mode": params.get("mode", "structure"),
            "preprocess": params.get("preprocess", "center"),
            "n_points": int(preview.get("total_points", len(points))),
            "points": points,
            "explained_variance": list(preview.get("explained_variance", [])),
            "x_label": preview.get("x_label", "PC1"),
            "y_label": preview.get("y_label", "PC2"),
        }
        with open_text_for_write(out_dir / "pca.json") as fh:
            json.dump(payload, fh, ensure_ascii=False)

    def _analysis_row(self, analysis_id: str | None) -> dict:
        if not analysis_id:
            raise AppError(INVALID_PARAMS, "'analysis_id' is required")
        row = self.db.query_one("SELECT * FROM analysis_runs WHERE id = ?", (analysis_id,))
        if row is None:
            raise AppError(ANALYSIS_NOT_FOUND, f"analysis {analysis_id} does not exist")
        return row

    def _public_analysis_row(self, row: dict, include_preview: bool = False) -> dict:
        output = dict(row)
        for key in ("input_run_ids_json", "dataset_ids_json", "warnings_json", "artifact_manifest_json", "preprocessing_json"):
            target = key.removesuffix("_json")
            output[target] = self._json_load(output.pop(key, None), [] if key.endswith("ids_json") or key == "warnings_json" else {})
        output.pop("params_json", None)
        output["parameters"] = self._json_load(row.get("params_json"), {})
        if include_preview:
            output["preview"] = self._json_load(row.get("preview_json"), {})
            output.pop("preview_json", None)
        else:
            output.pop("preview_json", None)
        return output

    def _require_artifact(self, row: dict) -> None:
        # STALE artifacts remain readable for audit and comparison history;
        # only _usable_run rejects stale descriptor inputs for new work.
        if row.get("status") not in ("COMPLETED", "STALE"):
            raise AppError(RESULT_INCOMPATIBLE, f"analysis {row['id']} is {row['status']}")
        if not self._artifact_is_complete(row):
            raise AppError(ARTIFACT_INVALID, f"analysis {row['id']} has no complete artifact")

    def _managed_artifact_path(self, analysis_id: str, stored: object) -> Path:
        return self._artifacts.managed_path(analysis_id, stored)

    @staticmethod
    def _artifact_file(root: Path, raw_name: object) -> Path | None:
        return AnalysisArtifactStore.artifact_file(root, raw_name)

    def _artifact_is_complete(self, row: dict) -> bool:
        return self._artifacts.is_complete(row)

    @staticmethod
    def _json_load(raw, fallback):
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
            return value if value is not None else fallback
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _canonical_params(params: dict) -> dict:
        def normalize(value):
            if isinstance(value, dict):
                return {str(k): normalize(value[k]) for k in sorted(value)}
            if isinstance(value, (list, tuple)):
                return [normalize(v) for v in value]
            if isinstance(value, float):
                if not np.isfinite(value):
                    raise AppError(ANALYSIS_INPUT_INVALID, "analysis parameters must be finite")
                return float(format(value, ".15g"))
            return value

        return normalize(params)

    @staticmethod
    def _analysis_cache_key(analysis_type: str, input_ids: list[str], params: dict) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "analysis_type": analysis_type,
                    "inputs": input_ids,
                    "params": params,
                    "algorithm_version": ANALYSIS_ALGORITHM_VERSION,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _rows_from_artifact(self, row: dict, offset: int, limit: int) -> list[dict]:
        manifest = self._json_load(row.get("artifact_manifest_json"), {})
        files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
        arrays = {}
        try:
            root = self._managed_artifact_path(str(row.get("id") or ""), row.get("result_path"))
        except (TypeError, ValueError, UnsafePathError):
            return []
        for name in ("coords", "indices", "labels", "scores", "distances", "similarity", "selected_indices"):
            meta = files.get(name)
            if isinstance(meta, dict):
                try:
                    target = self._artifact_file(root, meta.get("path"))
                    if target is None:
                        continue
                    arrays[name] = np.load(target, mmap_mode="r", allow_pickle=False)
                except (OSError, ValueError):
                    pass
        input_ids = self._json_load(row.get("input_run_ids_json"), [row.get("descriptor_run_id")])
        if isinstance(input_ids, list) and input_ids:
            source_index = 1 if row.get("analysis_type") in ("coverage", "drift") and len(input_ids) > 1 else 0
            source_row = self.db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (input_ids[source_index],))
            if source_row is not None:
                try:
                    params = self._json_load(row.get("params_json"), {})
                    built = self._build_preview({"arrays": arrays}, self._load_samples(source_row, params, row.get("analysis_type") or ""), row.get("analysis_type") or "")
                    candidate = built.get("rows") or built.get("selected") or built.get("points") or []
                    return candidate[offset : offset + limit]
                except (AppError, OSError, TypeError, ValueError, IndexError):
                    pass
        n = max((len(v) for v in arrays.values()), default=0)
        return [{"i": i, **{name: self._json_safe(value[i]) for name, value in arrays.items() if i < len(value)}} for i in range(offset, min(offset + limit, n))]

    @staticmethod
    def _json_safe(value):
        if isinstance(value, dict):
            return {str(k): AnalysisArtifactMixin._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [AnalysisArtifactMixin._json_safe(v) for v in value]
        if isinstance(value, np.ndarray):
            return AnalysisArtifactMixin._json_safe(value.tolist())
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        return value

    def _rmtree_quiet(self, path: Path | None) -> None:
        self._artifacts.remove_quiet(path)


__all__ = ["AnalysisArtifactMixin"]

