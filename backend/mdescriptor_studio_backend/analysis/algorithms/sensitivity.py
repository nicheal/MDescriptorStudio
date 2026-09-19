"""Analysis algorithms: sensitivity."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Callable

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, ANALYSIS_INSUFFICIENT_SAMPLES, AppError
from ..models import DescriptorMatrix
from ._common import _aligned_space_metrics, _as_float64, _effective_dimension_metrics, _preprocess, _preprocess_reference_query

def sensitivity(runs: list[tuple[dict, DescriptorMatrix]], params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
    if len(runs) < 2:
        raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "parameter sensitivity requires at least two completed runs")
    descriptor_names = {str(run.get("descriptor_name")) for run, _sample in runs if run.get("descriptor_name")}
    if len(descriptor_names) > 1:
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "parameter sensitivity requires the same descriptor; use Compare for different descriptors",
            {"descriptors": sorted(descriptor_names)},
        )
    rows = []
    baseline_run, baseline_sample = runs[0]
    same_feature_dimensions = all(sample.n_features == baseline_sample.n_features for _run, sample in runs)
    if same_feature_dimensions:
        baseline_values, _baseline_query, baseline_warnings, _baseline_keep = _preprocess_reference_query(
            baseline_sample.values,
            baseline_sample.values,
            params,
            "standardized",
        )
    else:
        baseline_values, baseline_warnings, _baseline_keep = _preprocess(baseline_sample.values, params, "standardized")
    baseline_memory = baseline_run.get("memory_peak_bytes")
    try:
        baseline_memory = int(baseline_memory) if baseline_memory is not None else None
    except (TypeError, ValueError):
        baseline_memory = None
    for i, (run, sample) in enumerate(runs):
        if sample.n_samples != baseline_sample.n_samples or sample.sample_ids != baseline_sample.sample_ids:
            raise AppError(ANALYSIS_INPUT_INVALID, "parameter sensitivity requires aligned sample IDs")
        parameters = run.get("parameters_json") or "{}"
        try:
            parameter_value = parameters if isinstance(parameters, dict) else json.loads(parameters)
        except (TypeError, ValueError):
            parameter_value = {"raw": str(parameters)}
        if same_feature_dimensions:
            _baseline_again, values, run_warnings, _run_keep = _preprocess_reference_query(
                baseline_sample.values,
                sample.values,
                params,
                "standardized",
            )
            if i == 0:
                values = baseline_values
                run_warnings = []
        else:
            values, run_warnings, _run_keep = _preprocess(sample.values, params, "standardized")
        if i == 0:
            geometry = {
                "pairwise_distance_pearson": 1.0,
                "pairwise_distance_spearman": 1.0,
                "neighbor_overlap": 1.0,
                "pca_topology_error": 0.0,
                "clustering_stability": 1.0,
            }
        else:
            geometry, _arrays = _aligned_space_metrics(baseline_values, values, params)
        mean_delta_norm = float(np.linalg.norm(values.mean(axis=0) - baseline_values.mean(axis=0))) if values.shape[1] == baseline_values.shape[1] else None
        effective_dimension, thresholds = _effective_dimension_metrics(values)
        runtime = None
        try:
            if run.get("started_at") and run.get("finished_at"):
                runtime = (datetime.fromisoformat(run["finished_at"]) - datetime.fromisoformat(run["started_at"])).total_seconds()
        except (TypeError, ValueError):
            runtime = None
        memory_peak = run.get("memory_peak_bytes")
        try:
            memory_peak = int(memory_peak) if memory_peak is not None else None
        except (TypeError, ValueError):
            memory_peak = None
        memory_delta = memory_peak - baseline_memory if memory_peak is not None and baseline_memory is not None else None
        rows.append({
            "run_id": run["id"],
            "parameters": parameter_value,
            "feature_count": int(sample.n_features),
            "effective_dimension": effective_dimension,
            "components_95": thresholds["0.95"],
            "runtime_seconds": runtime,
            "memory_peak_bytes": memory_peak,
            "memory_delta_bytes": memory_delta,
            "mean_delta_norm": mean_delta_norm,
            **geometry,
            "warnings": list(dict.fromkeys(run_warnings)),
        })
        if progress:
            progress((i + 1) / len(runs), "comparing completed runs")
    return {
        "arrays": {
            "pairwise_distance_pearson": np.asarray([row["pairwise_distance_pearson"] for row in rows], dtype=np.float64),
            "neighbor_overlap": np.asarray([row["neighbor_overlap"] for row in rows], dtype=np.float64),
            "effective_dimension": np.asarray([row["effective_dimension"] for row in rows], dtype=np.float64),
            "memory_peak_bytes": np.asarray([row["memory_peak_bytes"] if row["memory_peak_bytes"] is not None else np.nan for row in rows], dtype=np.float64),
        },
        "preview": {
            "kind": "sensitivity",
            "runs": rows,
            "baseline_run_id": baseline_run["id"],
            "memory_metric": "peak process RSS during descriptor compute",
            "memory_available": any(row["memory_peak_bytes"] is not None for row in rows),
        },
        "warnings": list(dict.fromkeys(baseline_warnings)),
    }


def perturbation_sensitivity(
        baseline: DescriptorMatrix,
        perturbations: list[tuple[float, DescriptorMatrix]],
        params: dict,
        progress: Callable[[float, str], None] | None = None,
    ) -> dict:
        """Summarize descriptor response to physically perturbed structures.

        The service owns structure generation and descriptor recomputation.  At
        this layer the response is deliberately independent of any particular
        descriptor: it compares each recomputed matrix with the stored baseline
        using the baseline feature scaling and keeps a per-sample response curve.
        """
        if not perturbations:
            raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "at least one structural perturbation is required")
        base = _as_float64(baseline.values)
        mode = params.get("preprocess", "standardized")
        if mode not in ("raw", "center", "standardized"):
            raise AppError(ANALYSIS_INPUT_INVALID, "preprocess must be raw, center, or standardized")
        means = base.mean(axis=0)
        scales = base.std(axis=0)
        # A feature that is constant in the baseline can still respond to a
        # perturbation.  Keep it with unit scale instead of dropping the
        # baseline-to-perturbed displacement from the response.
        keep = np.ones(base.shape[1], dtype=bool)
        warnings = list(baseline.warnings)
        constant = scales <= np.finfo(np.float64).eps
        if bool(constant.any()) and mode != "raw":
            warnings.append(f"retained {int(constant.sum())} zero-variance baseline feature(s) with unit scale")

        def transform(values: np.ndarray) -> np.ndarray:
            values = _as_float64(values)
            if values.shape != base.shape:
                raise AppError(
                    ANALYSIS_INPUT_INVALID,
                    "perturbed descriptor shape does not match the baseline",
                    {"baseline": list(base.shape), "perturbed": list(values.shape)},
                )
            used = values[:, keep]
            if mode == "raw":
                return used
            centered = used - means[keep]
            if mode == "center":
                return centered
            return centered / np.where(scales[keep] > np.finfo(np.float64).eps, scales[keep], 1.0)

        base_used = transform(base)
        metric = str(params.get("metric") or "euclidean")
        if metric not in ("euclidean", "cosine", "manhattan"):
            raise AppError(ANALYSIS_INPUT_INVALID, "perturbation metric must be euclidean, cosine, or manhattan")
        amplitudes: list[float] = []
        response_rows: list[np.ndarray] = []
        for index, (amplitude, perturbed) in enumerate(perturbations):
            if perturbed.n_samples != baseline.n_samples or perturbed.sample_ids != baseline.sample_ids:
                raise AppError(ANALYSIS_INPUT_INVALID, "perturbed structures must preserve baseline sample IDs")
            amplitude = float(amplitude)
            if not np.isfinite(amplitude) or amplitude < 0:
                raise AppError(ANALYSIS_INPUT_INVALID, "perturbation amplitudes must be finite and non-negative")
            target = transform(perturbed.values)
            delta = target - base_used
            if metric == "euclidean":
                response = np.linalg.norm(delta, axis=1)
            elif metric == "manhattan":
                response = np.abs(delta).sum(axis=1)
            else:
                base_norm = np.linalg.norm(base_used, axis=1)
                target_norm = np.linalg.norm(target, axis=1)
                cosine = np.sum(base_used * target, axis=1) / np.maximum(base_norm * target_norm, 1e-15)
                response = np.maximum(1.0 - cosine, 0.0)
            amplitudes.append(amplitude)
            response_rows.append(response.astype(np.float64))
            if progress:
                progress((index + 1) / len(perturbations), "summarizing perturbation response")
        order = np.argsort(np.asarray(amplitudes), kind="stable")
        amplitude_array = np.asarray(amplitudes, dtype=np.float64)[order]
        response_matrix = np.vstack(response_rows).astype(np.float64)[order]
        mean_response = response_matrix.mean(axis=1)
        return {
            "arrays": {
                "amplitudes": amplitude_array,
                "mean_response": mean_response,
                "median_response": np.median(response_matrix, axis=1),
                "p95_response": np.quantile(response_matrix, 0.95, axis=1),
                "max_response": response_matrix.max(axis=1),
                "response_matrix": response_matrix,
                "sample_indices": np.arange(base.shape[0], dtype=np.int64),
            },
            "preview": {
                "kind": "perturbation_sensitivity",
                "perturbation": str(params.get("perturbation") or "jitter"),
                "metric": metric,
                "preprocess": mode,
                "amplitudes": amplitude_array.tolist(),
                "sample_count": int(base.shape[0]),
                "curve_count": int(amplitude_array.size),
                "response_unit": "scaled descriptor distance" if mode == "standardized" else "descriptor distance",
                "baseline_included": bool(np.any(np.isclose(amplitude_array, 0.0))),
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }



class Sensitivity:
    name = "sensitivity"
    category = "sensitivity"

    def run(self, runs, params: dict, progress=None) -> dict:
        return sensitivity(runs, params, progress)


class PerturbationSensitivity:
    name = "perturbation_sensitivity"
    category = "perturbation"

    def run(self, baseline, perturbations, params: dict, progress=None) -> dict:
        return perturbation_sensitivity(baseline, perturbations, params, progress)
