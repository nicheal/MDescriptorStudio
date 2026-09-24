"""Job execution for analyses: submission, the analysis runner and the sweep."""

from __future__ import annotations

import json
import os
import pickle
import queue
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np

from ..analysis import (
    ANALYSIS_REGISTRY,
    DescriptorMatrix,
    StructureDescriptorMatrix,
)
from ..analysis.algorithms.sensitivity import perturbation_sensitivity
from ..generation.evaluator import structure_values as _descriptor_structure_values
from ..generation.operators import displaced, strained
from ..errors import (
    ANALYSIS_INPUT_INVALID,
    ANALYSIS_INSUFFICIENT_SAMPLES,
    AppError,
    INVALID_PARAMS,
    INTERNAL_ERROR,
    JOB_CANCELLED,
)
from .analysis_helpers import (
    CROSS_DATASET_TYPES,
    _NOW,
    _block_names,
    canonical_analysis_request,
    _view_id,
)
# These algorithms enter sklearn/LAPACK calls that expose no cancellation
# callback.  A process boundary is the only reliable way to stop them on
# Windows; algorithms with their own progress checkpoints remain in-process.
_HARD_CANCEL_ANALYSES = frozenset(
    {
        "pca",
        "tsne",
        "kernel",
        "feature_correlation",
        "property_correlation",
        "effective_dimension",
        "kmeans",
        "dbscan",
        "hdbscan",
        "agglomerative",
        "hierarchical",
        "knn",
        "lof",
        "isolation_forest",
        "isolation-forest",
        "iforest",
        "mahalanobis",
        "mahalanobis_distance",
        # Exact reference/query nearest-neighbour scanning followed by the
        # greedy FPS loop can saturate native numeric workers for a while.
        # Keep that load out of the RPC process so the desktop shell remains
        # responsive and cancellation can terminate it on Windows.
        "acquisition",
    }
)


def _preprocessing(params: dict) -> dict:
    """What `analysis_runs.preprocessing_json` records: the scale the run used.

    One expression because the row is written twice - once when the run is
    claimed, once when it settles - and the two had already drifted: the
    settlement dropped `scaling`, which for composite FPS is a parameter that
    defines the space the samples were drawn from.
    """
    return {"preprocess": params.get("preprocess"), "scaling": params.get("scaling")}


