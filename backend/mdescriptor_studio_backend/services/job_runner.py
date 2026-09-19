"""Job execution for analyses: submission, the analysis runner and the sweep."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np

from ..analysis import (
    ANALYSIS_REGISTRY,
    DescriptorMatrix,
    StructureDescriptorMatrix,
)
from ..analysis.algorithms.sensitivity import perturbation_sensitivity
from ..errors import (
    ANALYSIS_INPUT_INVALID,
    ANALYSIS_INSUFFICIENT_SAMPLES,
    AppError,
    INVALID_PARAMS,
    JOB_CANCELLED,
)
from .analysis_helpers import (
    CROSS_DATASET_TYPES,
    _NOW,
    _block_names,
    _view_id,
)


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
        run_rows = [self._usable_run(run_id) for run_id in input_ids]
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
                preprocessing={"preprocess": params.get("preprocess"), "scaling": params.get("scaling")},
            )
            if early is not None:
                return early

            def runner(ctx):
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
                        frame_count = int(preview_samples.frame.max()) + 1 if preview_samples.frame.size else 1
                        properties = self._frame_properties(run_rows[0], frame_count)
                        preview["points"] = [
                            {**point, **properties[int(point["frame"])]}
                            if isinstance(point, dict) and 0 <= int(point.get("frame", -1)) < len(properties)
                            else point
                            for point in preview["points"]
                        ]
                    artifact_path, manifest = self._commit_artifact(
                        analysis_id,
                        analysis_type,
                        input_ids,
                        canonical_params,
                        result,
                        preview,
                        ctx,
                    )
                    if analysis_type == "pca":
                        # Keep result.get_pca usable for databases created by the
                        # legacy frontend. New code reads the generic preview.
                        self._write_legacy_pca_compatibility(artifact_path, analysis_id, input_ids[0], params, preview)
                    self._complete_analysis_run(
                        analysis_id,
                        {
                            "result_path": str(artifact_path),
                            "finished_at": _NOW(),
                            "warnings_json": json.dumps(result.get("warnings", []), ensure_ascii=False),
                            "artifact_manifest_json": json.dumps(manifest, ensure_ascii=False),
                            "preview_json": json.dumps(preview, ensure_ascii=False),
                            "preprocessing_json": json.dumps({"preprocess": params.get("preprocess")}, ensure_ascii=False),
                            "updated_at": _NOW(),
                        },
                        artifact_path,
                    )
                    ctx.progress(1, 1, "analysis complete")
                    n_points = preview_samples.n_samples if cross_dataset else samples[0].n_samples
                    return {"analysis_id": analysis_id, "n_points": n_points, "analysis_type": analysis_type, "warnings": result.get("warnings", [])}

                except BaseException:
                    # Keep failed runs atomic: a committed artifact must
                    # never outlive the run row that failed to settle.
                    if artifact_path is not None:
                        self._artifacts.remove_quiet(artifact_path)
                    raise
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

    def _apply_thread_limit(self) -> None:
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
            return
        try:
            limit = int(str(raw).strip())
        except (TypeError, ValueError):
            return
        if limit <= 0:
            return
        from threadpoolctl import threadpool_limits

        threadpool_limits(limits=limit)

    def _run_engine(self, analysis_type: str, params: dict, rows: list[dict], samples: list[DescriptorMatrix], ctx) -> dict:
        progress = lambda fraction, message: (ctx.check_cancelled(), ctx.progress(None, None, message, fraction=0.1 + 0.85 * float(fraction)))
        self._apply_thread_limit()
        spec = ANALYSIS_REGISTRY.get(analysis_type)
        if spec.category == "perturbation":
            return self._run_perturbation_sensitivity(params, rows[0], samples[0], ctx)
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
        source_adapter = self.datasets._adapter_for(dataset)
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
                self._perturb_frame(frame, amplitude, perturbation, jitter_vectors[index])
                for index, frame in enumerate(source_frames)
            ]
            batch = self.datasets.adapter.to_structure_batch(perturbed_frames)
            computed = self.datasets.adapter.compute(descriptor, batch, control)
            values = self._computed_structure_values(computed, len(perturbed_frames))
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

    @staticmethod
    def _perturb_frame(frame, amplitude: float, perturbation: str, jitter_vector: np.ndarray):
        positions = np.asarray(frame.positions, dtype=np.float64)
        if perturbation == "jitter":
            return replace(frame, positions=positions + amplitude * jitter_vector)
        center = positions.mean(axis=0, keepdims=True) if positions.size else np.zeros((1, 3), dtype=np.float64)
        scale = 1.0 + amplitude
        cell = np.asarray(frame.cell, dtype=np.float64)
        if cell.shape == (3, 3) and abs(float(np.linalg.det(cell))) > 1e-10:
            cell = cell * scale
        return replace(frame, positions=center + (positions - center) * scale, cell=cell)

    def _computed_structure_values(self, computed, frame_count: int) -> np.ndarray:
        values = np.asarray(computed.values, dtype=np.float64)
        if values.ndim > 2:
            values = values.reshape(values.shape[0], -1)
        if values.ndim == 1:
            values = values.reshape(-1, 1)
        if values.ndim != 2 or not np.isfinite(values).all():
            raise AppError(ANALYSIS_INPUT_INVALID, "perturbed descriptor result is not a finite 2D matrix")
        offsets = np.asarray(getattr(computed, "row_offsets", None), dtype=np.int64) if getattr(computed, "row_offsets", None) is not None else None
        if self._valid_offsets(offsets, values.shape[0]) and offsets.size == frame_count + 1:
            pooled = np.empty((frame_count, values.shape[1]), dtype=np.float64)
            for index in range(frame_count):
                lo, hi = int(offsets[index]), int(offsets[index + 1])
                pooled[index] = values[lo:hi].mean(axis=0) if hi > lo else 0.0
            return pooled
        if values.shape[0] == frame_count:
            return values
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "perturbed descriptor result cannot be aligned to structures",
            {"rows": int(values.shape[0]), "structures": frame_count},
        )


__all__ = ["AnalysisRunMixin"]
