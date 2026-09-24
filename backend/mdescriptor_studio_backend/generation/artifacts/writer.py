"""Managed filesystem storage for generation artifacts.

Mirrors ``services/analysis_artifact_store.py``: everything lands in a
staging directory first, the manifest is the completeness contract, and
``os.replace`` publishes atomically — an interrupted run never leaves a
half-written artifact behind. Big arrays live on disk only; SQLite keeps
metadata (database design rule: no per-atom storage in the database).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ...datasets.exporters import write_extxyz
from ...errors import ARTIFACT_INVALID, AppError
from ...security import (
    UnsafePathError,
    ensure_no_reparse_points,
    open_text_for_write,
    remove_managed_tree,
    validate_managed_path,
)

log = logging.getLogger(__name__)

_GEN_ID_RE = re.compile(r"^gen_[A-Za-z0-9_-]{1,64}$")
_ARRAY_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_RESERVED_NAME_RE = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class GenerationArtifactWriter:
    """Publish one generation run beneath ``data_dir/generation/{gen_id}``."""

    def __init__(self, data_dir: Path, *, algorithm_version: str, schema_version: int = 1) -> None:
        self.data_dir = Path(data_dir).resolve(strict=False)
        self.algorithm_version = algorithm_version
        self.schema_version = schema_version

    def managed_path(self, generation_id: str, stored: object) -> Path:
        if not _GEN_ID_RE.fullmatch(generation_id or ""):
            raise UnsafePathError("invalid generation id")
        return validate_managed_path(self.data_dir / "generation", stored, generation_id)

    def remove_quiet(self, path: Path | None) -> None:
        if not path:
            return
        try:
            ensure_no_reparse_points(path)
            if Path(path).parent != self.data_dir / "generation":
                log.warning("refusing to remove an unmanaged generation path")
                return
            remove_managed_tree(path)
        except (OSError, UnsafePathError):
            log.warning("could not remove managed generation artifact", exc_info=True)

    @staticmethod
    def _file(root: Path, raw_name: object) -> Path | None:
        if (
            not isinstance(raw_name, str)
            or raw_name in (".", "..")
            or raw_name.endswith((".", " "))
            or _RESERVED_NAME_RE.fullmatch(raw_name)
            or not _ARRAY_NAME_RE.fullmatch(raw_name)
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
        manifest_path = self._file(root, "manifest.json")
        if manifest_path is None or not manifest_path.is_file():
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("completed") is not True:
                return False
            files = manifest.get("files")
            if not isinstance(files, dict) or not files:
                return False
            for meta in files.values():
                if not isinstance(meta, dict):
                    return False
                target = self._file(root, meta.get("path"))
                if target is None or not target.is_file():
                    return False
                expected = meta.get("bytes")
                if isinstance(expected, int) and expected != target.stat().st_size:
                    return False
            return True
        except (OSError, TypeError, ValueError):
            return False

    def write_convergence(self, staging: Path, rounds: list) -> None:
        """Round-incremental trace; also mirrored into the DB preview per round."""
        payload = {"rounds": [r.to_json() if hasattr(r, "to_json") else dict(r) for r in rounds]}
        with open_text_for_write(staging / "convergence.json") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    @staticmethod
    def append_evaluated_frames(path: Path, candidates: list) -> None:
        """Spool one evaluated round without retaining its geometries in memory."""
        path = Path(path)
        batch = path.with_suffix(".batch.extxyz")
        try:
            write_extxyz(
                batch,
                [candidate.to_frame() for candidate in candidates],
                extra_comments={
                    index: f'generation={candidate.generation} candidate_id="{candidate.candidate_id}"'
                    for index, candidate in enumerate(candidates)
                },
            )
            with batch.open("rb") as source, path.open("ab") as output:
                shutil.copyfileobj(source, output)
        finally:
            batch.unlink(missing_ok=True)

    def commit(
        self,
        generation_id: str,
        *,
        request: dict,
        descriptor_signature: dict,
        run,
        metadata_extra: dict | None = None,
        evaluated_structures_path: Path | None = None,
        ctx=None,
        json_safe=lambda value: value,
    ) -> tuple[Path, dict]:
        root = self.data_dir / "generation"
        root.mkdir(parents=True, exist_ok=True)
        ensure_no_reparse_points(root)
        staging = root / f".{generation_id}.tmp-{uuid.uuid4().hex[:8]}"
        final = root / generation_id
        staging.mkdir(parents=True, exist_ok=False)
        ensure_no_reparse_points(staging)
        manifest = {
            "generation_id": generation_id,
            "optimizer": request.get("optimizer"),
            "objective": (request.get("objective") or {}).get("type"),
            "algorithm_version": self.algorithm_version,
            "schema_version": self.schema_version,
            "completed": False,
            "files": {},
        }
        try:
            accepted = list(run.accepted)
            evaluations = list(run.evaluations)
            # accepted.extxyz carries full provenance in every frame header.
            extra = {
                i: (
                    f'generation_id="{generation_id}" candidate_id="{c.candidate_id}" '
                    f"parent_frame={-1 if c.parent_frame is None else c.parent_frame} "
                    f"generation={c.generation} operator={c.operator} "
                    f"fitness={e.fitness:.10g} "
                    f"novelty={e.novelty if e.novelty is not None else float('nan'):.10g} "
                    f"local_novelty={e.local_diversity if e.local_diversity is not None else float('nan'):.10g}"
                )
                for i, (c, e) in enumerate(zip(accepted, evaluations))
            }
            write_extxyz(staging / "accepted.extxyz", [c.to_frame() for c in accepted], extra_comments=extra)
            manifest["files"]["accepted"] = {
                "path": "accepted.extxyz",
                "bytes": (staging / "accepted.extxyz").stat().st_size,
            }

            if evaluated_structures_path is not None and evaluated_structures_path.is_file():
                ensure_no_reparse_points(evaluated_structures_path)
                evaluated_output = staging / "evaluated.extxyz"
                shutil.copyfile(evaluated_structures_path, evaluated_output)
                manifest["files"]["evaluated_structures"] = {
                    "path": evaluated_output.name,
                    "bytes": evaluated_output.stat().st_size,
                }

            with open_text_for_write(staging / "candidates.jsonl") as fh:
                for c, e in zip(accepted, evaluations):
                    fh.write(
                        json.dumps(
                            json_safe(
                                {
                                    "candidate_id": c.candidate_id,
                                    "parent_frame": c.parent_frame,
                                    "parent_candidate_id": c.parent_candidate_id,
                                    "generation": c.generation,
                                    "operator": c.operator,
                                    "operator_params": c.operator_params,
                                    "fitness": e.fitness,
                                    "novelty": e.novelty,
                                    "local_diversity": e.local_diversity,
                                    "novel_environment_count": e.novel_environment_count,
                                    "atom_count": e.atom_count,
                                }
                            ),
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            manifest["files"]["candidates"] = {
                "path": "candidates.jsonl",
                "bytes": (staging / "candidates.jsonl").stat().st_size,
            }

            arrays = {
                "fitness": np.asarray([e.fitness for e in evaluations], dtype=np.float64),
                "novelty": np.asarray(
                    [e.novelty if e.novelty is not None else np.nan for e in evaluations], dtype=np.float64
                ),
                "local_diversity": np.asarray(
                    [e.local_diversity if e.local_diversity is not None else np.nan for e in evaluations],
                    dtype=np.float64,
                ),
                "generation": np.asarray([c.generation for c in accepted], dtype=np.int64),
                "parent_frame": np.asarray(
                    [-1 if c.parent_frame is None else c.parent_frame for c in accepted], dtype=np.int64
                ),
                "structure_descriptors": np.stack(
                    [np.asarray(e.structure_descriptor, dtype=np.float64) for e in evaluations]
                ),
            }
            has_local = any(e.atomic_descriptors is not None for e in evaluations)
            # Every evaluated candidate's structure descriptor drives the
            # descriptor-space map (gray = original, blue = evaluated,
            # orange = accepted). float32 halves the array; these are
            # visualization inputs, not scoring inputs.
            evaluated = list(run.evaluated)
            if evaluated:
                arrays["evaluated_structure_descriptors"] = np.stack(
                    [np.asarray(r.structure_descriptor, dtype=np.float32) for r in evaluated]
                )
                arrays["evaluated_generation"] = np.asarray(
                    [int(r.generation) for r in evaluated], dtype=np.int32
                )
                arrays["evaluated_novelty"] = np.asarray(
                    [float("nan") if r.novelty is None else float(r.novelty) for r in evaluated],
                    dtype=np.float32,
                )
                arrays["evaluated_accepted"] = np.asarray(
                    [1 if r.accepted else 0 for r in evaluated], dtype=np.int8
                )
            if has_local:
                local_rows = np.concatenate(
                    [np.asarray(e.atomic_descriptors, dtype=np.float64) for e in evaluations]
                )
                offsets = np.concatenate(
                    [[0], np.cumsum([0 if e.atomic_descriptors is None else len(e.atomic_descriptors) for e in evaluations])]
                ).astype(np.int64)
                arrays["local_environment_descriptors"] = local_rows
                arrays["local_row_offsets"] = offsets
            for name, value in arrays.items():
                if ctx is not None:
                    ctx.check_cancelled()
                array = np.asarray(value)
                safe = (
                    unicodedata.normalize("NFKC", name)
                    if _ARRAY_NAME_RE.fullmatch(name) and not _RESERVED_NAME_RE.fullmatch(name)
                    else f"array-{uuid.uuid4().hex[:12]}"
                )
                np.save(staging / f"{safe}.npy", array, allow_pickle=False)
                target = staging / f"{safe}.npy"
                manifest["files"][name] = {
                    "path": target.name,
                    "shape": list(array.shape),
                    "dtype": str(array.dtype),
                    "bytes": target.stat().st_size,
                }

            metadata = {
                "generation_id": generation_id,
                "request": request,
                "descriptor_signature": descriptor_signature,
                "algorithm_version": self.algorithm_version,
                "schema_version": self.schema_version,
                "stopped_by": run.stopped_by,
                "evaluations": run.evaluation_count,
                "accepted_count": run.accepted_count,
                "warnings": [],
                "created_at": _now(),
            }
            metadata.update(metadata_extra or {})
            with open_text_for_write(staging / "metadata.json") as fh:
                json.dump(json_safe(metadata), fh, ensure_ascii=False, indent=2)
            manifest["files"]["metadata"] = {"path": "metadata.json", "bytes": (staging / "metadata.json").stat().st_size}

            self.write_convergence(staging, run.rounds)
            manifest["files"]["convergence"] = {
                "path": "convergence.json",
                "bytes": (staging / "convergence.json").stat().st_size,
            }

            manifest["completed"] = True
            with open_text_for_write(staging / "manifest.json") as fh:
                json.dump(json_safe(manifest), fh, ensure_ascii=False, indent=2)
            ensure_no_reparse_points(final)
            if final.exists() or final.is_symlink():
                raise AppError(ARTIFACT_INVALID, f"generation artifact already exists: {final}")
            os.replace(staging, final)
            return final, manifest
        except Exception:
            remove_managed_tree(staging)
            raise
