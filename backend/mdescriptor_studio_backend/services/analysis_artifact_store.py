"""Managed filesystem storage for generic analysis artifacts.

The analysis service owns request validation and result shaping; this module
owns the on-disk artifact contract (safe names, manifests, atomic publish,
and cleanup).  Keeping that boundary in one place prevents each analysis
endpoint from growing its own path and completeness checks.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Callable

import numpy as np

from ..errors import ARTIFACT_INVALID, AppError
from ..security import (
    UnsafePathError,
    ensure_no_reparse_points,
    open_text_for_write,
    remove_managed_tree,
    validate_managed_path,
)

log = logging.getLogger(__name__)

_ANALYSIS_ID_RE = re.compile(r"^ana_[A-Za-z0-9_-]{1,64}$")
_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_RESERVED_ARTIFACT_NAME_RE = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE
)


class AnalysisArtifactStore:
    """Read and publish artifacts beneath the configured analysis directory."""

    def __init__(self, data_dir: Path, *, algorithm_version: str, schema_version: int):
        self.data_dir = Path(data_dir).resolve(strict=False)
        self.algorithm_version = algorithm_version
        self.schema_version = schema_version

    def managed_path(self, analysis_id: str, stored: object) -> Path:
        if not _ANALYSIS_ID_RE.fullmatch(analysis_id or ""):
            raise UnsafePathError("invalid analysis id")
        return validate_managed_path(self.data_dir / "analysis", stored, analysis_id)

    @staticmethod
    def artifact_file(root: Path, raw_name: object) -> Path | None:
        if (
            not isinstance(raw_name, str)
            or raw_name in (".", "..")
            or raw_name.endswith((".", " "))
            or _RESERVED_ARTIFACT_NAME_RE.fullmatch(raw_name)
            or not _ARTIFACT_NAME_RE.fullmatch(raw_name)
        ):
            return None
        candidate = root / raw_name
        try:
            ensure_no_reparse_points(candidate)
        except UnsafePathError:
            return None
        return candidate

    def is_complete(self, row: dict) -> bool:
        path = row.get("result_path")
        if not path:
            return False
        try:
            root = self.managed_path(str(row.get("id") or ""), path)
        except (TypeError, ValueError, UnsafePathError):
            return False
        manifest_path = self.artifact_file(root, "manifest.json")
        if manifest_path is None or not manifest_path.is_file():
            # Legacy PCA is valid through its compatibility artifact.
            target = self.artifact_file(root, "pca.json")
            return bool(target and target.is_file())
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("completed") is not True:
                return False
            files = manifest.get("files")
            if not isinstance(files, dict) or not files:
                return False
            for file_meta in files.values():
                if not isinstance(file_meta, dict):
                    return False
                target = self.artifact_file(root, file_meta.get("path"))
                if target is None or not target.is_file():
                    return False
                expected_bytes = file_meta.get("bytes")
                if isinstance(expected_bytes, int) and expected_bytes != target.stat().st_size:
                    return False
            return True
        except (OSError, TypeError, ValueError):
            return False

    def commit(
        self,
        analysis_id: str,
        analysis_type: str,
        input_ids: list[str],
        params: dict,
        result: dict,
        preview: dict,
        ctx,
        json_safe: Callable[[object], object],
    ) -> tuple[Path, dict]:
        root = self.data_dir / "analysis"
        root.mkdir(parents=True, exist_ok=True)
        ensure_no_reparse_points(root)
        staging = root / f".{analysis_id}.tmp-{uuid.uuid4().hex[:8]}"
        final = root / analysis_id
        staging.mkdir(parents=True, exist_ok=False)
        ensure_no_reparse_points(staging)
        manifest = {
            "analysis_id": analysis_id,
            "analysis_type": analysis_type,
            "input_run_ids": input_ids,
            "algorithm_version": self.algorithm_version,
            "schema_version": self.schema_version,
            "completed": False,
            "files": {},
        }
        try:
            arrays = result.get("arrays", {})
            for name, value in arrays.items():
                ctx.check_cancelled()
                array = np.asarray(value)
                if np.issubdtype(array.dtype, np.floating):
                    array = array.astype(np.float64, copy=False)
                if not isinstance(name, str) or not name:
                    raise AppError(ARTIFACT_INVALID, "analysis artifact contains an invalid array name")
                normalized_name = unicodedata.normalize("NFKC", name)
                safe_name = (
                    normalized_name
                    if normalized_name not in (".", "..")
                    and len(normalized_name) <= 64
                    and not normalized_name.endswith((".", " "))
                    and not _RESERVED_ARTIFACT_NAME_RE.fullmatch(normalized_name)
                    and _ARTIFACT_NAME_RE.fullmatch(normalized_name)
                    else f"array-{hashlib.sha256(name.encode('utf-8')).hexdigest()[:16]}"
                )
                np.save(staging / f"{safe_name}.npy", array, allow_pickle=False)
                target = staging / f"{safe_name}.npy"
                manifest["files"][name] = {
                    "path": target.name,
                    "shape": list(array.shape),
                    "dtype": str(array.dtype),
                    "bytes": target.stat().st_size,
                }
            metadata = {
                "analysis_id": analysis_id,
                "analysis_type": analysis_type,
                "input_run_ids": input_ids,
                "parameters": params,
                "algorithm_version": self.algorithm_version,
                "warnings": list(result.get("warnings", [])),
                "preview": preview,
            }
            metadata["created_at"] = _now()
            with open_text_for_write(staging / "metadata.json") as fh:
                json.dump(json_safe(metadata), fh, ensure_ascii=False, indent=2)
            manifest["files"]["metadata"] = {
                "path": "metadata.json",
                "bytes": (staging / "metadata.json").stat().st_size,
            }
            manifest["completed"] = True
            # The manifest describes the payload and metadata, but not itself;
            # this keeps byte metadata stable.
            with open_text_for_write(staging / "manifest.json") as fh:
                json.dump(json_safe(manifest), fh, ensure_ascii=False, indent=2)
            ensure_no_reparse_points(final)
            if final.exists() or final.is_symlink():
                raise AppError(ARTIFACT_INVALID, f"analysis artifact already exists: {final}")
            os.replace(staging, final)
            return final, manifest
        except Exception:
            self.remove_quiet(staging)
            raise

    def remove_quiet(self, path: Path | None) -> None:
        if not path:
            return
        try:
            ensure_no_reparse_points(path)
            root = self.data_dir / "analysis"
            if Path(path).parent != root:
                log.warning("refusing to remove an unmanaged analysis path")
                return
            remove_managed_tree(path)
        except (OSError, UnsafePathError):
            log.warning("could not remove managed analysis artifact", exc_info=True)


def _now() -> str:
    # Avoid coupling artifact persistence to the service's request helpers.
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