class AnalysisRunMixin:
    """Analysis job execution pipeline (load -> engine -> artifact settle)."""

    def submit_generic(self, analysis_type: str, params: dict) -> dict:
        """Create or reuse a generic analysis job.

        The returned shape is intentionally identical for every long-running
        module, including sampling and export.  The cache key contains all
        input run IDs and normalized parameters, plus this backend algorithm
        version, so changing implementation cannot reuse an old artifact.
        """
        params = dict(params or {})
        has_algorithm_selector = "algorithm" in params or "method" in params
        analysis_type, params = canonical_analysis_request(analysis_type, params)
        # Direct registry RPCs (analysis.fps, analysis.kmeans, ...) historically
        # had no selector field, while wrapper RPCs carry one. Keep those two
        # public routes from colliding in the cache after canonicalization.
        if not has_algorithm_selector:
            params.pop("algorithm", None)
        if analysis_type == "pca":
            mode = str(params.get("mode") or "structure")
            if mode not in ("structure", "atom"):
                raise AppError(INVALID_PARAMS, f"mode must be 'structure' or 'atom', got {mode!r}")
            preprocess = str(params.get("preprocess") or "center")
            if preprocess not in ("raw", "center", "standardized"):
                raise AppError(ANALYSIS_INPUT_INVALID, "preprocess must be raw, center, or standardized")
            # PCA is deterministic; keep old callers and the generic request
            # path on one cache identity regardless of an irrelevant seed.
            params.update({"mode": mode, "preprocess": preprocess, "seed": 42})
        if analysis_type == "effective_dimension" and ("preprocess" not in params or params["preprocess"] is None or params["preprocess"] == ""):
            # Keep the backend default identical to the UI default so omitted
            # and explicit standardized requests share one cache identity.
            params["preprocess"] = "standardized"
        input_ids = self._input_ids(analysis_type, params)
        # The dataset freshness probe is per submission, not per run: a three-run
        # sensitivity submission shares one dataset and used to walk and hash that
        # directory three times before loading a single sample.
        current_datasets: set[str] = set()
        run_rows = self._usable_runs(input_ids, current_datasets)
        cross_dataset = analysis_type in CROSS_DATASET_TYPES
        warm_start_fps = analysis_type == "fps" and len(input_ids) == 2
        if cross_dataset or warm_start_fps:
            signatures_and_meta = [self.results.feature_space_signature(run_id) for run_id in input_ids]
            signatures = [item[0] for item in signatures_and_meta]
            if not signatures[0] or signatures[0] != signatures[1]:
                side = "reference and query" if cross_dataset else "candidate and existing"
                raise AppError(
                    ANALYSIS_INPUT_INVALID,
                    f"{side} runs must use the same descriptor feature space",
                    {
                        "reference_run_id": input_ids[0],
                        "query_run_id": input_ids[1],
                        "reference_descriptor": run_rows[0].get("descriptor_name"),
                        "query_descriptor": run_rows[1].get("descriptor_name"),
                        "reference_feature_count": signatures_and_meta[0][1].get("feature_count"),
                        "query_feature_count": signatures_and_meta[1][1].get("feature_count"),
                    },
                )
            view_ids = [params.get("reference_view_id"), params.get("query_view_id")]
            for index, view_id in enumerate(view_ids):
                if not view_id:
                    continue
                view = self._usable_view(str(view_id), run_rows[index]["dataset_id"])
                side = "reference" if index == 0 else "query"
                params[f"{side}_selection_hash"] = view["selection_hash"]
        view_id = _view_id(params)
        if view_id:
            # Single-run analyses scope to a dataset view by slicing the run's
            # samples to the view frames (same lens the cross-dataset modules
            # use). The selection hash joins the cache key so a view's *content*,
            # not just its id, decides reuse.  This cannot sit in an ``elif`` on
            # the branch above: warm-start FPS takes that branch too, and the
            # runner still slices its candidate set by view_id — skipping the
            # validation left a stale or foreign view to fail minutes later
            # inside the job, and skipping the hash let a result survive an edit
            # of the very frames it was computed on.  Warm-start FPS scopes the
            # view to the candidate run; the existing set is always used whole.
            if len(run_rows) != 1 and not warm_start_fps:
                raise AppError(
                    ANALYSIS_INPUT_INVALID,
                    "view_id applies to single-run analyses only",
                    {"analysis_type": analysis_type},
                )
            view = self._usable_view(view_id, run_rows[0]["dataset_id"])
            params["selection_hash"] = view["selection_hash"]
        if analysis_type == "sensitivity":
            descriptor_names = {str(row["descriptor_name"]) for row in run_rows}
            if len(descriptor_names) > 1:
                raise AppError(
                    ANALYSIS_INPUT_INVALID,
                    "parameter sensitivity requires the same descriptor; use Compare for different descriptors",
                    {"descriptors": sorted(descriptor_names)},
                )
        if analysis_type == "fps":
            # Validate the composite block vocabulary before enqueuing: a typo
            # should fail the request, not create a job that fails a minute later.
            _block_names(params)
        if analysis_type == "stratified":
            source = str(params.get("stratification_source") or "").strip().lower()
            if source not in {"composition", "element_set"}:
                raise AppError(
                    ANALYSIS_INPUT_INVALID,
                    "stratified sampling requires an explicit supported grouping variable",
                    {"supported_sources": ["composition", "element_set"]},
                )
            params["stratification_source"] = source
        canonical_params = self._canonical_params(params)
        cache_key = self._analysis_cache_key(analysis_type, input_ids, canonical_params)
        with self._submit_lock:
            analysis_id, early, created = self._claim_analysis_row(
                analysis_type=analysis_type,
                cache_key=cache_key,
                canonical_params=canonical_params,
                input_ids=input_ids,
                primary_run_id=run_rows[0]["id"],
                dataset_ids=sorted({row["dataset_id"] for row in run_rows}),
                preprocessing=_preprocessing(params),
            )
            if early is not None:
                return early

            def runner(ctx):
                return self._execute_analysis_job(
                    analysis_id,
                    analysis_type,
                    params,
                    input_ids,
                    canonical_params,
                    run_rows,
                    ctx,
                    cross_dataset=cross_dataset,
                    warm_start_fps=warm_start_fps,
                )
            try:
                job_id = self.jobs.submit(
                    f"analysis.{analysis_type}",
                    runner,
                    dataset_id=run_rows[0]["dataset_id"],
                    analysis_run_id=analysis_id,
                )
            except Exception:
                if created:
                    self.db.execute("DELETE FROM analysis_runs WHERE id = ?", (analysis_id,))
                raise
            return {"job_id": job_id, "analysis_id": analysis_id, "cache": None}

    def _execute_analysis_job(
        self,
        analysis_id: str,
        analysis_type: str,
        params: dict,
        input_ids: list[str],
        canonical_params: dict,
        run_rows: list[dict],
        ctx,
        *,
        cross_dataset: bool,
        warm_start_fps: bool,
    ) -> dict:
        """Run one claimed analysis row; submission stays focused on identity."""
        artifact_path: Path | None = None
        try:
            self._mark_run_running(analysis_id)
            ctx.progress(0, 1, "loading descriptor results")
            view_ids = (
                [params.get("reference_view_id"), params.get("query_view_id")]
                if cross_dataset
                else [params.get("view_id"), None]
                if warm_start_fps
                else [params.get("view_id")] * len(run_rows)
            )
            samples = [
                self._load_samples(
                    row,
                    params,
                    analysis_type,
                    check=ctx.check_cancelled,
                    view_id=view_ids[index],
                )
                for index, row in enumerate(run_rows)
            ]
            ctx.check_cancelled()
            result = self._run_engine(analysis_type, params, run_rows, samples, ctx)
            ctx.check_cancelled()
            preview_samples = samples[1] if cross_dataset else samples[0]
            preview = self._build_preview(
                result,
                preview_samples,
                analysis_type,
                reference_samples=samples[0] if cross_dataset else None,
            )
            if analysis_type == "pca" and isinstance(preview.get("points"), list):
                frame_of = {int(point["frame"]) for point in preview["points"] if isinstance(point, dict) and "frame" in point}
                # Only colour the frames present in the bounded preview. A view
                # selecting frames 10 and 11 must not decode frames 0..11.
                properties = self._frame_properties_by_frame(run_rows[0], sorted(frame_of))
                preview["points"] = [
                    {**point, **properties[int(point["frame"])]}
                    if isinstance(point, dict) and "frame" in point and int(point["frame"]) in properties
                    else point
                    for point in preview["points"]
                ]
            # The estimator may clamp an omitted/default parameter to the
            # available sample count.  Store the value actually used so a
            # history restore and the artifact metadata describe the same run.
            settled_params = dict(canonical_params)
            if analysis_type == "tsne" and isinstance(preview.get("parameters"), dict):
                effective = preview["parameters"].get("perplexity")
                if isinstance(effective, (int, float)) and np.isfinite(effective):
                    # Keep the UI's 30-valued auto mode restorable while also
                    # recording the concrete value used for this sample count.
                    settled_params.setdefault("perplexity", 30.0)
                    settled_params["effective_perplexity"] = float(effective)
            if analysis_type == "property_correlation":
                for key in (
                    "requested_reliability_k",
                    "effective_reliability_k_min",
                    "effective_reliability_k_max",
                    "reliability_k",
                ):
                    if key in preview:
                        settled_params[key] = preview[key]
            artifact_path, manifest = self._commit_artifact(
                analysis_id,
                analysis_type,
                input_ids,
                settled_params,
                result,
                preview,
                ctx,
            )
            if analysis_type == "pca":
                # Keep result.get_pca usable for legacy frontend databases.
                self._write_legacy_pca_compatibility(artifact_path, analysis_id, input_ids[0], params, preview)
            self._complete_analysis_run(
                analysis_id,
                {
                    "result_path": str(artifact_path),
                    "finished_at": _NOW(),
                    "warnings_json": json.dumps(result.get("warnings", []), ensure_ascii=False),
                    "artifact_manifest_json": json.dumps(manifest, ensure_ascii=False),
                    "preview_json": json.dumps(preview, ensure_ascii=False, allow_nan=False),
                    "params_json": json.dumps(settled_params, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    "preprocessing_json": json.dumps(_preprocessing(params), ensure_ascii=False),
                    "updated_at": _NOW(),
                },
                artifact_path,
            )
            ctx.progress(1, 1, "analysis complete")
            n_points = preview_samples.n_samples if cross_dataset else samples[0].n_samples
            return {"analysis_id": analysis_id, "n_points": n_points, "analysis_type": analysis_type, "warnings": result.get("warnings", [])}
        except BaseException:
            # Keep failed runs atomic: a committed artifact must never outlive
            # the run row that failed to settle.
            if artifact_path is not None:
                self._artifacts.remove_quiet(artifact_path)
            raise

    def _mark_run_running(self, analysis_id: str) -> None:
        """Flip the analysis run to RUNNING when its job actually starts.
        Without this the row sits at QUEUED for the whole computation and the
        history shows 排队中 for a result that is being computed right now.
        The WHERE guard keeps a cancel-settled row from being resurrected."""
        self.db.execute(
            "UPDATE analysis_runs SET status = 'RUNNING', updated_at = ? WHERE id = ? AND status IN ('QUEUED', 'RUNNING')",
            (_NOW(), analysis_id),
        )

    def _complete_analysis_run(self, analysis_id: str, values: dict[str, object], artifact_path: Path) -> None:
        """Settle an analysis artifact with a cancellation-safe status CAS."""
        allowed = {
            "result_path",
            "finished_at",
            "warnings_json",
            "artifact_manifest_json",
            "preview_json",
            "params_json",
            "preprocessing_json",
            "updated_at",
        }
        if not set(values) <= allowed:
            raise ValueError("unsupported analysis completion field")
        assignments = ["status = 'COMPLETED'"]
        params: list[object] = []
        for column, value in values.items():
            assignments.append(f"{column} = ?")
            params.append(value)
        params.append(analysis_id)
        changed = self.db.execute(
            f"UPDATE analysis_runs SET {', '.join(assignments)} WHERE id = ? AND status = 'RUNNING'",
            tuple(params),
        )
        if changed == 1:
            return
        self._artifacts.remove_quiet(artifact_path)
        raise AppError(JOB_CANCELLED, f"analysis run {analysis_id} was cancelled")

    def _apply_thread_limit(self) -> int | None:
        """Apply the user's `compute.default_threads` setting to the numeric
        stack used by analysis compute (BLAS/OpenMP pools via threadpoolctl).

        Applied process-wide and deliberately left in place: the setting is a
        user preference for analysis parallelism, not a per-job override, and
        concurrent analysis jobs all read the same value. Descriptor engine
        threads are managed by the engine itself and unaffected. Missing,
        invalid, or non-positive values mean "engine default" (no change).
        """
        raw = self.db.get_setting("compute.default_threads")
        if not raw:
            return None
        try:
            limit = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        if limit <= 0:
            return None
        from threadpoolctl import threadpool_limits

        threadpool_limits(limits=limit)
        return limit

    @staticmethod
    def _stop_analysis_process(process) -> None:
        """Terminate a black-box worker and reap it without blocking forever."""
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)

    @staticmethod
    def _analysis_worker_command() -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--mdescriptor-analysis-worker"]
        return [
            sys.executable,
            "-c",
            "from mdescriptor_studio_backend.services.analysis_worker import run_analysis_subprocess; run_analysis_subprocess()",
        ]

    def _run_isolated_analysis(self, analysis_type: str, params: dict, samples: list[DescriptorMatrix], ctx, *, thread_limit: int | None = None) -> dict:
        """Run a non-cooperative numerical call in a killable child process."""
        ctx.check_cancelled()
        ctx.progress(None, None, f"running {analysis_type}", fraction=0.1)
        package_root = str(Path(__file__).resolve().parents[2])
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (package_root, env.get("PYTHONPATH"))))
        process = subprocess.Popen(
            self._analysis_worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        result_queue: queue.Queue[object] = queue.Queue(maxsize=1)

        def read_result() -> None:
            try:
                assert process.stdout is not None
                result_queue.put(pickle.load(process.stdout))
            except BaseException as exc:  # noqa: BLE001 - parent turns it into AppError
                result_queue.put(("reader_error", type(exc).__name__, str(exc)))

        reader = threading.Thread(target=read_result, name=f"analysis-reader-{analysis_type}", daemon=True)
        reader.start()
        try:
            assert process.stdin is not None
            pickle.dump((analysis_type, samples, dict(params), thread_limit), process.stdin, protocol=pickle.HIGHEST_PROTOCOL)
            process.stdin.close()
            packet = None
            while packet is None:
                try:
                    packet = result_queue.get(timeout=0.05)
                except queue.Empty:
                    ctx.check_cancelled()
            if not packet or packet[0] == "ok":
                if not packet:
                    raise AppError(INTERNAL_ERROR, f"analysis worker returned no result for {analysis_type}")
                ctx.check_cancelled()
                ctx.progress(None, None, f"{analysis_type} complete", fraction=0.95)
                return packet[1]
            if packet[0] == "app_error":
                raise AppError(packet[1], packet[2], packet[3], public_message=packet[4])
            if packet[0] == "reader_error":
                raise AppError(INTERNAL_ERROR, f"analysis worker output could not be read: {packet[2]}")
            raise AppError(INTERNAL_ERROR, f"{analysis_type} worker failed: {packet[2]}")
        finally:
            if process.poll() is None:
                self._stop_analysis_process(process)
            reader.join(timeout=0.2)

    def _run_engine(self, analysis_type: str, params: dict, rows: list[dict], samples: list[DescriptorMatrix], ctx) -> dict:
        progress = lambda fraction, message: (ctx.check_cancelled(), ctx.progress(None, None, message, fraction=0.1 + 0.85 * float(fraction)))
        thread_limit = self._apply_thread_limit()
        spec = ANALYSIS_REGISTRY.get(analysis_type)
        if spec.category == "perturbation":
            return self._run_perturbation_sensitivity(params, rows[0], samples[0], ctx)
        if analysis_type in _HARD_CANCEL_ANALYSES:
            return self._run_isolated_analysis(analysis_type, params, samples, ctx, thread_limit=thread_limit)
        if spec.category in ("engine", "pair", "cluster", "outlier"):
            return ANALYSIS_REGISTRY.run(analysis_type, samples, params, progress)
        if spec.category == "sensitivity":
            return ANALYSIS_REGISTRY.run(analysis_type, samples, params, progress, runs=list(zip(rows, samples)))
        if spec.category == "sampling":
            existing = samples[1] if analysis_type == "fps" and len(samples) > 1 else None
            group_labels = None
            blocks = None
            existing_blocks = None
            if analysis_type == "fps":
                block_names = _block_names(params)
                if str(params.get("strategy") or "global") == "grouped":
                    # "" for "no view", the same spelling the fps_quota preview
                    # uses: the two share one label cache and must hit it alike.
                    group_labels = self._element_group_labels(rows[0], samples[0], (params.get("selection_hash") or "", samples[0].n_samples))
                if block_names:
                    # Composition fractions must share one element layout across
                    # candidate and existing sets, so use their union.
                    element_list = sorted({
                        *self._dataset_elements(rows[0]),
                        *(self._dataset_elements(rows[1]) if existing is not None else []),
                    })
                    blocks = self._sampling_blocks(rows[0], samples[0], block_names, params, element_list)
                    if existing is not None:
                        existing_blocks = self._sampling_blocks(rows[1], samples[1], block_names, params, element_list)
            elif analysis_type == "stratified":
                source = str(params.get("stratification_source") or "").strip().lower()
                if source not in {"composition", "element_set"}:
                    raise AppError(
                        ANALYSIS_INPUT_INVALID,
                        "stratified sampling requires an explicit supported grouping variable",
                        {"supported_sources": ["composition", "element_set"]},
                    )
                group_labels = self._element_group_labels(
                    rows[0],
                    samples[0],
                    (params.get("selection_hash") or "", samples[0].n_samples),
                    source=source,
                )
            return ANALYSIS_REGISTRY.run(
                analysis_type,
                samples,
                params,
                progress,
                existing=existing,
                group_labels=group_labels,
                blocks=blocks,
                existing_blocks=existing_blocks,
            )
        raise AppError(ANALYSIS_INPUT_INVALID, f"unsupported analysis type: {analysis_type}")

    def _run_perturbation_sensitivity(self, params: dict, run_row: dict, samples: StructureDescriptorMatrix, ctx) -> dict:
        """Recompute one descriptor on deterministic structure perturbations."""
        if self.datasets is None:
            raise AppError(ANALYSIS_INPUT_INVALID, "structural perturbation requires the dataset service")
        if not isinstance(samples, StructureDescriptorMatrix):
            raise AppError(ANALYSIS_INPUT_INVALID, "structural perturbation sensitivity is structure-level only")
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset is None:
            raise AppError(ANALYSIS_INPUT_INVALID, f"dataset {run_row['dataset_id']} does not exist")
        source_adapter = self.datasets.adapter_for(dataset)
        frame_count = samples.n_samples
        if frame_count < 1:
            raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "at least one structure is required")
        max_structures = params.get("max_structures", 64)
        try:
            max_structures = int(max_structures)
        except (TypeError, ValueError) as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "max_structures must be an integer") from exc
        if max_structures < 1:
            raise AppError(ANALYSIS_INPUT_INVALID, "max_structures must be >= 1")
        # Every selected structure is recomputed once per amplitude, so the cap
        # is a compute budget: keep it explicit and bounded instead of letting
        # one request schedule an unbounded descriptor sweep.
        if max_structures > 2048:
            raise AppError(ANALYSIS_INPUT_INVALID, "max_structures must be <= 2048")
        selected_indices = np.linspace(0, frame_count - 1, min(frame_count, max_structures), dtype=np.int64)
        selected_frames = np.asarray(samples.frame, dtype=np.int64)[selected_indices]
        baseline = StructureDescriptorMatrix(
            values=np.asarray(samples.values, dtype=np.float64)[selected_indices],
            frame=selected_frames,
            sample_ids=[samples.sample_ids[int(index)] for index in selected_indices.tolist()],
        )

        perturbation = str(params.get("perturbation") or "jitter").lower()
        if perturbation not in ("jitter", "strain"):
            raise AppError(ANALYSIS_INPUT_INVALID, "perturbation must be jitter or strain")
        raw_amplitudes = params.get("amplitudes")
        if raw_amplitudes is None:
            try:
                count = int(params.get("n_amplitudes", 8))
                maximum = float(params.get("max_amplitude", 0.2 if perturbation == "jitter" else 0.1))
            except (TypeError, ValueError) as exc:
                raise AppError(ANALYSIS_INPUT_INVALID, "n_amplitudes and max_amplitude must be numeric") from exc
            if count < 2:
                raise AppError(ANALYSIS_INPUT_INVALID, "n_amplitudes must be >= 2")
            raw_amplitudes = np.linspace(0.0, maximum, count).tolist()
        if not isinstance(raw_amplitudes, (list, tuple)):
            raise AppError(ANALYSIS_INPUT_INVALID, "amplitudes must be a list of numbers")
        if len(raw_amplitudes) < 2 or len(raw_amplitudes) > 32:
            raise AppError(ANALYSIS_INPUT_INVALID, "amplitudes must contain between 2 and 32 values")
        try:
            amplitudes = [float(value) for value in raw_amplitudes]
        except (TypeError, ValueError) as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "amplitudes must be a list of numbers") from exc
        if not all(np.isfinite(value) and value >= 0 for value in amplitudes):
            raise AppError(ANALYSIS_INPUT_INVALID, "amplitudes must be finite and non-negative")
        if perturbation == "strain" and any(value >= 1.0 for value in amplitudes):
            raise AppError(ANALYSIS_INPUT_INVALID, "strain amplitudes must be smaller than 1")
        if not any(np.isclose(value, 0.0) for value in amplitudes):
            amplitudes = [0.0, *amplitudes]

        try:
            seed = int(params.get("seed", 42))
        except (TypeError, ValueError) as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "seed must be an integer") from exc
        rng = np.random.default_rng(seed)
        source_frames = [source_adapter.get_frame(int(frame)) for frame in selected_frames.tolist()]
        jitter_vectors = []
        for frame in source_frames:
            vector = rng.normal(size=np.asarray(frame.positions).shape)
            scale = float(np.sqrt(np.mean(vector * vector)))
            jitter_vectors.append(vector / max(scale, 1e-15))

        try:
            descriptor_parameters = json.loads(run_row.get("parameters_json") or "{}")
        except (TypeError, ValueError):
            descriptor_parameters = {}
        # Sensitivity recomputes the producer descriptor; keep the device the
        # run originally declared so baselines stay reproducible.
        run_device = str(run_row.get("device") or "cpu")
        descriptor = self.datasets.adapter.build(run_row["descriptor_name"], descriptor_parameters, device=run_device)
        control = self.datasets.adapter.make_control()
        ctx.attach_control(control)
        perturbation_results: list[tuple[float, StructureDescriptorMatrix]] = []
        for amplitude_index, amplitude in enumerate(amplitudes):
            ctx.check_cancelled()
            perturbed_frames = [
                displaced(frame, amplitude * jitter_vectors[index])
                if perturbation == "jitter"
                else strained(frame, 1.0 + amplitude)
                for index, frame in enumerate(source_frames)
            ]
            batch = self.datasets.adapter.to_structure_batch(perturbed_frames)
            computed = self.datasets.adapter.compute(descriptor, batch, control)
            values = _descriptor_structure_values(computed, len(perturbed_frames))
            perturbation_results.append((
                amplitude,
                StructureDescriptorMatrix(
                    values=values,
                    frame=selected_frames.copy(),
                    sample_ids=list(baseline.sample_ids),
                ),
            ))
            ctx.progress(
                amplitude_index + 1,
                len(amplitudes),
                "computing structural perturbations",
                fraction=0.1 + 0.65 * (amplitude_index + 1) / len(amplitudes),
            )

        def report(fraction: float, message: str) -> None:
            ctx.check_cancelled()
            ctx.progress(None, None, message, fraction=0.75 + 0.25 * float(fraction))

        result = perturbation_sensitivity(baseline, perturbation_results, params, report)
        # The response curve is a summary over the selected structures, so the
        # artifact must say how many structures the run could have offered.
        # Otherwise "Structures: 64" reads as a property of the dataset.
        sampled = int(selected_indices.size)
        result["preview"]["available_structure_count"] = int(frame_count)
        if sampled < frame_count:
            result["warnings"] = [
                *result.get("warnings", []),
                f"sampled {sampled} of {frame_count} structures evenly across the run",
            ]
        return result


__all__ = ["AnalysisRunMixin"]
