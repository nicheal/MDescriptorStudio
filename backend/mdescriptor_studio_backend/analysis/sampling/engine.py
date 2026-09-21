"""Sampling algorithm plugin."""

from __future__ import annotations

from typing import Callable

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, AppError
from ..models import DescriptorMatrix
from ..algorithms._common import _as_float64, _float_param, _int_param, _safe_import, _seed, _visual_pca, _visual_pca_components

from .preprocessing import SCALING_MODES
from .preprocessing import apply_scaling
from .preprocessing import fit_scaling
from .blocks import FeatureBlock
from .blocks import combine_feature_blocks
from .fps import GroupedFPSResult
from .fps import coverage_statistics
from .fps import farthest_point_sampling
from .fps import grouped_farthest_point_sampling

def sampling(samples: DescriptorMatrix, params: dict, algorithm: str, progress: Callable[[float, str], None] | None = None, existing: DescriptorMatrix | None = None, group_labels: np.ndarray | None = None, blocks: list[FeatureBlock] | None = None, existing_blocks: list[FeatureBlock] | None = None) -> dict:
    x = _as_float64(samples.values)
    n = x.shape[0]
    requested = _int_param(params, "n_samples", 1000, 1)
    target = min(requested, n)
    algorithm = algorithm.lower().replace("-", "_")
    rng = np.random.default_rng(_seed(params))
    warnings: list[str] = []
    scaling_mode = str(params.get("scaling") or "robust")
    if scaling_mode not in SCALING_MODES:
        raise AppError(ANALYSIS_INPUT_INVALID, "scaling must be raw, standardized, or robust")
    # The space a distance measure is taken in.  Algorithms that never compare
    # two samples keep the raw matrix; the ones that do scale it first, so both
    # of them partition or spread over the same geometry.
    space = x
    space_claim: dict = {}

    def scaled_space() -> np.ndarray:
        """`x` in the feature space the distance-based algorithms share."""
        try:
            scaling, scale_warnings = fit_scaling(x, scaling_mode)
        except ValueError as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, str(exc)) from exc
        warnings.extend(scale_warnings)
        return apply_scaling(scaling, x)

    def choose_grouped(labels: np.ndarray) -> np.ndarray:
        """Choose exactly ``target`` rows while preserving group coverage."""
        groups = np.unique(labels)
        members = [np.flatnonzero(labels == group) for group in groups]
        sizes = np.asarray([group.size for group in members], dtype=np.int64)
        ideal = target * sizes.astype(np.float64) / max(n, 1)
        counts = np.floor(ideal).astype(np.int64)
        # When the target can cover every group, keep at least one row per
        # group.  For a smaller target, the largest-remainder fill below
        # selects exactly the requested number of groups.
        if target >= len(groups):
            counts = np.maximum(counts, 1)
        counts = np.minimum(counts, sizes)
        while int(counts.sum()) < target:
            candidates = np.flatnonzero(counts < sizes)
            if not candidates.size:
                break
            residual = ideal[candidates] - counts[candidates]
            counts[int(candidates[int(np.argmax(residual))])] += 1
        while int(counts.sum()) > target:
            minimum = 1 if target >= len(groups) else 0
            candidates = np.flatnonzero(counts > minimum)
            if not candidates.size:
                break
            residual = ideal[candidates] - counts[candidates]
            counts[int(candidates[int(np.argmin(residual))])] -= 1
        selected: list[int] = []
        for group_members, count in zip(members, counts.tolist()):
            if count:
                selected.extend(rng.choice(group_members, size=count, replace=False).tolist())
        return np.asarray(sorted(selected), dtype=np.int64)

    if algorithm == "random":
        selected = np.sort(rng.choice(n, size=target, replace=False))
    elif algorithm in ("fps", "farthest_point"):
        strategy = str(params.get("strategy") or "global")
        if strategy not in ("global", "grouped"):
            raise AppError(ANALYSIS_INPUT_INVALID, "strategy must be global or grouped")
        if strategy == "grouped" and (group_labels is None or len(group_labels) != n):
            raise AppError(ANALYSIS_INPUT_INVALID, "grouped FPS requires one element-set label per sample")
        target_coverage = params.get("target_coverage")
        if target_coverage is None or target_coverage == "":
            target_coverage = None
        else:
            target_coverage = _float_param(params, "target_coverage", 0.95, 0.0, 1.0)
            if not 0.0 < target_coverage < 1.0:
                raise AppError(ANALYSIS_INPUT_INVALID, "target_coverage must be strictly between 0 and 1")
        layout: list[dict] | None = None
        try:
            min_distance = _float_param(params, "min_distance", 0.0, 0.0)
            if blocks:
                # Composite space: each block is scaled on its own and
                # weighted by 1/√D before concatenation.  The transform is
                # fitted on the existing set when warm-starting, exactly as
                # in the single-matrix path, so distances stay comparable.
                space_obj, reference_obj = combine_feature_blocks(
                    blocks, scaling=scaling_mode, reference=existing_blocks
                )
                space = space_obj.values
                existing_space = reference_obj.values if reference_obj is not None else None
                warnings.extend(space_obj.warnings)
                layout = space_obj.layout
            else:
                fit_on = _as_float64(existing.values) if existing is not None else x
                scaling, scale_warnings = fit_scaling(fit_on, scaling_mode)
                warnings.extend(scale_warnings)
                space = apply_scaling(scaling, x)
                existing_space = apply_scaling(scaling, fit_on) if existing is not None else None
            run = grouped_farthest_point_sampling if strategy == "grouped" else farthest_point_sampling
            run_kwargs = {"groups": group_labels} if strategy == "grouped" else {}
            result = run(
                space,
                n_samples=requested,
                min_distance=min_distance,
                initial=params.get("initialization", "center"),
                selected_features=existing_space,
                target_coverage=target_coverage,
                seed=_seed(params),
                progress=progress,
                **run_kwargs,
            )
        except ValueError as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, str(exc)) from exc
        # The projection visualises the space the sampling actually ran in;
        # its explained-variance ratio keeps that claim honest on screen.
        coords, pc_ratio = _visual_pca_components(space)
        residuals = coverage_statistics(result.nearest_distances)
        order = np.argsort(result.indices, kind="stable")
        initialization = params.get("initialization", "center")
        preview = {
            "kind": "sampling",
            "algorithm": "fps",
            "strategy": strategy,
            "selected_count": int(result.n_selected),
            "requested_count": int(requested),
            "n_candidates": int(n),
            "stop_reason": result.stopped_by,
            "coverage_radius": result.coverage_radius,
            "coverage_r2": result.coverage_r2,
            "total_spread": result.total_spread,
            "mean_residual": residuals["mean"],
            "p50_residual": residuals["p50"],
            "p90_residual": residuals["p90"],
            "p95_residual": residuals["p95"],
            "p99_residual": residuals["p99"],
            "max_residual": residuals["max"],
            "scaling": scaling_mode,
            "initialization": str(initialization).lower() if isinstance(initialization, str) else int(initialization),
            "min_distance": float(min_distance),
            "target_coverage": target_coverage,
            "warm_start": existing is not None,
            "sampling_dimension": int(space.shape[1]),
            "sampling_space": "composite" if blocks else "descriptor",
            "pc_explained_variance": [float(value) for value in pc_ratio],
        }
        if layout is not None:
            preview["blocks"] = layout
        if isinstance(result, GroupedFPSResult):
            preview["allocation"] = [
                {"group": name, "structures": int(size), "quota": int(share)}
                for name, size, share in zip(result.group_names, result.group_sizes, result.group_quota)
            ]
        return {
            "arrays": {
                # Sorted for the sample set, and the selection distance travels
                # with its own sample: `FPSResult` pairs `indices[k]` with
                # `selection_distances[k]`, and sorting only the first made the
                # two stored .npy columns describe different orders (pass 5, 5-C8).
                "selected_indices": result.indices[order].astype(np.int64),
                "coords": coords,
                "nearest_distances": result.nearest_distances,
                "selection_distances": result.selection_distances[order],
                "coverage_radius_curve": result.coverage_radius_curve,
                "coverage_mean_curve": result.coverage_mean_curve,
                "coverage_r2_curve": result.coverage_r2_curve,
            },
            "preview": preview,
            "warnings": warnings,
        }
    elif algorithm == "stratified":
        labels = samples.elements
        if labels is None or len(labels) != n:
            # Structure-level stratification has a stable frame fallback.
            labels = samples.frame
        selected = choose_grouped(np.asarray(labels))
    elif algorithm in ("cluster", "cluster_representative"):
        # KMeans assigns every sample to its nearest centre and then keeps the
        # member closest to each centre, so it is distance-based exactly like
        # FPS and has to measure in the same space: on a mixed-unit matrix
        # (energy in eV, virial, volume in Å³) the raw values hand the whole
        # choice to the widest column.
        space = scaled_space()
        space_claim = {"scaling": scaling_mode}
        k = _int_param(params, "n_clusters", min(6, max(2, target)), 2)
        cls = _safe_import("sklearn.cluster", "scikit-learn").KMeans
        model = cls(n_clusters=min(k, n), random_state=_seed(params), n_init=10).fit(space)
        selected_list = []
        for label, center in enumerate(model.cluster_centers_):
            # KMeans can leave a cluster empty, so the label a centre owns is
            # its own index, not however many representatives exist so far.
            members = np.flatnonzero(model.labels_ == label)
            if len(members):
                distances = ((space[members] - center) ** 2).sum(axis=1)
                selected_list.append(int(members[int(np.argmin(distances))]))
        selected = np.asarray(selected_list, dtype=np.int64)
        if selected.size < target:
            remaining = np.setdiff1d(np.arange(n), selected, assume_unique=False)
            selected = np.concatenate([selected, remaining[: target - selected.size]])
        selected = np.sort(selected[:target])
    elif algorithm in ("per_element", "element"):
        labels = samples.elements
        if labels is None or len(labels) != n:
            raise AppError(ANALYSIS_INPUT_INVALID, "per-element sampling requires atom-level element metadata")
        selected = choose_grouped(np.asarray(labels))
    else:
        raise AppError(ANALYSIS_INPUT_INVALID, f"unsupported sampling algorithm: {algorithm}")
    if progress:
        progress(1.0, f"{algorithm} sampling complete")
    return {"arrays": {"selected_indices": selected.astype(np.int64), "coords": _visual_pca(space)}, "preview": {"kind": "sampling", "algorithm": algorithm, "selected_count": int(selected.size), "requested_count": int(target), **space_claim}, "warnings": warnings}



class Sampling:
    name = "sampling"
    category = "sampling"

    def run(self, data, params: dict, algorithm: str, progress=None, existing=None, group_labels=None, blocks=None, existing_blocks=None) -> dict:
        return sampling(data, params, algorithm, progress, existing=existing, group_labels=group_labels, blocks=blocks, existing_blocks=existing_blocks)
