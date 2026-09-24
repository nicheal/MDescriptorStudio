"""GenerationService: descriptor-guided dataset expansion runs.

Follows the AnalysisService contract: request validation → cache lookup →
run row + job creation → worker executes the engine loop → artifact
published atomically → row settled. Big arrays never enter SQLite.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path

import numpy as np

from ..errors import (
    ANALYSIS_INPUT_INVALID,
    ANALYSIS_STALE,
    ARTIFACT_INVALID,
    AppError,
    INVALID_PARAMS,
    JOB_NOT_FOUND,
    JOB_CANCELLED,
    RESULT_INCOMPATIBLE,
)
from ..generation.artifacts import GenerationArtifactReader, GenerationArtifactWriter
from ..generation.archive import DescriptorArchive, LocalEnvironmentArchive
from ..generation.constraints import build_constraints
from ..generation.engine import GenerationEngine
from ..generation.evaluator import DescriptorEvaluator, evaluate_batch
from ..generation.models import StructureCandidate, parse_request
from ..generation.registry import GENERATION_ALGORITHM_VERSION, GENERATION_REGISTRY
from ..analysis.sampling import fit_scaling
from ..security import UnsafePathError, ensure_no_reparse_points, validate_local_path
from ..datasets import create_adapter, detect_format
from .artifact_service import AnalysisArtifactMixin

log = logging.getLogger(__name__)

# Every column except preview_json: generation.list is a history endpoint the
# UI polls, and a per-row blob of tens of kilobytes must not ride that query
# (same contract as AnalysisService._LIST_COLUMNS).
_LIST_COLUMNS = (
    "id",
    "dataset_id",
    "descriptor_run_id",
    "optimizer",
    "objective",
    "params_json",
    "status",
    "created_at",
    "started_at",
    "finished_at",
    "evaluations",
    "accepted_count",
    "result_path",
    "warnings_json",
    "cache_key",
    "stale_reason",
    "updated_at",
)

_SEED_POOL_CAP = 512


def _now() -> str:
    from .analysis_helpers import _NOW

    return _NOW()


def _json_safe(value):
    return AnalysisArtifactMixin._json_safe(value)


class GenerationService:
    def __init__(self, db, jobs, results, datasets, data_dir: Path, frame_service=None) -> None:
        self.db = db
        self.jobs = jobs
        self.results = results
        self.datasets = datasets
        self.frame_service = frame_service
        self.data_dir = Path(data_dir).resolve(strict=False)
        self._artifacts = GenerationArtifactWriter(
            self.data_dir,
            algorithm_version=GENERATION_ALGORITHM_VERSION,
        )
        # Serialize the cache-lookup/create section so identical requests
        # cannot enqueue duplicate runs.
        self._submit_lock = threading.Lock()

    # -- catalog -------------------------------------------------------------
    def catalog(self, params: dict) -> dict:
        return GENERATION_REGISTRY.catalog()

    # -- cache key -------------------------------------------------------------
    def _descriptor_signature(self, run_row: dict, scaling: str) -> dict:
        try:
            parameters = json.loads(run_row.get("parameters_json") or "{}")
        except (TypeError, ValueError):
            parameters = {}
        return {
            "descriptor_name": run_row["descriptor_name"],
            "descriptor_version": run_row.get("descriptor_version"),
            "engine_version": run_row.get("engine_version"),
            "descriptor_parameters": parameters,
            "feature_count": run_row.get("feature_count"),
            "row_semantics": run_row.get("row_semantics"),
            "scaling": scaling,
            "algorithm_version": GENERATION_ALGORITHM_VERSION,
        }

    def _cache_key(self, request, dataset_row: dict, signature: dict, seed_scope_hash: str | None) -> str | None:
        # Generation is not a deterministic analysis: identical parameters
        # with different seeds are different runs. No seed → never cached.
        if request.seed is None:
            return None
        payload = {
            "dataset_fingerprint": dataset_row["fingerprint"],
            "descriptor_run_id": request.descriptor_run_id,
            "signature": signature,
            "optimizer": request.optimizer,
            "optimizer_params": request.optimizer_params,
            "objective": request.objective,
            "operators": [spec.__dict__ for spec in request.operators],
            "constraints": request.constraints,
            "budget": request.budget.__dict__,
            "seed": request.seed,
            "seed_scope_hash": seed_scope_hash,
        }
        blob = json.dumps(_json_safe(payload), sort_keys=True, ensure_ascii=False)
        return "gen:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _seed_view(self, request, dataset: dict) -> tuple[list[int] | None, str | None]:
        if request.seed_view_id is None:
            return None, None
        self.datasets.refresh_if_changed(dataset)
        view = self.db.query_one("SELECT * FROM dataset_views WHERE id = ?", (request.seed_view_id,))
        if view is None:
            raise AppError(INVALID_PARAMS, f"dataset view {request.seed_view_id} does not exist")
        if view["dataset_id"] != request.dataset_id:
            raise AppError(ANALYSIS_INPUT_INVALID, "seed view does not belong to the active dataset")
        if view["dataset_fingerprint"] != dataset["fingerprint"]:
            raise AppError(ANALYSIS_STALE, f"dataset view {request.seed_view_id} is stale")
        try:
            indices = json.loads(view["frame_indices_json"])
        except (TypeError, ValueError) as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "seed view has invalid frame indices") from exc
        frame_total = int(dataset["number_of_frames"])
        if not isinstance(indices, list) or not indices or any(
            isinstance(index, bool)
            or not isinstance(index, int)
            or not 0 <= index < frame_total
            for index in indices
        ):
            raise AppError(ANALYSIS_INPUT_INVALID, "seed view has no valid dataset frames")
        return indices, view["selection_hash"]

    # -- submit -----------------------------------------------------------------
    def submit(self, params: dict) -> dict:
        request = parse_request(params)
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (request.dataset_id,))
        if dataset is None:
            raise AppError(INVALID_PARAMS, f"dataset {request.dataset_id} does not exist")
        run_row = self.db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (request.descriptor_run_id,))
        if run_row is None:
            raise AppError(INVALID_PARAMS, f"descriptor run {request.descriptor_run_id} does not exist")
        if run_row["status"] != "COMPLETED" or not run_row.get("result_path"):
            raise AppError(RESULT_INCOMPATIBLE, f"descriptor run {request.descriptor_run_id} is {run_row['status']}")
        if run_row.get("device") in ("imported", "external"):
            raise AppError(RESULT_INCOMPATIBLE, "imported descriptor results cannot be used as generation targets")
        seed_frame_indices, seed_scope_hash = self._seed_view(request, dataset)
        scaling = str(request.objective.get("scaling") or "robust")
        signature = self._descriptor_signature(run_row, scaling)
        cache_key = self._cache_key(request, dataset, signature, seed_scope_hash)
        with self._submit_lock:
            if cache_key is not None:
                cached = self.db.query_one(
                    "SELECT * FROM generation_runs WHERE cache_key = ? AND status = 'COMPLETED'"
                    " ORDER BY created_at DESC LIMIT 1",
                    (cache_key,),
                )
                if cached is not None and self._artifacts.is_complete(cached):
                    return {"generation_id": cached["id"], "job_id": None, "cached": True}
            generation_id = f"gen_{np.random.default_rng().integers(0, 2**63):016x}"
            params_json = json.dumps(
                _json_safe(
                    {
                        "optimizer": request.optimizer,
                        "optimizer_params": request.optimizer_params,
                        "objective": request.objective,
                        "operators": [spec.__dict__ for spec in request.operators],
                        "constraints": request.constraints,
                        "budget": request.budget.__dict__,
                        "seed": request.seed,
                        "seed_mode": "fixed" if request.seed is not None else "random",
                        "seed_view_id": request.seed_view_id,
                        "seed_view_selection_hash": seed_scope_hash,
                    }
                ),
                ensure_ascii=False,
            )
            self.db.execute(
                "INSERT INTO generation_runs (id, dataset_id, descriptor_run_id, optimizer, objective, params_json,"
                " status, created_at, updated_at, cache_key)"
                " VALUES (?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?)",
                (
                    generation_id,
                    request.dataset_id,
                    request.descriptor_run_id,
                    request.optimizer,
                    request.objective["type"],
                    params_json,
                    _now(),
                    _now(),
                    cache_key,
                ),
            )
            job_id = self.jobs.submit(
                "generation.run",
                lambda ctx: self._run_generation(ctx, generation_id, request, signature, seed_frame_indices),
                dataset_id=request.dataset_id,
                descriptor_run_id=request.descriptor_run_id,
                generation_run_id=generation_id,
            )
            return {"generation_id": generation_id, "job_id": job_id, "cached": False}

    # -- read paths ---------------------------------------------------------------
    def _row(self, generation_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM generation_runs WHERE id = ?", (generation_id,))
        if row is None:
            raise AppError(INVALID_PARAMS, f"generation run {generation_id} does not exist")
        return row

    def get(self, params: dict) -> dict:
        row = self._row(str(params.get("id") or ""))
        preview = json.loads(row["preview_json"]) if row.get("preview_json") else None
        out = {key: row[key] for key in _LIST_COLUMNS}
        descriptor = self.db.query_one(
            "SELECT descriptor_name FROM descriptor_runs WHERE id = ?",
            (row.get("descriptor_run_id"),),
        )
        out["descriptor_name"] = descriptor["descriptor_name"] if descriptor else None
        out["preview"] = preview
        out["artifact_complete"] = self._artifacts.is_complete(row)
        return out

    def list(self, params: dict) -> list[dict]:
        params = params or {}
        conditions, args = [], []
        if params.get("dataset_id"):
            conditions.append("dataset_id = ?")
            args.append(str(params["dataset_id"]))
        if params.get("status"):
            conditions.append("status = ?")
            args.append(str(params["status"]))
        sql = f"SELECT {', '.join(_LIST_COLUMNS)} FROM generation_runs"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at DESC"
        return self.db.query(sql, tuple(args))

    def delete(self, params: dict) -> dict:
        generation_id = str((params or {}).get("id") or "")
        row = self._row(generation_id)
        if row["status"] in ("QUEUED", "RUNNING"):
            raise AppError(RESULT_INCOMPATIBLE, f"generation {generation_id} is {row['status']}")
        try:
            artifact_path = (
                self._artifacts.managed_path(generation_id, row.get("result_path"))
                if row.get("result_path")
                else None
            )
        except (TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(ARTIFACT_INVALID, "stored generation artifact path is invalid") from exc
        self.db.execute("DELETE FROM jobs WHERE generation_run_id = ?", (generation_id,))
        self.db.execute("DELETE FROM generation_runs WHERE id = ?", (generation_id,))
        self._artifacts.remove_quiet(artifact_path)
        return {"ok": True, "generation_id": generation_id}

    def preview(self, params: dict) -> dict:
        row = self._row(str(params.get("id") or ""))
        if not row.get("preview_json"):
            return {"rounds": []}
        return json.loads(row["preview_json"])

    def cancel(self, params: dict) -> dict:
        generation_id = str(params.get("id") or "")
        run = self._row(generation_id)
        if run["status"] not in ("QUEUED", "RUNNING"):
            return {"ok": True, "already_finished": True}
        job = self.db.query_one(
            "SELECT id FROM jobs WHERE generation_run_id = ? ORDER BY created_at DESC LIMIT 1",
            (generation_id,),
        )
        if job is None:
            raise AppError(JOB_NOT_FOUND, "no job is linked to this generation run")
        return self.jobs.cancel(job["id"])

    # -- worker ---------------------------------------------------------------------
    def _run_generation(
        self, ctx, generation_id: str, request, signature: dict, seed_frame_indices: list[int] | None
    ) -> dict:
        self.db.execute(
            "UPDATE generation_runs SET status = 'RUNNING', started_at = ?, updated_at = ? WHERE id = ?",
            (_now(), _now(), generation_id),
        )
        run_row = self.db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (request.descriptor_run_id,))
        if run_row is None or run_row["status"] != "COMPLETED":
            raise AppError(RESULT_INCOMPATIBLE, "descriptor run is no longer available")
        objective = GENERATION_REGISTRY.build_objective(request.objective)
        needs_atomic = bool(getattr(objective, "needs_atomic", False))

        # Reference descriptor matrix (raw rows + optional row offsets).
        raw_values, run_meta = self.results.load_values(run_row["id"], mmap=False)
        values = np.asarray(raw_values, dtype=np.float64)
        if values.ndim > 2:
            values = values.reshape(values.shape[0], -1)
        root = Path(run_meta["result_path"])
        offsets_file = root / "row_offsets.npy"
        offsets = (
            np.asarray(np.load(offsets_file, allow_pickle=False), dtype=np.int64)
            if offsets_file.is_file()
            else None
        )
        aligned = evaluate_batch(
            _NamespaceValues(values, offsets),
            self._structure_count(values, offsets),
            what="generation reference descriptor result",
        )
        reference_structure = aligned.structure_values
        reference_atomic = aligned.atomic_values if needs_atomic else None

        scaling_mode = signature["scaling"]
        structure_scaling, scaling_warnings = fit_scaling(reference_structure, scaling_mode)
        structure_archive = DescriptorArchive(reference_structure, structure_scaling)
        local_archive = None
        if needs_atomic and reference_atomic is not None:
            local_scaling, local_warnings = fit_scaling(reference_atomic, scaling_mode)
            local_archive = LocalEnvironmentArchive(reference_atomic, local_scaling)
            warnings = scaling_warnings + local_warnings
        else:
            warnings = scaling_warnings
            if needs_atomic:
                warnings.append("descriptor run has no atom-level rows; local objective sees structure rows only")

        # Seed pool: a bounded, seed-deterministic sample of dataset frames.
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (request.dataset_id,))
        if dataset is None:
            raise AppError(INVALID_PARAMS, "dataset does not exist")
        source = self.datasets.adapter_for(dataset)
        frame_total = len(source)
        if frame_total < 1:
            raise AppError(ANALYSIS_INPUT_INVALID, "dataset has no frames")
        rng = np.random.default_rng(request.seed) if request.seed is not None else np.random.default_rng()
        candidates = np.asarray(
            seed_frame_indices if seed_frame_indices is not None else range(frame_total), dtype=np.int64
        )
        take = min(len(candidates), _SEED_POOL_CAP)
        positions = np.linspace(0, len(candidates) - 1, take, dtype=np.int64)
        indices = candidates[positions]
        rng.shuffle(indices)
        seed_pool = []
        for position, frame_index in enumerate(indices.tolist()):
            ctx.check_cancelled()
            frame = source.get_frame(int(frame_index))
            seed_pool.append(
                StructureCandidate.from_frame(
                    frame,
                    candidate_id=f"seed_{position}",
                    parent_frame=int(frame_index),
                )
            )

        evaluator = DescriptorEvaluator(
            self.datasets.adapter,
            run_row["descriptor_name"],
            signature["descriptor_parameters"],
            device=str(run_row.get("device") or "cpu"),
        )
        control = self.datasets.adapter.make_control()
        ctx.attach_control(control)

        operators = [GENERATION_REGISTRY.build_operator(spec) for spec in request.operators]
        optimizer = GENERATION_REGISTRY.build_optimizer(request.optimizer, operators, request.optimizer_params)
        constraints = build_constraints(request.constraints)
        duplicate_threshold = request.constraints.get("duplicate_threshold")
        if duplicate_threshold is not None:
            duplicate_threshold = float(duplicate_threshold)

        budget = request.budget
        engine = GenerationEngine(
            seed_pool=seed_pool,
            evaluator=evaluator,
            structure_archive=structure_archive,
            local_archive=local_archive,
            objective=objective,
            optimizer=optimizer,
            constraints=constraints,
            budget=budget,
            rng=rng,
            n_seeds=int(request.optimizer_params.get("n_seeds", 64)),
            duplicate_threshold=duplicate_threshold,
        )

        rounds: list = []

        def on_round(record) -> None:
            rounds.append(record)
            preview = {
                "status": "RUNNING",
                "stopped_by": None,
                "accepted": int(sum(r.accepted for r in rounds)),
                "evaluations": int(sum(r.evaluations for r in rounds)),
                "rounds": [r.to_json() for r in rounds],
            }
            self.db.execute(
                "UPDATE generation_runs SET preview_json = ?, evaluations = ?, accepted_count = ?, updated_at = ?"
                " WHERE id = ?",
                (
                    json.dumps(_json_safe(preview), ensure_ascii=False),
                    preview["evaluations"],
                    preview["accepted"],
                    _now(),
                    generation_id,
                ),
            )

        def progress(completed, total, message) -> None:
            ctx.progress(completed, total, message)

        with tempfile.TemporaryDirectory(prefix="mds-generation-") as spool_dir:
            evaluated_frames_path = Path(spool_dir) / "evaluated.extxyz"
            run = engine.run(
                check_cancelled=ctx.check_cancelled,
                progress=progress,
                on_round=on_round,
                on_evaluated=lambda candidates: self._artifacts.append_evaluated_frames(
                    evaluated_frames_path, candidates
                ),
            )

            cancelled = run.stopped_by == "cancelled" or ctx.cancel_requested
            final = None
            manifest = None
            if run.accepted_count:
                final, manifest = self._artifacts.commit(
                    generation_id,
                    request={
                        "optimizer": request.optimizer,
                        "optimizer_params": request.optimizer_params,
                        "objective": request.objective,
                        "operators": [spec.__dict__ for spec in request.operators],
                        "constraints": request.constraints,
                        "budget": request.budget.__dict__,
                        "seed": request.seed,
                    },
                    descriptor_signature=signature,
                    run=run,
                    metadata_extra={"warnings": warnings},
                    evaluated_structures_path=evaluated_frames_path,
                    ctx=None,
                    json_safe=_json_safe,
                )
                cancelled = cancelled or ctx.cancel_requested

            preview = {
                "status": "CANCELLED" if cancelled else "COMPLETED",
                "stopped_by": "cancelled" if cancelled else run.stopped_by,
                "accepted": run.accepted_count,
                "evaluations": run.evaluation_count,
                "rounds": [r.to_json() for r in run.rounds],
            }
            self.db.execute(
                "UPDATE generation_runs SET status = ?, finished_at = ?, updated_at = ?,"
                " evaluations = ?, accepted_count = ?, result_path = ?, artifact_manifest_json = ?,"
                " preview_json = ?, warnings_json = ?"
                " WHERE id = ?",
                (
                    "CANCELLED" if cancelled else "COMPLETED",
                    _now(),
                    _now(),
                    run.evaluation_count,
                    run.accepted_count,
                    str(final) if final else None,
                    json.dumps(_json_safe(manifest), ensure_ascii=False) if manifest else None,
                    json.dumps(_json_safe(preview), ensure_ascii=False),
                    json.dumps(warnings, ensure_ascii=False),
                    generation_id,
                ),
            )
            if cancelled:
                raise AppError(JOB_CANCELLED, f"job {ctx.job_id} cancelled after saving accepted structures")
        return {"generation_id": generation_id, "accepted": run.accepted_count, "evaluations": run.evaluation_count}

    @staticmethod
    def _structure_count(values: np.ndarray, offsets) -> int:
        if (
            offsets is not None
            and offsets.ndim == 1
            and offsets.size >= 2
            and int(offsets[0]) == 0
            and int(offsets[-1]) == values.shape[0]
        ):
            return int(offsets.size - 1)
        return int(values.shape[0])

    # -- materialize / export ---------------------------------------------------------
    def _artifact_root(self, generation_id: str) -> Path:
        row = self._row(generation_id)
        if row["status"] not in ("COMPLETED", "CANCELLED") or not row.get("result_path"):
            raise AppError(RESULT_INCOMPATIBLE, f"generation run {generation_id} is {row['status']}")
        root = self._artifacts.managed_path(generation_id, row["result_path"])
        if not self._artifacts.is_complete(row):
            raise AppError(RESULT_INCOMPATIBLE, "generation artifact is incomplete")
        return root

    def materialize(self, params: dict) -> dict:
        generation_id = str(params.get("id") or "")
        row = self._row(generation_id)
        if row["status"] not in ("COMPLETED", "CANCELLED"):
            raise AppError(RESULT_INCOMPATIBLE, f"generation run {generation_id} is {row['status']}")
        dest = params.get("path")
        if not isinstance(dest, str) or not dest:
            raise AppError(INVALID_PARAMS, "'path' (string) is required")
        try:
            target = validate_local_path(dest, field="materialize destination")
        except UnsafePathError as exc:
            raise AppError(INVALID_PARAMS, "materialize destination must be an absolute local path") from exc
        if target.suffix.lower() not in (".xyz", ".extxyz"):
            raise AppError(INVALID_PARAMS, "materializing generated structures needs a .xyz / .extxyz destination")
        if target.exists():
            raise AppError(INVALID_PARAMS, "materialize destination already exists")

        def runner(ctx):
            root = self._artifact_root(generation_id)
            source = GenerationArtifactReader(root).accepted_extxyz()
            ctx.progress(0, 1, "writing generated dataset")
            shutil.copyfile(source, target)
            ensure_no_reparse_points(target)
            ctx.progress(1, 1, "done")
            return {
                "path": str(target),
                "name": params.get("name") or f"{generation_id}_expanded",
                "lineage": {
                    "parent_dataset_id": row["dataset_id"],
                    "operation": "dataset_generation",
                    "generation_run_id": generation_id,
                },
            }

        job_id = self.jobs.submit("generation.materialize", runner, dataset_id=row["dataset_id"], generation_run_id=generation_id)
        return {"job_id": job_id, "dest_path": str(target)}

    def add_to_dataset(self, params: dict) -> dict:
        generation_id = str(params.get("id") or "")
        row = self._row(generation_id)
        if row["status"] not in ("COMPLETED", "CANCELLED"):
            raise AppError(RESULT_INCOMPATIBLE, f"generation run {generation_id} is {row['status']}")
        if int(row.get("accepted_count") or 0) <= 0:
            raise AppError(INVALID_PARAMS, "this run has no accepted structures to add")

        dataset_id = params.get("dataset_id")
        if not isinstance(dataset_id, str) or not dataset_id:
            raise AppError(INVALID_PARAMS, "'dataset_id' (string) is required")
        dataset = self.datasets.row_or_raise(dataset_id)
        if dataset["format"] != "extxyz":
            raise AppError(INVALID_PARAMS, "adding generated structures is currently supported for extxyz datasets only")
        try:
            source = validate_local_path(dataset["source_path"], field="dataset source path")
        except UnsafePathError as exc:
            raise AppError(INVALID_PARAMS, "dataset source path is not a safe local path") from exc
        if not source.is_file() or detect_format(source) != "extxyz":
            raise AppError(INVALID_PARAMS, "the selected dataset is not an extxyz file")
        ensure_no_reparse_points(source)

        active_job = self.db.query_one(
            "SELECT job_type FROM jobs WHERE dataset_id = ? AND status IN ('QUEUED', 'RUNNING') LIMIT 1",
            (dataset_id,),
        )
        if active_job is not None:
            raise AppError(RESULT_INCOMPATIBLE, "wait for this dataset's active job before adding structures")

        def runner(ctx):
            root = self._artifact_root(generation_id)
            accepted = GenerationArtifactReader(root).accepted_extxyz()
            ensure_no_reparse_points(accepted)
            if not accepted.is_file() or accepted.stat().st_size == 0:
                raise AppError(RESULT_INCOMPATIBLE, "accepted structures artifact is empty")
            appended_structures = create_adapter(accepted, "extxyz").scan().number_of_frames
            if appended_structures != int(row["accepted_count"]):
                raise AppError(RESULT_INCOMPATIBLE, "accepted structures artifact does not match the run summary")

            source_scan = create_adapter(source, "extxyz").scan()
            source_frame_count = source_scan.number_of_frames
            source_size = source.stat().st_size
            accepted_size = accepted.stat().st_size
            with source.open("rb") as current:
                current.seek(0, os.SEEK_END)
                current.seek(-1, os.SEEK_CUR) if source_size else None
                needs_separator = bool(source_size) and current.read(1) not in (b"\n", b"\r")

            total = max(1, source_size + accepted_size + int(needs_separator))
            fd, temp_name = tempfile.mkstemp(prefix=".mds-generation-", suffix=".tmp", dir=source.parent)
            temporary = Path(temp_name)
            written = 0
            try:
                with os.fdopen(fd, "wb") as output, source.open("rb") as existing, accepted.open("rb") as added:
                    ctx.progress(0, total, "appending generated structures")
                    while chunk := existing.read(8 * 1024 * 1024):
                        ctx.check_cancelled()
                        output.write(chunk)
                        written += len(chunk)
                        ctx.progress(written, total, "appending generated structures")
                    if needs_separator:
                        output.write(b"\n")
                        written += 1
                    while chunk := added.read(8 * 1024 * 1024):
                        ctx.check_cancelled()
                        output.write(chunk)
                        written += len(chunk)
                        ctx.progress(written, total, "appending generated structures")
                    output.flush()
                    os.fsync(output.fileno())
                ensure_no_reparse_points(temporary)
                ctx.check_cancelled()
                os.replace(temporary, source)
            finally:
                temporary.unlink(missing_ok=True)

            scan_job_id = None
            try:
                scan_job_id = self.datasets.rescan({"id": dataset_id}).get("job_id")
            except Exception:  # noqa: BLE001 - the file is already safely updated
                log.exception("generated structures appended, but dataset rescan could not start")
            return {
                "dataset_id": dataset_id,
                "appended_frame_start": source_frame_count,
                "appended_structures": appended_structures,
                "scan_job_id": scan_job_id,
            }

        job_id = self.jobs.submit(
            "generation.add_to_dataset",
            runner,
            dataset_id=dataset_id,
            generation_run_id=generation_id,
        )
        return {"job_id": job_id}

    def pca(self, params: dict) -> dict:
        """Descriptor-space map: original (gray), evaluated (blue), accepted (orange).

        One 2-D PCA over the pooled reference structure descriptors plus every
        evaluated candidate's structure descriptor — the view that shows the
        expansion pushing into sparse regions.
        """
        generation_id = str(params.get("id") or "")
        row = self._row(generation_id)
        if row["status"] not in ("COMPLETED", "CANCELLED"):
            raise AppError(RESULT_INCOMPATIBLE, f"generation run {generation_id} is {row['status']}")
        root = self._artifact_root(generation_id)
        reader = GenerationArtifactReader(root)

        run_row = self.db.query_one(
            "SELECT * FROM descriptor_runs WHERE id = ?", (row["descriptor_run_id"],)
        )
        if run_row is None:
            raise AppError(RESULT_INCOMPATIBLE, "descriptor run is no longer available")
        if run_row["dataset_id"] != row["dataset_id"]:
            raise AppError(RESULT_INCOMPATIBLE, "descriptor run belongs to a different dataset")
        raw_values, run_meta = self.results.load_values(run_row["id"], mmap=False)
        values = np.asarray(raw_values, dtype=np.float64)
        if values.ndim > 2:
            values = values.reshape(values.shape[0], -1)
        offsets = None
        offsets_file = Path(run_meta["result_path"]) / "row_offsets.npy"
        if offsets_file.is_file():
            offsets = np.asarray(np.load(offsets_file, allow_pickle=False), dtype=np.int64)
        reference = evaluate_batch(
            _NamespaceValues(values, offsets),
            self._structure_count(values, offsets),
            what="generation reference descriptor result",
        ).structure_values
        if run_row.get("scope") == "frame":
            frame_index = run_row.get("frame_index")
            if not isinstance(frame_index, int):
                raise AppError(RESULT_INCOMPATIBLE, "descriptor run has no source frame index")
            reference_frames = np.asarray([frame_index], dtype=np.int64)
        else:
            reference_frames = np.arange(reference.shape[0], dtype=np.int64)
        if reference_frames.size != reference.shape[0]:
            raise AppError(RESULT_INCOMPATIBLE, "descriptor rows cannot be mapped to source frames")

        evaluated = None
        evaluated_file = root / "evaluated_structure_descriptors.npy"
        if evaluated_file.is_file():
            evaluated = np.asarray(reader.array("evaluated_structure_descriptors", mmap=True), dtype=np.float64)
        accepted_mask = None
        if evaluated is not None:
            accepted_mask = np.asarray(reader.array("evaluated_accepted", mmap=True)).astype(bool)

        # Bound the original cloud so the response frame stays well under the
        # protocol cap even for 100k-structure references.
        max_original = 20_000
        step = max(1, reference.shape[0] // max_original)
        reference = reference[::step]
        reference_frames = reference_frames[::step]

        combined = reference if evaluated is None else np.vstack([reference, evaluated])
        from ..analysis.algorithms.pca import pca as pca_algorithm
        from ..analysis.models import StructureDescriptorMatrix

        result = pca_algorithm(
            StructureDescriptorMatrix(
                values=np.ascontiguousarray(combined),
                frame=np.arange(combined.shape[0], dtype=np.int64),
            ),
            {},
        )
        coords = np.asarray(result["arrays"]["coords"], dtype=np.float64)
        n_ref = reference.shape[0]
        original = coords[:n_ref]
        evaluated_coords = coords[n_ref:] if evaluated is not None else np.empty((0, 2))

        candidates = reader.candidates()
        novel_environments = sum(int(c.get("novel_environment_count") or 0) for c in candidates)
        generated_environments = 0
        local_file = root / "local_environment_descriptors.npy"
        if local_file.is_file():
            generated_environments = int(np.load(local_file, allow_pickle=False, mmap_mode="r").shape[0])
        original_environments = int(values.shape[0]) if offsets is not None else int(reference.shape[0])

        finite = np.isfinite(original).all(axis=1)
        original = original[finite]
        reference_frames = reference_frames[finite]
        payload = {
            "x_label": result["preview"]["x_label"],
            "y_label": result["preview"]["y_label"],
            "original": original.tolist(),
            "original_frames": reference_frames.tolist(),
            "evaluated": evaluated_coords.tolist(),
            "evaluated_generation": (
                np.asarray(reader.array("evaluated_generation", mmap=True)).tolist() if evaluated is not None else []
            ),
            "evaluated_novelty": (
                [
                    None if not np.isfinite(v) else float(v)
                    for v in np.asarray(reader.array("evaluated_novelty", mmap=True), dtype=np.float64)
                ]
                if evaluated is not None
                else []
            ),
            "evaluated_accepted": accepted_mask.tolist() if accepted_mask is not None else [],
            "discovery": {
                "original_structures": int(reference.shape[0]),
                "accepted_structures": int(len(candidates)),
                "original_environments": original_environments,
                "generated_environments": generated_environments,
                "novel_environments": novel_environments,
            },
        }
        return _json_safe(payload)

    def structure(self, params: dict) -> dict:
        generation_id = str(params.get("id") or "")
        if self.frame_service is None:
            raise AppError(RESULT_INCOMPATIBLE, "structure preview is unavailable")
        root = self._artifact_root(generation_id)
        reader = GenerationArtifactReader(root)
        frame_index = params.get("index")
        if isinstance(frame_index, bool) or not isinstance(frame_index, int) or frame_index < 0:
            raise AppError(INVALID_PARAMS, "'index' must be a non-negative integer")
        adapter = create_adapter(reader.evaluated_extxyz(), "extxyz")
        frame = adapter.get_frame(frame_index)
        return self.frame_service.frame_from(
            frame,
            name=f"{generation_id} candidate {frame_index}",
            params=params,
        )

    def export(self, params: dict) -> dict:
        generation_id = str(params.get("id") or "")
        row = self._row(generation_id)
        if row["status"] not in ("COMPLETED", "CANCELLED"):
            raise AppError(RESULT_INCOMPATIBLE, f"generation run {generation_id} is {row['status']}")
        what = str(params.get("what") or "accepted")
        if what not in ("accepted", "convergence"):
            raise AppError(INVALID_PARAMS, "what must be 'accepted' or 'convergence'")
        dest = params.get("path")
        if not isinstance(dest, str) or not dest:
            raise AppError(INVALID_PARAMS, "'path' (string) is required")
        try:
            target = validate_local_path(dest, field="export destination")
        except UnsafePathError as exc:
            raise AppError(INVALID_PARAMS, "export destination must be an absolute local path") from exc
        if target.exists():
            raise AppError(INVALID_PARAMS, "export destination already exists")

        def runner(ctx):
            root = self._artifact_root(generation_id)
            reader = GenerationArtifactReader(root)
            source = reader.accepted_extxyz() if what == "accepted" else root / "convergence.json"
            ctx.progress(0, 1, f"exporting {what}")
            shutil.copyfile(source, target)
            ensure_no_reparse_points(target)
            ctx.progress(1, 1, "done")
            return {"path": str(target), "what": what}

        job_id = self.jobs.submit("generation.export", runner, dataset_id=row["dataset_id"], generation_run_id=generation_id)
        return {"job_id": job_id, "dest_path": str(target)}


class _NamespaceValues:
    """Minimal compute-result stand-in for evaluate_batch."""

    def __init__(self, values, row_offsets):
        self.values = values
        self.row_offsets = row_offsets
