"""Numerical analysis engine used by :mod:`services.analysis_service`.

All calculations operate on float64 arrays.  Large arrays are returned to the
service as named artifacts rather than being serialised into an IPC response.
The methods below intentionally expose a small, stable parameter surface; the
Studio API should not become a thin and version-fragile wrapper around every
scikit-learn keyword.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from itertools import product
from typing import Any, Callable

import numpy as np

from ..errors import (
    ANALYSIS_DEPENDENCY_MISSING,
    ANALYSIS_INPUT_INVALID,
    ANALYSIS_INSUFFICIENT_SAMPLES,
    AppError,
)
from .sampling import (
    SCALING_MODES,
    FeatureBlock,
    GroupedFPSResult,
    apply_scaling,
    combine_feature_blocks,
    coverage_statistics,
    farthest_point_sampling,
    fit_scaling,
    grouped_farthest_point_sampling,
)
from .umap_numpy import fit_umap


# Deferred-warmup gate. backend.ready is emitted before the background warmup
# thread imports sklearn/hdbscan, so _safe_import waits until that import
# pass has finished: the heavy modules are still imported exactly once and
# never race a request, but they no longer block startup. The event starts set
# so direct users (tests, scripts) are unaffected.
_warmup_gate = threading.Event()
_warmup_gate.set()


def arm_analysis_warmup_gate() -> None:
    """Switch _safe_import into wait-for-warmup mode before warmup starts."""
    _warmup_gate.clear()


@dataclass
class SampleMatrix:
    """A matrix plus the identity needed to navigate back to Explore."""

    values: np.ndarray
    frame: np.ndarray
    row: np.ndarray | None = None
    sample_ids: list[str] = field(default_factory=list)
    elements: np.ndarray | None = None
    mode: str = "structure"
    warnings: list[str] = field(default_factory=list)
    properties: dict[str, np.ndarray] = field(default_factory=dict)
    # Optional atom geometry used only by local-environment analyses.  These
    # fields stay aligned with ``values`` and are absent for structure-level
    # descriptor results.
    positions: np.ndarray | None = None
    cells: np.ndarray | None = None
    pbc: np.ndarray | None = None

    @property
    def n_samples(self) -> int:
        return int(self.values.shape[0])

    @property
    def n_features(self) -> int:
        return int(self.values.shape[1])


def _as_float64(values: Any, *, allow_nonfinite: bool = False) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 1:
        array = array.reshape(-1, 1)
    if array.ndim != 2:
        raise AppError(ANALYSIS_INPUT_INVALID, "analysis input must be a 2D matrix")
    if array.shape[0] == 0 or array.shape[1] == 0:
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "analysis input cannot be empty",
            {"shape": list(array.shape)},
        )
    # Do not silently drop bad rows for ordinary analyses: a NaN/Inf descriptor
    # usually indicates a broken upstream calculation and hiding it would
    # destroy reproducibility. Feature-variance is the one diagnostic that
    # deliberately handles them per column and reports the invalid counts.
    finite = np.isfinite(array)
    if not allow_nonfinite and not bool(finite.all()):
        bad = np.argwhere(~finite)
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "analysis input contains NaN or Inf",
            {"bad_values": int((~finite).sum()), "first_index": bad[0].tolist()},
        )
    return array


def _int_param(params: dict, name: str, default: int, minimum: int = 1) -> int:
    value = params.get(name, default)
    if isinstance(value, bool):
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be an integer")
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be an integer") from exc
    if value < minimum:
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be >= {minimum}")
    return value


def _float_param(params: dict, name: str, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    value = params.get(name, default)
    if isinstance(value, bool):
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be a finite number")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be a finite number") from exc
    if not np.isfinite(value):
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise AppError(ANALYSIS_INPUT_INVALID, f"{name} must be <= {maximum}")
    return value


def _seed(params: dict) -> int:
    return _int_param(params, "seed", 42, 0)


def _preprocess(x: np.ndarray, params: dict, default: str) -> tuple[np.ndarray, list[str], np.ndarray]:
    x = _as_float64(x)
    mode = params.get("preprocess", default)
    if mode not in ("raw", "center", "standardized"):
        raise AppError(ANALYSIS_INPUT_INVALID, "preprocess must be raw, center, or standardized")
    warnings: list[str] = []
    means = x.mean(axis=0)
    centered = x - means
    variances = centered.var(axis=0)
    scale = np.sqrt(variances)
    keep = scale > np.finfo(np.float64).eps
    if not bool(keep.all()):
        warnings.append(f"ignored {int((~keep).sum())} zero-variance feature(s)")
    # Keeping at least one column makes constant descriptors report a useful
    # structured error in algorithms that need a non-empty feature space.
    if not bool(keep.any()):
        keep = np.ones(x.shape[1], dtype=bool)
        scale = np.ones(x.shape[1], dtype=np.float64)
    if mode == "raw":
        return x[:, keep], warnings, keep
    if mode == "center":
        return centered[:, keep], warnings, keep
    return centered[:, keep] / np.where(scale[keep] > 0, scale[keep], 1.0), warnings, keep


def _check_samples(x: np.ndarray, minimum: int = 2) -> None:
    if x.shape[0] < minimum:
        raise AppError(
            ANALYSIS_INSUFFICIENT_SAMPLES,
            f"at least {minimum} samples are required",
            {"samples": int(x.shape[0]), "minimum": minimum},
        )


def _safe_import(module: str, package: str | None = None):
    try:
        # Hold heavy-module imports until the background warmup pass is done
        # (no-op once warm; see module docstring of the warmup gate).
        _warmup_gate.wait()
        # A non-wildcard fromlist returns the requested module without walking
        # package ``__all__``. sklearn.model_selection deliberately exposes
        # experimental names there that raise during wildcard import.
        return __import__(module, fromlist=["__name__"])
    except ImportError as exc:
        label = package or module
        raise AppError(
            ANALYSIS_DEPENDENCY_MISSING,
            f"analysis dependency {label!r} is not installed",
            {"dependency": label},
        ) from exc


def _nearest_distances(x: np.ndarray, k: int, metric: str = "euclidean") -> tuple[np.ndarray, np.ndarray]:
    neighbors = _safe_import("sklearn.neighbors", "scikit-learn").NearestNeighbors
    _check_samples(x, 2)
    k_eff = min(k + 1, x.shape[0])
    model = neighbors(n_neighbors=k_eff, metric=metric, n_jobs=1)
    distances, indices = model.fit(x).kneighbors(x)
    return indices[:, 1:], distances[:, 1:]


def _bounded_indices(count: int, limit: int) -> np.ndarray:
    """Return deterministic, order-preserving indices for bounded visual artifacts."""
    if count <= limit:
        return np.arange(count, dtype=np.int64)
    return np.linspace(0, count - 1, limit, dtype=np.int64)


def _visual_pca_components(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two-dimensional visual PCA plus the explained-variance ratio of both axes.

    The ratio is what lets a panel state how much descriptor information the
    two drawn axes still carry: a visual jump in a 25%-variance panel is not on
    its own evidence of a descriptor-space jump.
    """
    x = _as_float64(x)
    if x.shape[0] == 1:
        return np.zeros((1, 2), dtype=np.float64), np.zeros(2, dtype=np.float64)
    centered = x - x.mean(axis=0)
    components = min(2, centered.shape[0], centered.shape[1])
    if components == 0:
        return np.zeros((x.shape[0], 2), dtype=np.float64), np.zeros(2, dtype=np.float64)
    if min(centered.shape) > 4:
        pca = _safe_import("sklearn.decomposition", "scikit-learn").PCA(
            n_components=components,
            svd_solver="randomized",
            random_state=42,
        )
        coords = np.asarray(pca.fit_transform(centered), dtype=np.float64)
        ratio = np.asarray(pca.explained_variance_ratio_, dtype=np.float64)
    else:
        _u, singular, vt = np.linalg.svd(centered, full_matrices=False)
        coords = centered @ vt[:components].T
        total = float(np.sum(singular * singular))
        ratio = (singular[:components] ** 2) / total if total > 0 else np.zeros(components, dtype=np.float64)
    if components < 2:
        coords = np.pad(coords, ((0, 0), (0, 2 - components)))
        ratio = np.pad(ratio, (0, 2 - components))
    return coords.astype(np.float64, copy=False), np.asarray(ratio[:2], dtype=np.float64)


def _visual_pca(x: np.ndarray) -> np.ndarray:
    """Fast deterministic two-dimensional PCA used only as a visual companion."""
    return _visual_pca_components(x)[0]


_TRAJECTORY_EVENT_METHODS = ("mad", "zscore", "percentile")


def _trajectory_threshold(steps: np.ndarray, params: dict) -> tuple[str, float, float, dict[str, float]]:
    """Event threshold over descriptor-space step distances.

    ``mad`` is the robust default (median + k * 1.4826 * MAD) because one large
    structural jump inflates the standard deviation and hides later events.
    ``zscore`` is offered for comparison and ``percentile`` reproduces a fixed
    top fraction of frames.
    """
    method = str(params.get("event_method") or "mad").lower()
    if method not in _TRAJECTORY_EVENT_METHODS:
        raise AppError(ANALYSIS_INPUT_INVALID, "event_method must be mad, zscore, or percentile")
    default_sensitivity = 1.0 if method == "percentile" else 3.0
    sensitivity = _float_param(params, "event_sensitivity", default_sensitivity, np.finfo(np.float64).eps)
    median = float(np.median(steps))
    mad = float(np.median(np.abs(steps - median)))
    mean = float(steps.mean())
    std = float(steps.std())
    robust_sigma = 1.4826 * mad
    if method == "percentile":
        if sensitivity >= 50.0:
            raise AppError(ANALYSIS_INPUT_INVALID, "event_sensitivity must be below 50 for percentile detection")
        threshold = float(np.quantile(steps, 1.0 - sensitivity / 100.0))
    elif method == "mad":
        threshold = median + sensitivity * robust_sigma
    else:
        threshold = mean + sensitivity * std
    return method, sensitivity, threshold, {
        "median": median,
        "mad": mad,
        "robust_sigma": robust_sigma,
        "mean": mean,
        "std": std,
    }


def _effective_dimension_metrics(x: np.ndarray) -> tuple[float, dict[str, int]]:
    centered = _as_float64(x) - np.asarray(x, dtype=np.float64).mean(axis=0)
    singular = np.linalg.svd(centered, compute_uv=False, full_matrices=False)
    eigen = singular * singular
    total = float(eigen.sum())
    if total <= 0:
        return 0.0, {"0.9": 0, "0.95": 0, "0.99": 0}
    normalized = eigen / total
    participation = float(1.0 / np.sum(normalized * normalized))
    cumulative = np.cumsum(normalized)
    thresholds = {str(t): int(np.searchsorted(cumulative, t) + 1) for t in (0.9, 0.95, 0.99)}
    return participation, thresholds


def _safe_correlation(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    if a.size != b.size or a.size < 2:
        return 0.0
    if float(a.std()) <= np.finfo(np.float64).eps or float(b.std()) <= np.finfo(np.float64).eps:
        return 1.0 if np.allclose(a, b) else 0.0
    value = float(np.corrcoef(a, b)[0, 1])
    return value if np.isfinite(value) else 0.0


def _rank_correlation(a: np.ndarray, b: np.ndarray) -> float:
    stats = _safe_import("scipy.stats", "scipy")
    value = float(stats.spearmanr(np.asarray(a), np.asarray(b)).statistic)
    return value if np.isfinite(value) else 0.0


def _pairwise_matrix(x: np.ndarray, metric: str) -> np.ndarray:
    if metric not in ("euclidean", "cosine", "manhattan"):
        raise AppError(ANALYSIS_INPUT_INVALID, "metric must be euclidean, cosine, or manhattan")
    fn = _safe_import("sklearn.metrics", "scikit-learn").pairwise_distances
    return np.asarray(fn(_as_float64(x), metric=metric, n_jobs=1), dtype=np.float64)


def _clustered_feature_order(correlation: np.ndarray, feature_indices: np.ndarray) -> np.ndarray:
    """Return a deterministic order that groups features by ``1 - |r|``."""
    if correlation.shape[0] < 2:
        return feature_indices.copy()
    distance = 1.0 - np.clip(np.abs(correlation), 0.0, 1.0)
    np.fill_diagonal(distance, 0.0)
    try:
        hierarchy = _safe_import("scipy.cluster.hierarchy", "scipy")
        spatial_distance = _safe_import("scipy.spatial.distance", "scipy")
        condensed = spatial_distance.squareform(distance, checks=False)
        linkage = hierarchy.linkage(condensed, method="average", optimal_ordering=True)
        leaves = np.asarray(hierarchy.leaves_list(linkage), dtype=np.int64)
    except (AppError, ValueError):
        # Keep correlation analysis usable in reduced runtimes without SciPy.
        strength = np.sum(np.abs(correlation), axis=1) - 1.0
        leaves = np.argsort(-strength, kind="stable").astype(np.int64)
    return feature_indices[leaves]


def _connected_component_count(edges: np.ndarray, size: int) -> int:
    """Count connected components represented by local feature-index edges."""
    if edges.size == 0:
        return 0
    parent = np.arange(size, dtype=np.int64)

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = int(parent[value])
        return value

    for left, right in edges.tolist():
        left_root = find(int(left))
        right_root = find(int(right))
        if left_root != right_root:
            parent[right_root] = left_root
    involved = np.unique(edges)
    return len({find(int(index)) for index in involved.tolist()})


def _preprocess_reference_query(reference: np.ndarray, query: np.ndarray, params: dict, default: str = "raw") -> tuple[np.ndarray, np.ndarray, list[str], np.ndarray]:
    reference = _as_float64(reference)
    query = _as_float64(query)
    if reference.shape[1] != query.shape[1]:
        raise AppError(ANALYSIS_INPUT_INVALID, "reference and query feature counts do not match")
    mode = params.get("preprocess", default)
    if mode not in ("raw", "center", "standardized"):
        raise AppError(ANALYSIS_INPUT_INVALID, "preprocess must be raw, center, or standardized")
    means = reference.mean(axis=0)
    centered_reference = reference - means
    scale = centered_reference.std(axis=0)
    # A feature that is constant in the reference set is still informative
    # when the query set moves away from that reference value.  Dropping it
    # would make raw coverage and OOD distances silently ignore an entire
    # direction.  For standardized cross-set work, retain the feature with a
    # unit scale so a reference-zero/query-nonzero displacement remains
    # measurable.
    keep = np.ones(reference.shape[1], dtype=bool)
    constant = scale <= np.finfo(np.float64).eps
    warnings: list[str] = []
    if bool(constant.any()) and mode != "raw":
        warnings.append(f"retained {int(constant.sum())} reference-constant feature(s) with unit scale")
    if mode == "raw":
        return reference, query, warnings, keep
    centered_query = query - means
    if mode == "center":
        return centered_reference, centered_query, warnings, keep
    denominator = np.where(scale > np.finfo(np.float64).eps, scale, 1.0)
    return centered_reference / denominator, centered_query / denominator, warnings, keep


def _cross_nearest(reference: np.ndarray, query: np.ndarray, metric: str, query_chunk: int, reference_chunk: int, progress: Callable[[float, str], None] | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Bounded-memory nearest-reference search with source identities."""
    reference = _as_float64(reference)
    query = _as_float64(query)
    if metric not in ("euclidean", "cosine", "manhattan"):
        raise AppError(ANALYSIS_INPUT_INVALID, "cross-dataset metric must be euclidean, cosine, or manhattan")
    distances = np.empty(query.shape[0], dtype=np.float64)
    nearest = np.empty(query.shape[0], dtype=np.int64)
    for start in range(0, query.shape[0], query_chunk):
        stop = min(start + query_chunk, query.shape[0])
        query_block = query[start:stop]
        best = np.full(query_block.shape[0], np.inf, dtype=np.float64)
        best_index = np.full(query_block.shape[0], -1, dtype=np.int64)
        query_norm = np.linalg.norm(query_block, axis=1, keepdims=True) if metric == "cosine" else None
        for ref_start in range(0, reference.shape[0], reference_chunk):
            ref_block = reference[ref_start : ref_start + reference_chunk]
            if metric == "euclidean":
                # scipy's cdist subtracts coordinates directly. This preserves
                # exact zero for identical rows, unlike the cancellation-prone
                # ||a||² + ||b||² - 2a·b identity.
                matrix = _safe_import("scipy.spatial.distance", "scipy").cdist(
                    query_block,
                    ref_block,
                    metric="euclidean",
                )
            elif metric == "manhattan":
                matrix = _safe_import("scipy.spatial.distance", "scipy").cdist(
                    query_block,
                    ref_block,
                    metric="cityblock",
                )
            else:
                ref_norm = np.linalg.norm(ref_block, axis=1)
                similarity = (query_block @ ref_block.T) / np.maximum(query_norm * ref_norm[None, :], 1e-15)
                matrix = np.maximum(1.0 - similarity, 0.0)
            local = np.argmin(matrix, axis=1)
            local_distance = matrix[np.arange(matrix.shape[0]), local]
            improved = local_distance < best
            best[improved] = local_distance[improved]
            best_index[improved] = ref_start + local[improved]
        distances[start:stop] = best
        nearest[start:stop] = best_index
        if progress:
            progress(stop / max(query.shape[0], 1), "nearest-reference distances")
    return nearest, distances


def _cross_k_nearest(
    reference: np.ndarray,
    query: np.ndarray,
    metric: str,
    query_chunk: int,
    reference_chunk: int,
    k: int,
    progress: Callable[[float, str], None] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Bounded-memory k-nearest reference search.

    The acquisition layer uses the complete local neighborhood rather than a
    single nearest point to estimate descriptor-space extrapolation.  Keeping
    only the best ``k`` values per query preserves the memory bound of the
    existing cross-dataset search even for large reference sets.
    """
    reference = _as_float64(reference)
    query = _as_float64(query)
    if metric not in ("euclidean", "cosine", "manhattan"):
        raise AppError(ANALYSIS_INPUT_INVALID, "cross-dataset metric must be euclidean, cosine, or manhattan")
    if reference.shape[0] == 0:
        raise AppError(ANALYSIS_INPUT_INVALID, "reference descriptor set cannot be empty")
    k_eff = min(max(int(k), 1), reference.shape[0])
    distances = np.full((query.shape[0], k_eff), np.inf, dtype=np.float64)
    indices = np.full((query.shape[0], k_eff), -1, dtype=np.int64)
    for start in range(0, query.shape[0], query_chunk):
        stop = min(start + query_chunk, query.shape[0])
        query_block = query[start:stop]
        best_distances = np.full((query_block.shape[0], k_eff), np.inf, dtype=np.float64)
        best_indices = np.full((query_block.shape[0], k_eff), -1, dtype=np.int64)
        query_norm = np.linalg.norm(query_block, axis=1, keepdims=True) if metric == "cosine" else None
        for ref_start in range(0, reference.shape[0], reference_chunk):
            ref_block = reference[ref_start : ref_start + reference_chunk]
            if metric == "euclidean":
                block = _safe_import("scipy.spatial.distance", "scipy").cdist(query_block, ref_block, metric="euclidean")
            elif metric == "manhattan":
                block = _safe_import("scipy.spatial.distance", "scipy").cdist(query_block, ref_block, metric="cityblock")
            else:
                ref_norm = np.linalg.norm(ref_block, axis=1)
                similarity = (query_block @ ref_block.T) / np.maximum(query_norm * ref_norm[None, :], 1e-15)
                block = np.maximum(1.0 - similarity, 0.0)
            candidate_distances = np.concatenate([best_distances, block], axis=1)
            candidate_indices = np.concatenate([
                best_indices,
                np.broadcast_to(np.arange(ref_start, ref_start + ref_block.shape[0]), block.shape),
            ], axis=1)
            keep = np.argpartition(candidate_distances, k_eff - 1, axis=1)[:, :k_eff]
            row = np.arange(query_block.shape[0])[:, None]
            best_distances = candidate_distances[row, keep]
            best_indices = candidate_indices[row, keep]
            order = np.argsort(best_distances, axis=1, kind="stable")
            best_distances = np.take_along_axis(best_distances, order, axis=1)
            best_indices = np.take_along_axis(best_indices, order, axis=1)
        distances[start:stop] = best_distances
        indices[start:stop] = best_indices
        if progress:
            progress(stop / max(query.shape[0], 1), "nearest-reference neighborhoods")
    return indices, distances


def _local_neighbor_graph(samples: SampleMatrix, cutoff: float, max_neighbors: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Build a coordinate-aware local-environment graph.

    The returned CSR-like arrays use atom rows as vertices.  Periodic frames
    are queried against translated images.  Every periodic image contact is
    retained, including repeated source indices for small cells; the stored
    index still points to the original atom.  CSR rows are emitted in global
    atom order even when frame rows are interleaved.
    """
    if samples.positions is None:
        return (
            np.zeros(samples.n_samples, dtype=np.int64),
            np.zeros(samples.n_samples + 1, dtype=np.int64),
            np.zeros(0, dtype=np.int64),
            np.zeros(0, dtype=np.float64),
            ["atom coordinates are unavailable; coordination and neighbor shell were not computed"],
        )
    positions = np.asarray(samples.positions, dtype=np.float64)
    if positions.shape != (samples.n_samples, 3) or not np.isfinite(positions).all():
        return (
            np.zeros(samples.n_samples, dtype=np.int64),
            np.zeros(samples.n_samples + 1, dtype=np.int64),
            np.zeros(0, dtype=np.int64),
            np.zeros(0, dtype=np.float64),
            ["atom coordinates are invalid; coordination and neighbor shell were not computed"],
        )
    cKDTree = _safe_import("scipy.spatial", "scipy").cKDTree
    cells = np.asarray(samples.cells, dtype=np.float64) if samples.cells is not None else None
    pbc = np.asarray(samples.pbc, dtype=bool) if samples.pbc is not None else None
    row_indices: list[list[int]] = [[] for _ in range(samples.n_samples)]
    row_distances: list[list[float]] = [[] for _ in range(samples.n_samples)]
    offsets = np.zeros(samples.n_samples + 1, dtype=np.int64)
    coordination = np.zeros(samples.n_samples, dtype=np.int64)
    warnings: list[str] = []
    frame_values = np.asarray(samples.frame, dtype=np.int64)
    for frame in np.unique(frame_values).tolist():
        members = np.flatnonzero(frame_values == int(frame))
        frame_positions = positions[members]
        frame_cell = None
        frame_pbc = None
        if cells is not None and cells.ndim == 3 and cells.shape[0] == samples.n_samples:
            frame_cell = cells[members[0]]
        if pbc is not None and pbc.ndim == 2 and pbc.shape == (samples.n_samples, 3):
            frame_pbc = pbc[members[0]]
        periodic = bool(
            frame_cell is not None
            and frame_cell.shape == (3, 3)
            and frame_pbc is not None
            and frame_pbc.any()
            and abs(float(np.linalg.det(frame_cell))) > 1e-10
        )
        if periodic:
            periodic_axes = [axis for axis in range(3) if bool(frame_pbc[axis])]
            # Wrap periodic coordinates first so the finite image stencil is
            # valid for datasets whose coordinates have crossed a cell many
            # times.  The inverse-cell columns give a conservative number of
            # lattice images needed for the requested Cartesian cutoff.
            frame_inverse = np.linalg.inv(frame_cell)
            fractional = frame_positions @ frame_inverse
            fractional[:, periodic_axes] -= np.floor(fractional[:, periodic_axes])
            graph_positions = fractional @ frame_cell
            shift_limits = [
                max(1, int(np.ceil(cutoff * np.linalg.norm(frame_inverse[:, axis]))) + 1)
                for axis in periodic_axes
            ]
            shift_tuples = list(product(*[range(-limit, limit + 1) for limit in shift_limits]))
            shifts = []
            for compact_shift in shift_tuples:
                shift = np.zeros(3, dtype=np.float64)
                for axis, value in zip(periodic_axes, compact_shift):
                    shift[axis] = value
                shifts.append(shift)
            shifts_array = np.asarray(shifts, dtype=np.float64)
            translated = (graph_positions[None, :, :] + shifts_array[:, None, :] @ frame_cell).reshape(-1, 3)
            source_indices = np.tile(np.arange(members.size, dtype=np.int64), len(shifts))
            zero_shift_index = shift_tuples.index(tuple(0 for _ in periodic_axes))
            tree = cKDTree(translated)
        else:
            graph_positions = frame_positions
            translated = graph_positions
            source_indices = np.arange(members.size, dtype=np.int64)
            zero_shift_index = 0
            tree = cKDTree(frame_positions)

        for local_index, center in enumerate(graph_positions):
            candidates = tree.query_ball_point(center, cutoff)
            contacts: list[tuple[int, float, int]] = []
            for candidate in candidates:
                source = int(source_indices[candidate])
                shift_index = candidate // max(members.size, 1) if periodic else 0
                if source == local_index and shift_index == zero_shift_index:
                    continue
                distance = float(np.linalg.norm(translated[candidate] - center))
                if distance <= 1e-10:
                    continue
                # Do not collapse by ``source``: two different periodic image
                # contacts of the same atom are distinct neighbors in a small
                # unit cell (for example the six self-images of a cubic cell).
                contacts.append((source, distance, int(candidate)))
            ordered = sorted(contacts, key=lambda item: (item[1], item[0], item[2]))[:max_neighbors]
            global_index = int(members[local_index])
            coordination[global_index] = len(ordered)
            row_indices[global_index] = [int(members[source]) for source, _distance, _candidate in ordered]
            row_distances[global_index] = [float(distance) for _source, distance, _candidate in ordered]

    graph_indices: list[int] = []
    graph_distances: list[float] = []
    for global_index in range(samples.n_samples):
        offsets[global_index] = len(graph_indices)
        graph_indices.extend(row_indices[global_index])
        graph_distances.extend(row_distances[global_index])
    offsets[-1] = len(graph_indices)
    return coordination, offsets, np.asarray(graph_indices, dtype=np.int64), np.asarray(graph_distances, dtype=np.float64), warnings


def _joint_projection(reference: np.ndarray, query: np.ndarray, max_each: int) -> dict[str, np.ndarray]:
    ref_indices = _bounded_indices(reference.shape[0], max_each)
    query_indices = _bounded_indices(query.shape[0], max_each)
    stacked = np.vstack([reference[ref_indices], query[query_indices]])
    coords = _visual_pca(stacked)
    return {
        "projection_coords": coords,
        "projection_source": np.concatenate([
            np.zeros(ref_indices.size, dtype=np.int64),
            np.ones(query_indices.size, dtype=np.int64),
        ]),
        "projection_sample_indices": np.concatenate([ref_indices, query_indices]).astype(np.int64),
    }


def _aligned_space_metrics(left: np.ndarray, right: np.ndarray, params: dict) -> tuple[dict[str, float], dict[str, np.ndarray]]:
    """Compare two aligned descriptor spaces without requiring equal feature counts."""
    left = _as_float64(left)
    right = _as_float64(right)
    if left.shape[0] != right.shape[0]:
        raise AppError(ANALYSIS_INPUT_INVALID, "aligned descriptor spaces require matching sample counts")
    limit = min(_int_param(params, "max_samples", 600, 2), 2_000)
    sample_indices = _bounded_indices(left.shape[0], limit)
    a = left[sample_indices]
    b = right[sample_indices]
    left_matrix = _pairwise_matrix(a, "euclidean")
    right_matrix = _pairwise_matrix(b, "euclidean")
    triangle = np.triu_indices(sample_indices.size, 1)
    left_pairs = left_matrix[triangle]
    right_pairs = right_matrix[triangle]
    pearson = _safe_correlation(left_pairs, right_pairs)
    spearman = _rank_correlation(left_pairs, right_pairs)

    k = min(_int_param(params, "k", 10, 1), max(sample_indices.size - 1, 1))
    left_neighbors = np.argsort(left_matrix, axis=1)[:, 1 : k + 1]
    right_neighbors = np.argsort(right_matrix, axis=1)[:, 1 : k + 1]
    overlap = np.asarray(
        [len(set(a_row.tolist()) & set(b_row.tolist())) / max(k, 1) for a_row, b_row in zip(left_neighbors, right_neighbors)],
        dtype=np.float64,
    )

    left_coords = _visual_pca(a)
    right_coords = _visual_pca(b)
    left_norm = left_coords / max(float(np.linalg.norm(left_coords)), 1e-15)
    right_norm = right_coords / max(float(np.linalg.norm(right_coords)), 1e-15)
    u, _s, vt = np.linalg.svd(right_norm.T @ left_norm, full_matrices=False)
    aligned_right = right_norm @ (u @ vt)
    topology_error = float(np.sqrt(np.mean((left_norm - aligned_right) ** 2)))

    cluster_count = min(_int_param(params, "n_clusters", 6, 2), sample_indices.size)
    kmeans = _safe_import("sklearn.cluster", "scikit-learn").KMeans
    left_labels = kmeans(n_clusters=cluster_count, random_state=_seed(params), n_init=10).fit_predict(a)
    right_labels = kmeans(n_clusters=cluster_count, random_state=_seed(params), n_init=10).fit_predict(b)
    cluster_stability = float(
        _safe_import("sklearn.metrics", "scikit-learn").adjusted_rand_score(left_labels, right_labels)
    )

    pair_limit = min(_int_param(params, "display_pairs", 50_000, 100), 100_000)
    pair_indices = _bounded_indices(left_pairs.size, pair_limit)
    metrics = {
        "pairwise_distance_pearson": pearson,
        "pairwise_distance_spearman": spearman,
        "neighbor_overlap": float(overlap.mean()),
        "pca_topology_error": topology_error,
        "clustering_stability": cluster_stability,
    }
    arrays = {
        "sample_indices": sample_indices,
        "left_coords": left_coords,
        "right_coords": right_coords,
        "left_pair_distances": left_pairs[pair_indices].astype(np.float64),
        "right_pair_distances": right_pairs[pair_indices].astype(np.float64),
        "neighbor_overlap": overlap,
    }
    return metrics, arrays


class AnalysisEngine:
    """Pure numerical methods with deterministic defaults."""

    @staticmethod
    def warmup() -> dict[str, bool]:
        """Import optional numeric backends on the background warmup thread.

        Native sklearn and hdbscan dependencies can acquire process import
        locks or initialize DLL state during their first import. Doing that
        work in a single dedicated thread before any request touches these
        modules (gated via ``_safe_import``) keeps a Windows sidecar from
        hanging at ``loading descriptor results`` on its first analysis
        request while no longer blocking ``backend.ready``. Missing optional
        packages are reported and are still converted to
        ANALYSIS_DEPENDENCY_MISSING when selected.
        """
        import importlib

        try:
            availability: dict[str, bool] = {}
            for name in ("sklearn", "hdbscan"):
                try:
                    importlib.import_module(name)
                except ImportError:
                    availability[name] = False
                else:
                    availability[name] = True
            return availability
        finally:
            _warmup_gate.set()

    @staticmethod
    def pca(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        _check_samples(samples.values, 2)
        x, warnings, keep = _preprocess(samples.values, params, "center")
        if progress:
            progress(0.2, "preparing PCA")
        _, singular, vt = np.linalg.svd(x, full_matrices=False)
        variance = (singular * singular) / max(x.shape[0] - 1, 1)
        total = float(variance.sum())
        explained = variance / total if total > 0 else np.zeros_like(variance)
        components = min(2, vt.shape[0])
        coords = x @ vt[:components].T
        if components < 2:
            coords = np.pad(coords, ((0, 0), (0, 2 - components)))
        if progress:
            progress(1.0, "PCA complete")
        return {
            "arrays": {"coords": coords.astype(np.float64, copy=False), "explained_variance": explained.astype(np.float64)},
            "preview": {
                "kind": "projection",
                "x_label": f"PC1 ({explained[0] * 100:.1f}%)" if explained.size else "PC1",
                "y_label": f"PC2 ({explained[1] * 100:.1f}%)" if explained.size > 1 else "PC2",
                "explained_variance": explained[: min(10, explained.size)].tolist(),
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def umap(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        _check_samples(samples.values, 3)
        x, warnings, keep = _preprocess(samples.values, params, "raw")
        n_neighbors = _int_param(params, "n_neighbors", 15, 2)
        if n_neighbors >= x.shape[0]:
            n_neighbors = x.shape[0] - 1
        if n_neighbors < 2:
            raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "UMAP needs at least 3 samples")
        min_dist = _float_param(params, "min_dist", 0.1, 0.0, 1.0)
        metric = params.get("metric", "euclidean")
        if metric not in ("euclidean", "cosine", "manhattan"):
            raise AppError(ANALYSIS_INPUT_INVALID, "UMAP metric must be euclidean, cosine, or manhattan")
        coords = fit_umap(
            x,
            n_neighbors=n_neighbors,
            min_dist=min_dist,
            metric=metric,
            seed=_seed(params),
            progress=progress,
        )
        return {
            "arrays": {"coords": coords},
            "preview": {"kind": "projection", "x_label": "UMAP-1", "y_label": "UMAP-2", "parameters": {"n_neighbors": n_neighbors, "min_dist": min_dist, "metric": metric}},
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def tsne(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        _check_samples(samples.values, 4)
        x, warnings, keep = _preprocess(samples.values, params, "raw")
        default_perplexity = min(30.0, max(2.0, float(x.shape[0] - 1)))
        perplexity = _float_param(params, "perplexity", default_perplexity, 2.0)
        if perplexity >= x.shape[0]:
            raise AppError(ANALYSIS_INPUT_INVALID, "t-SNE perplexity must be smaller than sample count")
        max_iter = _int_param(params, "max_iter", 1000, 250)
        tsne_cls = _safe_import("sklearn.manifold", "scikit-learn").TSNE
        if progress:
            progress(0.1, "fitting t-SNE")
        try:
            model = tsne_cls(n_components=2, perplexity=perplexity, max_iter=max_iter, random_state=_seed(params), init="pca", learning_rate="auto")
        except TypeError:  # sklearn <1.5
            model = tsne_cls(n_components=2, perplexity=perplexity, n_iter=max_iter, random_state=_seed(params), init="pca", learning_rate="auto")
        coords = model.fit_transform(x).astype(np.float64)
        if progress:
            progress(1.0, "t-SNE complete")
        return {
            "arrays": {"coords": coords},
            "preview": {"kind": "projection", "x_label": "t-SNE-1", "y_label": "t-SNE-2", "parameters": {"perplexity": perplexity, "max_iter": max_iter}},
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def neighbors(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        x, warnings, keep = _preprocess(samples.values, params, "raw")
        k = _int_param(params, "k", 10, 1)
        metric = params.get("metric", "euclidean")
        if metric not in ("euclidean", "cosine", "manhattan"):
            raise AppError(ANALYSIS_INPUT_INVALID, "metric must be euclidean, cosine, or manhattan")
        indices, distances = _nearest_distances(x, k, metric)
        if progress:
            progress(1.0, "nearest neighbors complete")
        return {"arrays": {"indices": indices.astype(np.int64), "distances": distances.astype(np.float64)}, "preview": {"kind": "neighbors", "k": int(indices.shape[1]), "metric": metric}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}

    @staticmethod
    def similarity(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        x, warnings, keep = _preprocess(samples.values, params, "raw")
        metric = params.get("metric", "cosine")
        if metric not in ("euclidean", "cosine"):
            raise AppError(ANALYSIS_INPUT_INVALID, "similarity metric must be euclidean or cosine")
        query = params.get("query_index")
        if query is None:
            query = 0
        query = int(query)
        if query < 0 or query >= x.shape[0]:
            raise AppError(ANALYSIS_INPUT_INVALID, "query_index is out of range")
        k = _int_param(params, "k", 10, 1)
        model = _safe_import("sklearn.neighbors", "scikit-learn").NearestNeighbors(n_neighbors=min(k + 1, x.shape[0]), metric=metric, n_jobs=1)
        distances, indices = model.fit(x).kneighbors(x[query : query + 1])
        mask = indices[0] != query
        indices = indices[0][mask][:k]
        distances = distances[0][mask][:k]
        similarity = 1.0 / (1.0 + distances) if metric == "euclidean" else 1.0 - distances
        if progress:
            progress(1.0, "similarity complete")
        return {"arrays": {"indices": indices.astype(np.int64), "distances": distances.astype(np.float64), "similarity": similarity.astype(np.float64)}, "preview": {"kind": "similarity", "query_index": query, "metric": metric}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}

    @staticmethod
    def pairwise(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        x, warnings, keep = _preprocess(samples.values, params, "raw")
        metric = str(params.get("metric") or "cosine")
        limit = min(_int_param(params, "max_samples", 400, 2), 2_000)
        indices = _bounded_indices(x.shape[0], limit)
        if indices.size < x.shape[0]:
            warnings.append(f"pairwise matrix limited to {indices.size} deterministic samples")
        if progress:
            progress(0.2, "building pairwise matrix")
        distances = _pairwise_matrix(x[indices], metric)
        similarity = 1.0 - distances if metric == "cosine" else 1.0 / (1.0 + distances)
        if progress:
            progress(1.0, "pairwise matrix complete")
        return {
            "arrays": {
                "sample_indices": indices,
                "distance_matrix": distances,
                "similarity_matrix": similarity.astype(np.float64),
            },
            "preview": {
                "kind": "pairwise_similarity",
                "metric": metric,
                "sample_count": int(indices.size),
                "total_samples": int(x.shape[0]),
                "distance_min": float(distances.min()),
                "distance_max": float(distances.max()),
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def cluster(samples: SampleMatrix, params: dict, algorithm: str, progress: Callable[[float, str], None] | None = None) -> dict:
        x, warnings, keep = _preprocess(samples.values, params, "standardized")
        algorithm = algorithm.lower()
        if algorithm == "kmeans":
            k = _int_param(params, "n_clusters", 6, 2)
            if k > x.shape[0]:
                raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "n_clusters cannot exceed sample count")
            cls = _safe_import("sklearn.cluster", "scikit-learn").KMeans
            model = cls(n_clusters=k, random_state=_seed(params), n_init=10, max_iter=_int_param(params, "max_iter", 300, 1))
        elif algorithm == "dbscan":
            eps = _float_param(params, "eps", 0.5, 0.0)
            min_samples = _int_param(params, "min_samples", 5, 1)
            model = _safe_import("sklearn.cluster", "scikit-learn").DBSCAN(eps=eps, min_samples=min_samples, metric=params.get("metric", "euclidean"))
        elif algorithm == "hdbscan":
            hdb = _safe_import("hdbscan", "hdbscan")
            model = hdb.HDBSCAN(min_cluster_size=_int_param(params, "min_cluster_size", 5, 2), min_samples=params.get("min_samples"), metric=params.get("metric", "euclidean"), prediction_data=False)
        elif algorithm in ("agglomerative", "hierarchical"):
            k = _int_param(params, "n_clusters", 6, 2)
            if k > x.shape[0]:
                raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "n_clusters cannot exceed sample count")
            cls = _safe_import("sklearn.cluster", "scikit-learn").AgglomerativeClustering
            model = cls(n_clusters=k, linkage=params.get("linkage", "ward"))
        else:
            raise AppError(ANALYSIS_INPUT_INVALID, f"unsupported cluster algorithm: {algorithm}")
        if progress:
            progress(0.1, f"fitting {algorithm}")
        labels = np.asarray(model.fit_predict(x), dtype=np.int64)
        arrays: dict[str, np.ndarray] = {"labels": labels, "coords": _visual_pca(x)}
        if hasattr(model, "cluster_centers_"):
            arrays["centers"] = np.asarray(model.cluster_centers_, dtype=np.float64)
        if hasattr(model, "probabilities_"):
            arrays["probabilities"] = np.asarray(model.probabilities_, dtype=np.float64)
        if progress:
            progress(1.0, f"{algorithm} complete")
        return {"arrays": arrays, "preview": {"kind": "clusters", "algorithm": algorithm, "cluster_count": int(len(set(labels.tolist())) - (1 if -1 in labels else 0)), "noise_count": int((labels == -1).sum())}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}

    @staticmethod
    def outlier(samples: SampleMatrix, params: dict, algorithm: str, progress: Callable[[float, str], None] | None = None) -> dict:
        x, warnings, keep = _preprocess(samples.values, params, "standardized")
        algorithm = algorithm.lower()
        contamination = _float_param(params, "contamination", 0.01, 0.0, 0.5)
        if contamination <= 0:
            contamination = "auto"
        if algorithm in ("knn", "k-nearest-neighbor"):
            k = _int_param(params, "k", 10, 1)
            _, distances = _nearest_distances(x, k, "euclidean")
            scores = distances.mean(axis=1)
            threshold = float(np.quantile(scores, 1.0 - (0.01 if contamination == "auto" else contamination)))
            labels = (scores > threshold).astype(np.int64)
        elif algorithm == "lof":
            _check_samples(x, 3)
            cls = _safe_import("sklearn.neighbors", "scikit-learn").LocalOutlierFactor
            model = cls(n_neighbors=min(_int_param(params, "k", 20, 2), x.shape[0] - 1), contamination=contamination)
            predicted = np.asarray(model.fit_predict(x))
            scores = -np.asarray(model.negative_outlier_factor_, dtype=np.float64)
            labels = (predicted < 0).astype(np.int64)
        elif algorithm in ("isolation_forest", "isolation-forest", "iforest"):
            cls = _safe_import("sklearn.ensemble", "scikit-learn").IsolationForest
            model = cls(contamination=contamination, random_state=_seed(params), n_estimators=_int_param(params, "n_estimators", 200, 10), n_jobs=1)
            model.fit(x)
            scores = -np.asarray(model.score_samples(x), dtype=np.float64)
            labels = (np.asarray(model.predict(x)) < 0).astype(np.int64)
        elif algorithm in ("mahalanobis", "mahalanobis_distance"):
            center = x.mean(axis=0)
            cov = np.cov(x, rowvar=False)
            cov = np.atleast_2d(cov) + np.eye(x.shape[1]) * 1e-10
            inv = np.linalg.pinv(cov)
            delta = x - center
            scores = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", delta, inv, delta), 0.0))
            threshold = float(np.quantile(scores, 1.0 - (0.01 if contamination == "auto" else contamination)))
            labels = (scores > threshold).astype(np.int64)
        else:
            raise AppError(ANALYSIS_INPUT_INVALID, f"unsupported outlier algorithm: {algorithm}")
        if progress:
            progress(1.0, f"{algorithm} complete")
        return {"arrays": {"scores": scores.astype(np.float64), "labels": labels, "coords": _visual_pca(x)}, "preview": {"kind": "outliers", "algorithm": algorithm, "outlier_count": int(labels.sum()), "contamination": contamination}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}

    @staticmethod
    def sampling(samples: SampleMatrix, params: dict, algorithm: str, progress: Callable[[float, str], None] | None = None, existing: SampleMatrix | None = None, group_labels: np.ndarray | None = None, blocks: list[FeatureBlock] | None = None, existing_blocks: list[FeatureBlock] | None = None) -> dict:
        x = _as_float64(samples.values)
        n = x.shape[0]
        requested = _int_param(params, "n_samples", 1000, 1)
        target = min(requested, n)
        algorithm = algorithm.lower().replace("-", "_")
        rng = np.random.default_rng(_seed(params))
        warnings: list[str] = []

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
            scaling_mode = str(params.get("scaling") or "robust")
            if scaling_mode not in SCALING_MODES:
                raise AppError(ANALYSIS_INPUT_INVALID, "scaling must be raw, standardized, or robust")
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
                "coverage_r2_final": result.coverage_r2,
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
                    "selected_indices": np.sort(result.indices).astype(np.int64),
                    "coords": coords,
                    "nearest_distances": result.nearest_distances,
                    "selection_distances": result.selection_distances,
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
            k = _int_param(params, "n_clusters", min(6, max(2, target)), 2)
            cls = _safe_import("sklearn.cluster", "scikit-learn").KMeans
            model = cls(n_clusters=min(k, n), random_state=_seed(params), n_init=10).fit(x)
            selected_list = []
            for center in model.cluster_centers_:
                members = np.flatnonzero(model.labels_ == len(selected_list))
                if len(members):
                    distances = ((x[members] - center) ** 2).sum(axis=1)
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
        return {"arrays": {"selected_indices": selected.astype(np.int64), "coords": _visual_pca(x)}, "preview": {"kind": "sampling", "algorithm": algorithm, "selected_count": int(selected.size), "requested_count": int(target)}, "warnings": warnings}

    @staticmethod
    def coverage(reference: SampleMatrix, query: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        ref, qry, warnings, keep = _preprocess_reference_query(reference.values, query.values, params, "raw")
        metric = str(params.get("metric") or "euclidean")
        _check_samples(ref, 1)
        _check_samples(qry, 1)
        chunk = _int_param(params, "chunk_size", 2048, 1)
        reference_chunk = _int_param(params, "reference_chunk_size", 2048, 1)
        self_dist = _nearest_distances(ref, min(_int_param(params, "k", 1, 1), max(ref.shape[0] - 1, 1)), metric)[1][:, 0] if ref.shape[0] > 1 else np.zeros(1)
        q95 = _float_param(params, "q95", float(np.quantile(self_dist, 0.95)) if self_dist.size else 0.0, 0.0)
        q99 = _float_param(params, "q99", float(np.quantile(self_dist, 0.99)) if self_dist.size else q95, 0.0)
        if q99 < q95:
            raise AppError(ANALYSIS_INPUT_INVALID, "q99 must be >= q95")
        nearest, distances = _cross_nearest(ref, qry, metric, chunk, reference_chunk, progress)
        labels = np.where(distances <= q95, 0, np.where(distances <= q99, 1, 2)).astype(np.int64)
        arrays = {"nearest_indices": nearest, "distances": distances, "labels": labels}
        arrays.update(_joint_projection(ref, qry, min(_int_param(params, "projection_samples", 2_000, 50), 10_000)))
        return {"arrays": arrays, "preview": {"kind": "coverage", "categories": ["covered", "marginal", "out_of_coverage"], "q95": q95, "q99": q99, "metric": metric, "covered": int((labels == 0).sum()), "marginal": int((labels == 1).sum()), "out_of_coverage": int((labels == 2).sum()), "mean_distance": float(distances.mean()), "median_distance": float(np.median(distances)), "max_distance": float(distances.max())}, "warnings": warnings, "feature_indices": np.flatnonzero(keep).astype(np.int64)}

    @staticmethod
    def overlap(reference: SampleMatrix, query: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        ref, qry, warnings, keep = _preprocess_reference_query(reference.values, query.values, params, "standardized")
        metric = str(params.get("metric") or "euclidean")
        chunk = _int_param(params, "chunk_size", 2048, 1)
        reference_chunk = _int_param(params, "reference_chunk_size", 2048, 1)
        nearest, distances = _cross_nearest(ref, qry, metric, chunk, reference_chunk, progress)
        if ref.shape[0] > 1:
            self_dist = _nearest_distances(ref, 1, metric)[1][:, 0]
            similar_default = float(np.quantile(self_dist, 0.05))
        else:
            similar_default = 0.0
        duplicate_threshold = _float_param(params, "duplicate_threshold", 1e-8, 0.0)
        similar_threshold = _float_param(params, "similar_threshold", max(similar_default, duplicate_threshold), duplicate_threshold)
        labels = np.where(distances <= duplicate_threshold, 0, np.where(distances <= similar_threshold, 1, 2)).astype(np.int64)
        arrays = {"nearest_indices": nearest, "distances": distances, "labels": labels}
        arrays.update(_joint_projection(ref, qry, min(_int_param(params, "projection_samples", 2_000, 50), 10_000)))
        total = max(int(labels.size), 1)
        return {
            "arrays": arrays,
            "preview": {
                "kind": "overlap",
                "categories": ["near_duplicate", "highly_similar", "independent"],
                "metric": metric,
                "duplicate_threshold": duplicate_threshold,
                "similar_threshold": similar_threshold,
                "near_duplicates": int((labels == 0).sum()),
                "highly_similar": int((labels == 1).sum()),
                "independent": int((labels == 2).sum()),
                "overlap_fraction": float(((labels == 0) | (labels == 1)).sum() / total),
                "mean_distance": float(distances.mean()),
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def acquisition(reference: SampleMatrix, query: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        """Select a novel/uncertain and diverse query batch.

        ``novelty_fps`` keeps the original nearest-reference acquisition.  The
        ``uncertainty_diversity`` variant estimates descriptor-space epistemic
        uncertainty from the distance to the k-th reference neighbor and its
        local spread.  It is deliberately model-free: the Studio remains a
        descriptor-analysis application and does not silently run a force or
        energy model during acquisition.
        """
        ref, qry, warnings, keep = _preprocess_reference_query(reference.values, query.values, params, "standardized")
        metric = str(params.get("metric") or "euclidean")
        acquisition_method = str(params.get("acquisition_method") or params.get("algorithm") or "novelty_fps").lower()
        if acquisition_method in ("uncertainty", "uncertainty_diversity", "knn_uncertainty"):
            uncertainty_k = min(_int_param(params, "uncertainty_k", 8, 2), ref.shape[0])
            neighbor_indices, neighbor_distances = _cross_k_nearest(
                ref,
                qry,
                metric,
                _int_param(params, "chunk_size", 2048, 1),
                _int_param(params, "reference_chunk_size", 2048, 1),
                uncertainty_k,
                progress,
            )
            nearest = neighbor_indices[:, 0]
            novelty = neighbor_distances[:, 0]
            uncertainty = neighbor_distances[:, -1] + neighbor_distances.std(axis=1)
            acquisition_method = "uncertainty_diversity"
            warnings.append("uncertainty is a descriptor-space kNN extrapolation proxy, not model prediction variance")
        else:
            nearest, novelty = _cross_nearest(
                ref,
                qry,
                metric,
                _int_param(params, "chunk_size", 2048, 1),
                _int_param(params, "reference_chunk_size", 2048, 1),
                progress,
            )
            uncertainty = novelty.copy()
            acquisition_method = "novelty_fps"
        target = min(_int_param(params, "n_samples", 100, 1), qry.shape[0])
        pool_factor = _float_param(params, "pool_factor", 5.0, 1.0)
        pool_size = min(qry.shape[0], max(target, int(round(target * pool_factor))))
        base_score = uncertainty if acquisition_method == "uncertainty_diversity" else novelty
        pool = np.argsort(-base_score, kind="stable")[:pool_size]
        base_scale = np.ptp(base_score[pool])
        normalized_base = (base_score[pool] - base_score[pool].min()) / max(float(base_scale), 1e-15)
        base_weight_name = "uncertainty_weight" if acquisition_method == "uncertainty_diversity" else "novelty_weight"
        base_weight = _float_param(params, base_weight_name, 0.65, 0.0, 1.0)
        selected_local = [int(np.argmax(normalized_base))]
        min_diversity = np.full(pool_size, np.inf, dtype=np.float64)
        acquisition_score = np.zeros(pool_size, dtype=np.float64)
        for step in range(1, target):
            last = qry[pool[selected_local[-1]]]
            delta = qry[pool] - last
            if metric == "euclidean":
                distances = np.linalg.norm(delta, axis=1)
            elif metric == "manhattan":
                distances = np.abs(delta).sum(axis=1)
            else:
                pool_norm = np.linalg.norm(qry[pool], axis=1)
                last_norm = float(np.linalg.norm(last))
                denominator = np.maximum(pool_norm * max(last_norm, 1e-15), 1e-15)
                distances = np.maximum(1.0 - (qry[pool] @ last) / denominator, 0.0)
            min_diversity = np.minimum(min_diversity, distances)
            diversity_scale = np.ptp(min_diversity[np.isfinite(min_diversity)]) if np.isfinite(min_diversity).any() else 0.0
            normalized_diversity = (min_diversity - np.nanmin(min_diversity)) / max(float(diversity_scale), 1e-15)
            acquisition_score = base_weight * normalized_base + (1.0 - base_weight) * normalized_diversity
            acquisition_score[selected_local] = -1.0
            selected_local.append(int(np.argmax(acquisition_score)))
            if progress and (step % 50 == 0 or step == target - 1):
                progress(step / max(target, 1), f"{acquisition_method} acquisition")
        selected = pool[np.asarray(selected_local, dtype=np.int64)]
        full_scores = np.zeros(qry.shape[0], dtype=np.float64)
        full_uncertainty = np.zeros(qry.shape[0], dtype=np.float64)
        full_diversity = np.zeros(qry.shape[0], dtype=np.float64)
        if np.isfinite(min_diversity).any():
            diversity_component = np.nan_to_num(min_diversity / max(float(np.nanmax(min_diversity[np.isfinite(min_diversity)])), 1e-15), posinf=0.0)
        else:
            diversity_component = np.zeros(pool_size, dtype=np.float64)
        full_uncertainty[pool] = uncertainty[pool]
        full_diversity[pool] = diversity_component
        full_scores[pool] = base_weight * normalized_base + (1.0 - base_weight) * diversity_component
        arrays = {
            "selected_indices": selected.astype(np.int64),
            "nearest_indices": nearest,
            "distances": novelty,
            "novelty": novelty,
            "uncertainty": full_uncertainty,
            "diversity": full_diversity,
            "scores": full_scores,
            "coords": _visual_pca(qry),
        }
        return {
            "arrays": arrays,
            "preview": {
                "kind": "acquisition",
                "algorithm": acquisition_method,
                "uncertainty_method": "knn_extrapolation" if acquisition_method == "uncertainty_diversity" else None,
                "selected_count": int(selected.size),
                "candidate_pool": int(pool_size),
                "base_weight": base_weight,
                "novelty_weight": base_weight if acquisition_method == "novelty_fps" else None,
                "uncertainty_weight": base_weight if acquisition_method == "uncertainty_diversity" else None,
                "mean_selected_novelty": float(novelty[selected].mean()),
                "mean_selected_uncertainty": float(uncertainty[selected].mean()),
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def mantel(left: SampleMatrix, right: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        """Run a two-sided Mantel permutation test on two aligned descriptor spaces."""
        a, left_warnings, _left_keep = _preprocess(left.values, params, "standardized")
        b, right_warnings, _right_keep = _preprocess(right.values, params, "standardized")
        _check_samples(a, 3)
        _check_samples(b, 3)
        if left.n_samples != right.n_samples or left.sample_ids != right.sample_ids:
            raise AppError(
                ANALYSIS_INPUT_INVALID,
                "Mantel comparison requires aligned sample IDs",
                {"left_samples": left.n_samples, "right_samples": right.n_samples},
            )
        metric = str(params.get("metric") or "euclidean")
        if metric not in ("euclidean", "cosine", "manhattan"):
            raise AppError(ANALYSIS_INPUT_INVALID, "Mantel metric must be euclidean, cosine, or manhattan")
        method = str(params.get("method") or "pearson").lower()
        if method not in ("pearson", "spearman"):
            raise AppError(ANALYSIS_INPUT_INVALID, "Mantel method must be pearson or spearman")
        alternative = str(params.get("alternative") or "two-sided").lower()
        if alternative not in ("two-sided", "greater", "less"):
            raise AppError(ANALYSIS_INPUT_INVALID, "Mantel alternative must be two-sided, greater, or less")
        limit = min(_int_param(params, "max_samples", 600, 3), 2_000)
        sample_indices = _bounded_indices(a.shape[0], limit)
        a = a[sample_indices]
        b = b[sample_indices]
        left_matrix = _pairwise_matrix(a, metric)
        right_matrix = _pairwise_matrix(b, metric)
        triangle = np.triu_indices(sample_indices.size, 1)
        left_pairs = left_matrix[triangle]
        right_pairs = right_matrix[triangle]
        statistic_fn = _safe_correlation if method == "pearson" else _rank_correlation
        observed = float(statistic_fn(left_pairs, right_pairs))
        permutations = min(_int_param(params, "permutations", 999, 1), 5_000)
        rng = np.random.default_rng(_seed(params))
        null = np.empty(permutations, dtype=np.float64)
        if progress:
            progress(0.05, "computing Mantel statistic")
        for index in range(permutations):
            permutation = rng.permutation(b.shape[0])
            permuted_pairs = _pairwise_matrix(b[permutation], metric)[triangle]
            null[index] = statistic_fn(left_pairs, permuted_pairs)
            if progress and (index % 25 == 0 or index == permutations - 1):
                progress(0.05 + 0.9 * (index + 1) / permutations, "running Mantel permutations")
        if alternative == "greater":
            exceed = int((null >= observed).sum())
        elif alternative == "less":
            exceed = int((null <= observed).sum())
        else:
            exceed = int((np.abs(null) >= abs(observed)).sum())
        p_value = float((exceed + 1) / (permutations + 1))
        if progress:
            progress(1.0, "Mantel test complete")
        warnings = [*left_warnings, *right_warnings]
        if a.shape[0] < left.n_samples:
            warnings.append(f"Mantel permutations limited to {a.shape[0]} deterministic aligned samples")
        return {
            "arrays": {
                "sample_indices": sample_indices,
                "left_pair_distances": left_pairs.astype(np.float64),
                "right_pair_distances": right_pairs.astype(np.float64),
                "null_distribution": null,
            },
            "preview": {
                "kind": "mantel",
                "method": method,
                "metric": metric,
                "alternative": alternative,
                "statistic": observed,
                "p_value": p_value,
                "permutations": permutations,
                "sample_count": int(a.shape[0]),
                "pair_count": int(left_pairs.size),
                "significant_at_05": bool(p_value < 0.05),
            },
            "warnings": warnings,
        }

    @staticmethod
    def perturbation_sensitivity(
        baseline: SampleMatrix,
        perturbations: list[tuple[float, SampleMatrix]],
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

    @staticmethod
    def compare(left: SampleMatrix, right: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        a, left_warnings, _left_keep = _preprocess(left.values, params, "standardized")
        b, right_warnings, _right_keep = _preprocess(right.values, params, "standardized")
        _check_samples(a, 3)
        _check_samples(b, 3)
        if left.n_samples != right.n_samples or left.sample_ids != right.sample_ids:
            raise AppError(
                ANALYSIS_INPUT_INVALID,
                "comparison requires aligned sample IDs",
                {"left_samples": left.n_samples, "right_samples": right.n_samples},
            )
        if progress:
            progress(0.15, "comparing descriptor geometry")
        metrics, result = _aligned_space_metrics(a, b, params)
        left_dimension, left_thresholds = _effective_dimension_metrics(a)
        right_dimension, right_thresholds = _effective_dimension_metrics(b)
        preview = {
            "kind": "compare",
            "left_samples": left.n_samples,
            "right_samples": right.n_samples,
            "left_feature_count": left.n_features,
            "right_feature_count": right.n_features,
            **metrics,
            "left_effective_dimension": left_dimension,
            "right_effective_dimension": right_dimension,
            "left_components_for_threshold": left_thresholds,
            "right_components_for_threshold": right_thresholds,
            "comparison_level": "aligned descriptor-space geometry",
        }
        if left.n_features == right.n_features:
            mean_delta = b.mean(axis=0) - a.mean(axis=0)
            var_delta = b.var(axis=0) - a.var(axis=0)
            result.update({"mean_delta": mean_delta, "variance_delta": var_delta})
            preview.update({"feature_count": left.n_features, "mean_absolute_delta": float(np.mean(np.abs(mean_delta))), "variance_absolute_delta": float(np.mean(np.abs(var_delta)))})
        if progress:
            progress(1.0, "comparison complete")
        return {"arrays": result, "preview": preview, "warnings": [*left_warnings, *right_warnings]}

    @staticmethod
    def feature_variance(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        x = _as_float64(samples.values, allow_nonfinite=True)
        feature_count = int(x.shape[1])
        sample_count = int(x.shape[0])
        near_zero_threshold = _float_param(params, "near_zero_relative_threshold", 1e-4, 0.0)
        low_variation_threshold = _float_param(params, "low_variance_relative_threshold", 1e-2, 0.0)
        if low_variation_threshold < near_zero_threshold:
            raise AppError(
                ANALYSIS_INPUT_INVALID,
                "low_variance_relative_threshold must be >= near_zero_relative_threshold",
            )
        constant_tolerance = _float_param(params, "constant_tolerance", 1e-12, 0.0)
        histogram_bins = _int_param(params, "histogram_bins", 32, 8)
        if histogram_bins > 128:
            raise AppError(ANALYSIS_INPUT_INVALID, "histogram_bins must be <= 128")
        distribution_sample_size = _int_param(params, "distribution_sample_size", 20_000, 1)
        distribution_sample_size = min(distribution_sample_size, 20_000)
        # Bound the total sample artifact as descriptor width grows. The
        # sample is only for KDE/box visualisation; all scalar statistics below
        # use every finite value.
        distribution_capacity = min(
            sample_count,
            distribution_sample_size,
            max(1, 2_000_000 // max(feature_count, 1)),
        )

        finite_counts = np.isfinite(x).sum(axis=0).astype(np.int64)
        invalid_counts = (sample_count - finite_counts).astype(np.int64)
        variance = np.zeros(feature_count, dtype=np.float64)
        std = np.zeros(feature_count, dtype=np.float64)
        means = np.zeros(feature_count, dtype=np.float64)
        minima = np.zeros(feature_count, dtype=np.float64)
        maxima = np.zeros(feature_count, dtype=np.float64)
        percentiles = np.zeros((feature_count, 5), dtype=np.float64)
        iqr = np.zeros(feature_count, dtype=np.float64)
        mad = np.zeros(feature_count, dtype=np.float64)
        robust_sigma = np.zeros(feature_count, dtype=np.float64)
        # Persist finite arrays only. A missing ratio is represented as None
        # in the feature record and as zero in this companion array.
        std_robust_ratio = np.zeros(feature_count, dtype=np.float64)
        histogram_edges = np.zeros((feature_count, histogram_bins + 1), dtype=np.float64)
        histogram_counts = np.zeros((feature_count, histogram_bins), dtype=np.int64)
        distribution_samples = np.zeros((feature_count, distribution_capacity), dtype=np.float64)
        distribution_sample_counts = np.zeros(feature_count, dtype=np.int64)
        feature_records: list[dict[str, Any]] = []

        for index in range(feature_count):
            values = x[:, index]
            finite_values = values[np.isfinite(values)]
            count = int(finite_values.size)
            record: dict[str, Any] = {
                "index": index,
                "mean": None,
                "variance": None,
                "relative_variance": None,
                "std": None,
                "min": None,
                "max": None,
                "p05": None,
                "p25": None,
                "median": None,
                "p75": None,
                "p95": None,
                "iqr": None,
                "mad": None,
                "robust_sigma": None,
                "std_robust_ratio": None,
                "whisker_min": None,
                "whisker_max": None,
                "outlier_count": 0,
                "finite_count": count,
                "invalid_count": int(invalid_counts[index]),
                # Keep the name used by the original development plan as a
                # compatibility alias; invalid_count is the canonical field.
                "missing_count": int(invalid_counts[index]),
                "distribution_sample_count": 0,
                "status": "invalid",
            }
            if count:
                mean = float(np.mean(finite_values))
                centered = finite_values - mean
                feature_variance = float(np.mean(centered * centered))
                feature_std = float(np.sqrt(feature_variance))
                quantile_values = np.percentile(finite_values, [5, 25, 50, 75, 95])
                median = float(quantile_values[2])
                feature_iqr = float(quantile_values[3] - quantile_values[1])
                feature_mad = float(np.median(np.abs(finite_values - median)))
                feature_robust_sigma = float(1.4826 * feature_mad)
                ratio = (
                    float(feature_std / feature_robust_sigma)
                    if feature_robust_sigma > np.finfo(np.float64).eps
                    else None
                )
                lower_fence = float(quantile_values[1] - 1.5 * feature_iqr)
                upper_fence = float(quantile_values[3] + 1.5 * feature_iqr)
                outlier_mask = (finite_values < lower_fence) | (finite_values > upper_fence)
                outlier_indices = np.flatnonzero(outlier_mask)
                inlier_values = finite_values[~outlier_mask]
                whisker_min = float(np.min(inlier_values)) if inlier_values.size else float(quantile_values[1])
                whisker_max = float(np.max(inlier_values)) if inlier_values.size else float(quantile_values[3])
                outlier_count = int(outlier_indices.size)
                means[index] = mean
                variance[index] = feature_variance
                std[index] = feature_std
                minima[index] = float(np.min(finite_values))
                maxima[index] = float(np.max(finite_values))
                percentiles[index] = quantile_values
                iqr[index] = feature_iqr
                mad[index] = feature_mad
                robust_sigma[index] = feature_robust_sigma
                if ratio is not None:
                    std_robust_ratio[index] = ratio

                edges: np.ndarray
                counts: np.ndarray
                counts, edges = np.histogram(finite_values, bins=histogram_bins)
                histogram_counts[index] = counts.astype(np.int64, copy=False)
                histogram_edges[index] = edges.astype(np.float64, copy=False)
                sample_count_for_feature = min(count, distribution_capacity)
                if sample_count_for_feature:
                    if outlier_count >= sample_count_for_feature:
                        outlier_positions = np.linspace(0, outlier_count - 1, sample_count_for_feature, dtype=np.int64)
                        sample_indices = outlier_indices[outlier_positions]
                    elif outlier_count:
                        regular_count = sample_count_for_feature - outlier_count
                        regular_indices = np.linspace(0, count - 1, regular_count, dtype=np.int64)
                        # Preserve every detected outlier when the bounded
                        # sample has room; this keeps a long tail visible in
                        # the detail panel even for large descriptor runs.
                        sample_indices = np.unique(np.concatenate((outlier_indices, regular_indices)))
                    else:
                        sample_indices = np.linspace(0, count - 1, sample_count_for_feature, dtype=np.int64)
                    sample_count_for_feature = int(sample_indices.size)
                    distribution_samples[index, :sample_count_for_feature] = finite_values[sample_indices]
                    distribution_sample_counts[index] = sample_count_for_feature
                is_constant = float(np.ptp(finite_values)) <= constant_tolerance
                record.update(
                    {
                        "mean": mean,
                        "variance": feature_variance,
                        "std": feature_std,
                        "min": float(minima[index]),
                        "max": float(maxima[index]),
                        "p05": float(quantile_values[0]),
                        "p25": float(quantile_values[1]),
                        "median": median,
                        "p75": float(quantile_values[3]),
                        "p95": float(quantile_values[4]),
                        "iqr": feature_iqr,
                        "mad": feature_mad,
                        "robust_sigma": feature_robust_sigma,
                        "std_robust_ratio": ratio,
                        "whisker_min": whisker_min,
                        "whisker_max": whisker_max,
                        "outlier_count": outlier_count,
                        "distribution_sample_count": int(sample_count_for_feature),
                        "status": "constant" if is_constant else "pending",
                    }
                )
            feature_records.append(record)

        finite_variances = variance[finite_counts > 0]
        max_variance = float(np.max(finite_variances)) if finite_variances.size else 0.0
        relative_variance = variance / max_variance if max_variance > 0 else np.zeros(feature_count, dtype=np.float64)
        for index, record in enumerate(feature_records):
            if record["status"] == "invalid":
                continue
            if record["status"] == "constant":
                continue
            relative = float(relative_variance[index])
            record["status"] = (
                "near_zero"
                if relative < near_zero_threshold
                else "low_variation"
                if relative < low_variation_threshold
                else "active"
            )
            record["relative_variance"] = relative
        for index, record in enumerate(feature_records):
            if record["status"] == "constant":
                record["relative_variance"] = float(relative_variance[index])

        order = np.argsort(-variance, kind="stable")
        top_k = min(_int_param(params, "top_k", 20, 1), feature_count)
        status_values = np.asarray([record["status"] for record in feature_records], dtype=object)
        near_zero = status_values == "near_zero"
        constant = status_values == "constant"
        low_variation = status_values == "low_variation"
        active = status_values == "active"
        invalid = status_values == "invalid"
        summary_variances = finite_variances if finite_variances.size else np.asarray([0.0])
        warnings: list[str] = []
        invalid_total = int(invalid_counts.sum())
        if invalid_total:
            warnings.append(
                f"ignored {invalid_total} non-finite feature value(s) across {int(np.count_nonzero(invalid_counts))} feature(s)"
            )
        if progress:
            progress(0.8, "feature variance statistics complete")
            progress(1.0, "feature variance complete")
        return {
            "arrays": {
                "variance": variance,
                "relative_variance": relative_variance,
                "std": std,
                "iqr": iqr,
                "mad": mad,
                "std_robust_ratio": std_robust_ratio,
                "near_zero_mask": near_zero.astype(np.int64),
                "constant_mask": constant.astype(np.int64),
                "finite_count": finite_counts,
                "invalid_count": invalid_counts,
                "histogram_edges": histogram_edges,
                "histogram_counts": histogram_counts,
                "distribution_samples": distribution_samples,
                "distribution_sample_counts": distribution_sample_counts,
            },
            "preview": {
                "kind": "feature_variance",
                "schema_version": 2,
                "sample_count": sample_count,
                "feature_count": feature_count,
                "ddof": 0,
                "settings": {
                    "near_zero_relative_threshold": near_zero_threshold,
                    "low_variance_relative_threshold": low_variation_threshold,
                    "constant_tolerance": constant_tolerance,
                    "histogram_bins": histogram_bins,
                    "distribution_sample_limit": distribution_sample_size,
                },
                "summary": {
                    "max_variance": max_variance,
                    "median_variance": float(np.median(summary_variances)),
                    "min_variance": float(np.min(summary_variances)),
                    "near_zero_count": int(near_zero.sum()),
                    "constant_count": int(constant.sum()),
                    "low_variation_count": int(low_variation.sum()),
                    "active_count": int(active.sum()),
                    "invalid_count": int(invalid.sum()),
                    "invalid_value_count": invalid_total,
                    "effective_nonzero_dimensions": int((active | low_variation).sum()),
                },
                "top_k": top_k,
                "top_indices": order[:top_k].astype(np.int64).tolist(),
                "top_values": variance[order[:top_k]].astype(np.float64).tolist(),
                "features": feature_records,
            },
            "warnings": warnings,
        }

    @staticmethod
    def feature_correlation(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        x = _as_float64(samples.values)
        _check_samples(x, 2)
        method = str(params.get("method") or "pearson").lower()
        if method not in ("pearson", "spearman"):
            raise AppError(ANALYSIS_INPUT_INVALID, "feature correlation method must be pearson or spearman")
        variance = x.var(axis=0)
        variance_threshold = _float_param(params, "variance_threshold", 1e-12, 0.0)
        valid = variance > variance_threshold
        warnings = [f"ignored {int((~valid).sum())} zero-variance feature(s)"] if not bool(valid.all()) else []
        xv = x[:, valid]
        if xv.shape[1] < 2:
            raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "at least two non-constant features are required")
        top_k = _int_param(params, "top_k", 50, 1)
        correlation_input = xv
        if method == "spearman":
            rankdata = _safe_import("scipy.stats", "scipy").rankdata
            correlation_input = np.asarray(rankdata(xv, axis=0, method="average"), dtype=np.float64)
        standardized = (correlation_input - correlation_input.mean(axis=0)) / correlation_input.std(axis=0)
        # Only keep a bounded heatmap artifact.  Ranking pairs is the default
        # contract and avoids shipping an accidental D x D matrix over IPC.
        max_heatmap = min(_int_param(params, "heatmap_features", 256, 2), 512)
        if xv.shape[1] > max_heatmap:
            # For very wide descriptors, do not materialise the full D x D
            # correlation matrix just to discover the heatmap subset.  The
            # highest-variance features are a deterministic, useful subset.
            candidate = np.argsort(-variance[valid], kind="stable")[:max_heatmap]
            corr_input = standardized[:, candidate]
            bounded_matrix = (corr_input.T @ corr_input) / max(xv.shape[0], 1)
            feature_indices = np.flatnonzero(valid)[candidate]
            warnings.append(f"correlation heatmap limited to {max_heatmap} highest-variance features")
        else:
            bounded_matrix = (standardized.T @ standardized) / max(xv.shape[0], 1)
            feature_indices = np.flatnonzero(valid)
        tri = np.triu_indices(bounded_matrix.shape[0], 1)
        order = np.argsort(-np.abs(bounded_matrix[tri]), kind="stable")[:top_k]
        pairs = np.column_stack([feature_indices[tri[0][order]], feature_indices[tri[1][order]]]).astype(np.int64)
        values = bounded_matrix[tri[0][order], tri[1][order]].astype(np.float64)
        arrays: dict[str, np.ndarray] = {"pairs": pairs, "correlations": values}
        arrays["correlation_matrix"] = bounded_matrix.astype(np.float64)
        arrays["correlation_feature_indices"] = feature_indices.astype(np.int64)
        threshold_name = "correlation_threshold" if "correlation_threshold" in params else "redundancy_threshold"
        correlation_threshold = _float_param(params, threshold_name, 0.95, 0.0, 1.0)
        high_pairs = np.argwhere(np.triu(np.abs(bounded_matrix) >= correlation_threshold, 1))
        involved_features = np.unique(feature_indices[high_pairs]) if high_pairs.size else np.empty(0, dtype=np.int64)
        clustered_feature_order = _clustered_feature_order(bounded_matrix, feature_indices)
        component_count = _connected_component_count(high_pairs, bounded_matrix.shape[0])
        feature_count = int(x.shape[1])
        if progress:
            progress(1.0, "feature correlation complete")
        return {
            "arrays": arrays,
            "preview": {
                "kind": "feature_correlation",
                "schema_version": 3,
                "correlation_metric": method,
                "correlation_threshold": correlation_threshold,
                "feature_count": feature_count,
                "valid_feature_count": int(xv.shape[1]),
                "zero_variance_count": int((~valid).sum()),
                "highly_correlated_pairs": int(high_pairs.shape[0]),
                "high_correlation_cluster_count": component_count,
                "involved_feature_count": int(involved_features.size),
                "involved_feature_ratio": float(involved_features.size / max(feature_count, 1)),
                "clustered_feature_order": clustered_feature_order.astype(np.int64).tolist(),
                "heatmap_feature_count": int(feature_indices.size),
                "heatmap_limited": bool(xv.shape[1] > max_heatmap),
                "pairs": [
                    {
                        "feature_a": int(a),
                        "feature_b": int(b),
                        "correlation": float(v),
                        "absolute_correlation": float(abs(v)),
                    }
                    for (a, b), v in zip(pairs.tolist(), values.tolist())
                ],
            },
            "warnings": warnings,
        }

    @staticmethod
    def property_correlation(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        property_name = str(params.get("property") or "energy_per_atom")
        target = samples.properties.get(property_name)
        if target is None:
            raise AppError(
                ANALYSIS_INPUT_INVALID,
                f"property {property_name!r} is unavailable for this descriptor run",
                {"available_properties": sorted(samples.properties)},
            )
        target = np.asarray(target, dtype=np.float64).reshape(-1)
        if target.size != samples.n_samples:
            raise AppError(ANALYSIS_INPUT_INVALID, "property values are not aligned with descriptor samples")
        valid = np.isfinite(target)
        if int(valid.sum()) < 3:
            raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "property correlation requires at least three finite targets")
        y = target[valid]
        if float(y.std()) <= np.finfo(np.float64).eps:
            raise AppError(ANALYSIS_INPUT_INVALID, f"property {property_name!r} is constant")
        raw_x = _as_float64(samples.values[valid])
        x, warnings, keep = _preprocess(raw_x, {"preprocess": "standardized"}, "standardized")
        model_x = raw_x[:, keep]
        standardized_target = (y - y.mean()) / y.std()
        correlations = (x.T @ standardized_target) / max(x.shape[0], 1)
        stats = _safe_import("scipy.stats", "scipy")
        ranked_x = np.asarray(stats.rankdata(model_x, axis=0, method="average"), dtype=np.float64)
        ranked_y = np.asarray(stats.rankdata(y, method="average"), dtype=np.float64)
        ranked_x = (ranked_x - ranked_x.mean(axis=0)) / ranked_x.std(axis=0)
        ranked_y = (ranked_y - ranked_y.mean()) / ranked_y.std()
        spearman_correlations = (ranked_x.T @ ranked_y) / max(ranked_x.shape[0], 1)

        sklearn_feature_selection = _safe_import("sklearn.feature_selection", "scikit-learn")
        mutual_information = np.asarray(
            sklearn_feature_selection.mutual_info_regression(
                model_x,
                y,
                n_neighbors=min(3, max(y.size - 1, 1)),
                random_state=_seed(params),
            ),
            dtype=np.float64,
        )
        order = np.argsort(-np.abs(correlations), kind="stable")
        top_k = min(_int_param(params, "top_k", 20, 1), correlations.size)

        sklearn_linear = _safe_import("sklearn.linear_model", "scikit-learn")
        sklearn_model_selection = _safe_import("sklearn.model_selection", "scikit-learn")
        sklearn_pipeline = _safe_import("sklearn.pipeline", "scikit-learn")
        sklearn_preprocessing = _safe_import("sklearn.preprocessing", "scikit-learn")
        sklearn_metrics = _safe_import("sklearn.metrics", "scikit-learn")
        folds = min(_int_param(params, "folds", 5, 2), x.shape[0])
        splitter = sklearn_model_selection.KFold(n_splits=folds, shuffle=True, random_state=_seed(params))
        model = sklearn_pipeline.make_pipeline(
            sklearn_preprocessing.StandardScaler(),
            sklearn_linear.Ridge(alpha=_float_param(params, "alpha", 1.0, 0.0)),
        )
        if progress:
            progress(0.25, "cross-validating property regression")
        sklearn_base = _safe_import("sklearn.base", "scikit-learn")
        sklearn_neighbors = _safe_import("sklearn.neighbors", "scikit-learn")
        distance_metric = str(params.get("distance_metric") or "euclidean").lower()
        if distance_metric not in ("euclidean", "cosine"):
            raise AppError(ANALYSIS_INPUT_INVALID, "property reliability distance_metric must be euclidean or cosine")
        reliability_k = _int_param(params, "reliability_k", 5, 1)
        predictions = np.empty(y.shape[0], dtype=np.float64)
        baseline_predictions = np.empty(y.shape[0], dtype=np.float64)
        oof_distances = np.empty(y.shape[0], dtype=np.float64)
        for train_indices, test_indices in splitter.split(model_x):
            fold_model = sklearn_base.clone(model)
            fold_model.fit(model_x[train_indices], y[train_indices])
            predictions[test_indices] = fold_model.predict(model_x[test_indices])
            baseline_predictions[test_indices] = float(y[train_indices].mean())

            scaler = sklearn_preprocessing.StandardScaler()
            train_scaled = scaler.fit_transform(model_x[train_indices])
            test_scaled = scaler.transform(model_x[test_indices])
            fold_k = min(reliability_k, train_indices.size)
            neighbor_model = sklearn_neighbors.NearestNeighbors(n_neighbors=fold_k, metric=distance_metric, n_jobs=1)
            neighbor_model.fit(train_scaled)
            distances, _neighbor_indices = neighbor_model.kneighbors(test_scaled)
            oof_distances[test_indices] = distances.mean(axis=1)

        r2 = float(sklearn_metrics.r2_score(y, predictions))
        rmse = float(np.sqrt(sklearn_metrics.mean_squared_error(y, predictions)))
        mae = float(sklearn_metrics.mean_absolute_error(y, predictions))
        baseline_r2 = float(sklearn_metrics.r2_score(y, baseline_predictions))
        baseline_rmse = float(np.sqrt(sklearn_metrics.mean_squared_error(y, baseline_predictions)))
        baseline_mae = float(sklearn_metrics.mean_absolute_error(y, baseline_predictions))
        residuals = predictions - y
        absolute_errors = np.abs(residuals)
        distance_error_pearson = _safe_correlation(oof_distances, absolute_errors)
        distance_error_spearman = _rank_correlation(oof_distances, absolute_errors)

        sparse_quantile = _float_param(params, "sparse_quantile", 0.90, 0.5, 0.99)
        ood_quantile = _float_param(params, "ood_quantile", 0.99, sparse_quantile, 0.999)
        sparse_threshold = float(np.quantile(oof_distances, sparse_quantile))
        ood_threshold = float(np.quantile(oof_distances, ood_quantile))
        high_error_threshold = float(np.quantile(absolute_errors, 0.90))
        high_error = absolute_errors > high_error_threshold
        sparse_or_ood = oof_distances > sparse_threshold

        bin_count = min(12, max(4, int(np.sqrt(y.size))))
        quantile_edges = np.unique(np.quantile(oof_distances, np.linspace(0.0, 1.0, bin_count + 1)))
        bin_centers: list[float] = []
        bin_median: list[float] = []
        bin_p90: list[float] = []
        bin_p95: list[float] = []
        for left, right in zip(quantile_edges[:-1], quantile_edges[1:]):
            members = (oof_distances >= left) & (oof_distances <= right if right == quantile_edges[-1] else oof_distances < right)
            if not bool(members.any()):
                continue
            bin_centers.append(float(np.median(oof_distances[members])))
            bin_median.append(float(np.median(absolute_errors[members])))
            bin_p90.append(float(np.quantile(absolute_errors[members], 0.90)))
            bin_p95.append(float(np.quantile(absolute_errors[members], 0.95)))

        pair_limit = min(_int_param(params, "pair_samples", 20_000, 100), 100_000)
        if x.shape[0] <= 250:
            pair_i, pair_j = np.triu_indices(x.shape[0], 1)
        else:
            rng = np.random.default_rng(_seed(params))
            pair_i = rng.integers(0, x.shape[0], size=pair_limit)
            pair_j = rng.integers(0, x.shape[0], size=pair_limit)
            different = pair_i != pair_j
            pair_i, pair_j = pair_i[different], pair_j[different]
        if pair_i.size > pair_limit:
            display = _bounded_indices(pair_i.size, pair_limit)
            pair_i, pair_j = pair_i[display], pair_j[display]
        pair_distance = np.linalg.norm(x[pair_i] - x[pair_j], axis=1)
        pair_property_delta = np.abs(y[pair_i] - y[pair_j])
        distance_property_correlation = _safe_correlation(pair_distance, pair_property_delta)
        property_units = {
            "energy_per_atom": "eV/atom",
            "energy": "eV",
            "force_max": "eV/Å",
            "force_magnitude": "eV/Å",
            "volume": "Å³",
        }
        max_pearson = float(np.max(np.abs(correlations)))
        max_spearman = float(np.max(np.abs(spearman_correlations)))
        max_mi = float(np.max(mutual_information))
        encoding_strength = "strong" if r2 >= 0.8 else "moderate" if r2 >= 0.5 else "weak"
        information_pattern = (
            "distributed" if r2 >= 0.8 and max_pearson < 0.6
            else "dominant_features" if r2 >= 0.8 and max_pearson >= 0.6
            else "insufficient" if r2 < 0.5 and max_pearson < 0.4
            else "mixed"
        )
        if progress:
            progress(1.0, "property correlation complete")
        return {
            "arrays": {
                "sample_indices": np.flatnonzero(valid).astype(np.int64),
                "sample_frames": np.asarray(samples.frame)[valid].astype(np.int64),
                "sample_rows": (
                    np.asarray(samples.row)[valid].astype(np.int64)
                    if samples.row is not None
                    else np.full(y.size, -1, dtype=np.int64)
                ),
                "targets": y,
                "predictions": predictions,
                "residuals": residuals.astype(np.float64),
                "absolute_errors": absolute_errors.astype(np.float64),
                "feature_correlations": correlations.astype(np.float64),
                "pearson_correlations": correlations.astype(np.float64),
                "spearman_correlations": spearman_correlations.astype(np.float64),
                "mutual_information": mutual_information.astype(np.float64),
                "feature_indices": np.flatnonzero(keep).astype(np.int64),
                "top_indices": np.flatnonzero(keep)[order[:top_k]].astype(np.int64),
                "oof_distances": oof_distances.astype(np.float64),
                "reliability_bin_center": np.asarray(bin_centers, dtype=np.float64),
                "reliability_bin_median": np.asarray(bin_median, dtype=np.float64),
                "reliability_bin_p90": np.asarray(bin_p90, dtype=np.float64),
                "reliability_bin_p95": np.asarray(bin_p95, dtype=np.float64),
                "pair_distance": pair_distance.astype(np.float64),
                "pair_property_delta": pair_property_delta.astype(np.float64),
            },
            "preview": {
                "kind": "property_correlation",
                "property": property_name,
                "property_unit": property_units.get(property_name, ""),
                "sample_count": int(y.size),
                "feature_count": int(samples.n_features),
                "valid_feature_count": int(model_x.shape[1]),
                "missing_count": int((~valid).sum()),
                "model": "Ridge",
                "model_alpha": _float_param(params, "alpha", 1.0, 0.0),
                "cv_folds": folds,
                "cv_shuffle": True,
                "cv_seed": _seed(params),
                "r2": r2,
                "rmse": rmse,
                "mae": mae,
                "baseline": "training-fold mean",
                "baseline_r2": baseline_r2,
                "baseline_rmse": baseline_rmse,
                "baseline_mae": baseline_mae,
                "residual_mean": float(residuals.mean()),
                "residual_median": float(np.median(residuals)),
                "residual_std": float(residuals.std()),
                "p95_absolute_error": float(np.quantile(absolute_errors, 0.95)),
                "max_abs_pearson": max_pearson,
                "max_abs_spearman": max_spearman,
                "max_mutual_information": max_mi,
                "encoding_strength": encoding_strength,
                "information_pattern": information_pattern,
                "distance_definition": "mean OOF training-fold kNN distance",
                "distance_metric": distance_metric,
                "distance_standardized": True,
                "reliability_k": reliability_k,
                "distance_error_pearson": distance_error_pearson,
                "distance_error_spearman": distance_error_spearman,
                "sparse_quantile": sparse_quantile,
                "ood_quantile": ood_quantile,
                "sparse_threshold": sparse_threshold,
                "ood_threshold": ood_threshold,
                "high_error_threshold": high_error_threshold,
                "high_error_high_distance_count": int(np.sum(high_error & sparse_or_ood)),
                "high_error_low_distance_count": int(np.sum(high_error & ~sparse_or_ood)),
                "distance_property_correlation": distance_property_correlation,
                "top_features": [
                    {
                        "feature": int(np.flatnonzero(keep)[index]),
                        "correlation": float(correlations[index]),
                        "pearson": float(correlations[index]),
                        "spearman": float(spearman_correlations[index]),
                        "mutual_information": float(mutual_information[index]),
                    }
                    for index in order[:top_k]
                ],
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def local_diversity(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        if samples.mode != "atom" or samples.row is None:
            raise AppError(ANALYSIS_INPUT_INVALID, "local diversity requires atom/local-environment samples")
        x, warnings, keep = _preprocess(samples.values, params, "standardized")
        _check_samples(x, 3)
        elements = np.asarray(samples.elements if samples.elements is not None else np.zeros(x.shape[0]), dtype=np.int64)
        cutoff = _float_param(params, "cutoff", 3.0, 0.1)
        max_neighbors = _int_param(params, "max_neighbors", 128, 1)
        coordination_all, neighbor_offsets_all, neighbor_indices_all, neighbor_distances_all, graph_warnings = _local_neighbor_graph(samples, cutoff, max_neighbors)
        warnings.extend(graph_warnings)
        selected_element = params.get("element")
        if selected_element is not None:
            selected_element = int(selected_element)
            mask = elements == selected_element
            if int(mask.sum()) < 3:
                raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, f"element {selected_element} has fewer than three local environments")
            x = x[mask]
            elements = elements[mask]
            sample_indices = np.flatnonzero(mask).astype(np.int64)
        else:
            sample_indices = np.arange(x.shape[0], dtype=np.int64)
        selected_coordination = coordination_all[sample_indices]
        selected_neighbor_indices: list[int] = []
        selected_neighbor_distances: list[float] = []
        selected_neighbor_offsets = np.zeros(sample_indices.size + 1, dtype=np.int64)
        for output_index, original_index in enumerate(sample_indices.tolist()):
            lo, hi = int(neighbor_offsets_all[original_index]), int(neighbor_offsets_all[original_index + 1])
            selected_neighbor_indices.extend(int(value) for value in neighbor_indices_all[lo:hi].tolist())
            selected_neighbor_distances.extend(float(value) for value in neighbor_distances_all[lo:hi].tolist())
            selected_neighbor_offsets[output_index + 1] = len(selected_neighbor_indices)
        coords = _visual_pca(x)
        categories = np.zeros(x.shape[0], dtype=np.int64)
        scores = np.zeros(x.shape[0], dtype=np.float64)
        cluster_labels = np.full(x.shape[0], -1, dtype=np.int64)
        summaries = []
        cluster_offset = 0
        for group_index, element in enumerate(np.unique(elements).tolist()):
            members = np.flatnonzero(elements == element)
            group = x[members]
            if group.shape[0] > 1:
                k = min(_int_param(params, "k", 10, 1), group.shape[0] - 1)
                group_scores = _nearest_distances(group, k, "euclidean")[1].mean(axis=1)
            else:
                group_scores = np.zeros(group.shape[0], dtype=np.float64)
            q90 = float(np.quantile(group_scores, 0.90))
            q99 = float(np.quantile(group_scores, 0.99))
            group_categories = np.where(group_scores <= q90, 0, np.where(group_scores <= q99, 1, 2)).astype(np.int64)
            categories[members] = group_categories
            scores[members] = group_scores
            cluster_count = min(_int_param(params, "n_clusters", 6, 2), group.shape[0])
            if group.shape[0] >= 2:
                kmeans = _safe_import("sklearn.cluster", "scikit-learn").KMeans(
                    n_clusters=cluster_count,
                    random_state=_seed(params),
                    n_init=10,
                )
                group_clusters = np.asarray(kmeans.fit_predict(group), dtype=np.int64)
            else:
                group_clusters = np.zeros(group.shape[0], dtype=np.int64)
            cluster_labels[members] = group_clusters + cluster_offset
            cluster_offset += int(group_clusters.max()) + 1 if group_clusters.size else 0
            counts = np.bincount(group_clusters) if group_clusters.size else np.zeros(0, dtype=np.int64)
            effective_dimension, _thresholds = _effective_dimension_metrics(group)
            summaries.append({
                "element": int(element),
                "samples": int(group.shape[0]),
                "clusters": int(np.unique(group_clusters).size),
                "main_cluster_fraction": float(counts.max() / max(group.shape[0], 1)) if counts.size else 0.0,
                "distorted": int((group_categories == 1).sum()),
                "outliers": int((group_categories == 2).sum()),
                "median_neighbor_distance": float(np.median(group_scores)),
                "median_coordination": float(np.median(coordination_all[sample_indices[members]])) if members.size else 0.0,
                "max_coordination": int(coordination_all[sample_indices[members]].max()) if members.size else 0,
                "effective_dimension": effective_dimension,
            })
            if progress:
                progress((group_index + 1) / max(len(np.unique(elements)), 1), "summarizing local environments")
        return {
            "arrays": {
                "sample_indices": sample_indices,
                "coords": coords,
                "labels": categories,
                "scores": scores,
                "cluster_labels": cluster_labels,
                "elements": elements,
                "coordination": selected_coordination,
                "neighbor_offsets": selected_neighbor_offsets,
                "neighbor_indices": np.asarray(selected_neighbor_indices, dtype=np.int64),
                "neighbor_distances": np.asarray(selected_neighbor_distances, dtype=np.float64),
            },
            "preview": {
                "kind": "local_diversity",
                "categories": ["main", "distorted", "outlier"],
                "element_summary": summaries,
                "sample_count": int(x.shape[0]),
                "cutoff": cutoff,
                "max_neighbors": max_neighbors,
                "mean_coordination": float(selected_coordination.mean()) if selected_coordination.size else 0.0,
                "max_coordination": int(selected_coordination.max()) if selected_coordination.size else 0,
                "neighbor_count": int(len(selected_neighbor_indices)),
                "neighbor_graph_available": bool(samples.positions is not None),
                "selected_element": selected_element,
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def kernel(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        x, warnings, keep = _preprocess(samples.values, params, "standardized")
        limit = min(_int_param(params, "max_samples", 400, 2), 2_000)
        indices = _bounded_indices(x.shape[0], limit)
        if indices.size < x.shape[0]:
            warnings.append(f"kernel matrix limited to {indices.size} deterministic samples")
        x = x[indices]
        algorithm = str(params.get("kernel") or "rbf").lower()
        metrics = _safe_import("sklearn.metrics.pairwise", "scikit-learn")
        if algorithm == "linear":
            matrix = metrics.linear_kernel(x)
        elif algorithm == "cosine":
            matrix = metrics.cosine_similarity(x)
        elif algorithm == "polynomial":
            matrix = metrics.polynomial_kernel(
                x,
                degree=_int_param(params, "degree", 3, 1),
                gamma=params.get("gamma"),
                coef0=_float_param(params, "coef0", 1.0),
            )
        elif algorithm == "rbf":
            gamma = _float_param(params, "gamma", 1.0 / max(x.shape[1], 1), 0.0)
            matrix = metrics.rbf_kernel(x, gamma=gamma)
        else:
            raise AppError(ANALYSIS_INPUT_INVALID, "kernel must be linear, cosine, polynomial, or rbf")
        matrix = np.asarray(matrix, dtype=np.float64)
        centered = matrix - matrix.mean(axis=0, keepdims=True) - matrix.mean(axis=1, keepdims=True) + matrix.mean()
        eigenvalues = np.linalg.eigvalsh(centered)[::-1]
        positive = np.clip(eigenvalues, 0.0, None)
        total = float(positive.sum())
        normalized = positive / total if total > 0 else np.zeros_like(positive)
        effective_rank = float(np.exp(-np.sum(normalized[normalized > 0] * np.log(normalized[normalized > 0])))) if total > 0 else 0.0
        if progress:
            progress(1.0, "kernel analysis complete")
        return {
            "arrays": {"sample_indices": indices, "kernel_matrix": matrix, "eigenvalues": eigenvalues.astype(np.float64)},
            "preview": {
                "kind": "kernel",
                "kernel": algorithm,
                "sample_count": int(indices.size),
                "total_samples": int(samples.n_samples),
                "effective_rank": effective_rank,
                "top_eigenvalue_fraction": float(normalized[0]) if normalized.size else 0.0,
                "kernel_min": float(matrix.min()),
                "kernel_max": float(matrix.max()),
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def effective_dimension(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        effective_params = dict(params or {})
        preprocess = effective_params.get("preprocess")
        if preprocess is None or preprocess == "":
            preprocess = "standardized"
            effective_params["preprocess"] = preprocess
        x, warnings, keep = _preprocess(samples.values, effective_params, "standardized")
        _check_samples(x, 2)
        singular = np.linalg.svd(x, compute_uv=False, full_matrices=False)
        eigen = (singular * singular) / max(x.shape[0] - 1, 1)
        total = float(eigen.sum())
        normalized = eigen / total if total > 0 else np.zeros_like(eigen)
        participation = float(1.0 / np.sum(normalized * normalized)) if total > 0 else 0.0
        cumulative = np.cumsum(normalized)
        thresholds = (
            {str(t): int(np.searchsorted(cumulative, t) + 1) for t in (0.9, 0.95, 0.99)}
            if total > 0
            else {str(t): 0 for t in (0.9, 0.95, 0.99)}
        )
        pca_basis = {
            "standardized": "correlation",
            "center": "covariance",
            "raw": "uncentered_second_moment",
        }[preprocess]
        if progress:
            progress(1.0, "effective dimension complete")
        return {
            "arrays": {
                "eigenvalues": eigen.astype(np.float64),
                "explained_variance": normalized.astype(np.float64),
            },
            "preview": {
                "kind": "effective_dimension",
                "preprocess": preprocess,
                "pca_basis": pca_basis,
                "sample_count": int(samples.n_samples),
                "feature_count": int(samples.n_features),
                "pca_feature_count": int(x.shape[1]),
                "component_count": int(eigen.size),
                "participation_ratio": participation,
                "components_for_threshold": thresholds,
            },
            "warnings": warnings,
            "feature_indices": np.flatnonzero(keep).astype(np.int64),
        }

    @staticmethod
    def trajectory(samples: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        frames = np.asarray(samples.frame, dtype=np.int64)
        start = _int_param(params, "frame_start", int(frames.min()) if frames.size else 0, 0)
        end = _int_param(params, "frame_end", int(frames.max()) if frames.size else start, start)
        step = _int_param(params, "frame_step", 1, 1)
        selected = np.flatnonzero((frames >= start) & (frames <= end) & (((frames - start) % step) == 0))
        if selected.size < 2:
            raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "trajectory range contains fewer than two samples")
        x, warnings, _keep = _preprocess(samples.values[selected], params, "standardized")
        # Three distinct quantities: how much the descriptor moves between
        # neighbouring frames, how far it has drifted from the reference frame,
        # and how long the explored path is.  Only the first one detects events.
        deltas = np.linalg.norm(np.diff(x, axis=0), axis=1)
        step_distance = np.concatenate([[0.0], deltas])
        reference_distance = np.linalg.norm(x - x[0], axis=1)
        cumulative_distance = np.cumsum(step_distance)
        coords, pc_explained_variance = _visual_pca_components(x)
        if params.get("timestep") is not None:
            timestep = _float_param(params, "timestep", 1.0, 0.0)
            time_axis = frames[selected].astype(np.float64) * timestep
            time_unit = str(params.get("time_unit") or "arb. units")
        else:
            time_axis = frames[selected].astype(np.float64)
            time_unit = "frame"
        time_delta = np.diff(time_axis, prepend=time_axis[0])
        speed = np.divide(step_distance, time_delta, out=np.zeros_like(step_distance), where=time_delta > 0)
        event_method, event_sensitivity, event_threshold, step_stats = _trajectory_threshold(deltas, params)
        event_indices = np.flatnonzero(step_distance > event_threshold)
        if progress:
            progress(1.0, "trajectory analysis complete")
        # Percentile of each step among all steps, so one event reads as
        # "larger than 99.6% of the trajectory" without recomputation.
        step_ranks = np.argsort(np.argsort(deltas, kind="stable"), kind="stable")
        event_details = [
            {
                "index": int(index),
                "frame": int(frames[selected][index]),
                "step_distance": float(step_distance[index]),
                "threshold_ratio": float(step_distance[index] / event_threshold) if event_threshold > 0 else None,
                "percentile": float((step_ranks[index - 1] + 1) / deltas.size),
                "reference_distance": float(reference_distance[index]),
                "pc1": float(coords[index, 0]),
                "pc2": float(coords[index, 1]),
                "pc_displacement": float(np.linalg.norm(coords[index] - coords[index - 1])),
            }
            for index in event_indices[:200].tolist()
        ]
        return {
            "arrays": {
                "sample_indices": selected.astype(np.int64),
                "indices": selected.astype(np.int64),
                "frames": frames[selected],
                "time": time_axis,
                "step_distance": step_distance,
                "reference_distance": reference_distance.astype(np.float64),
                "cumulative_distance": cumulative_distance.astype(np.float64),
                "speed": speed.astype(np.float64),
                "coords": coords,
                "pc_explained_variance": pc_explained_variance,
                "event_indices": event_indices.astype(np.int64),
            },
            "preview": {
                "kind": "trajectory",
                "frame_start": start,
                "frame_end": end,
                "frame_step": step,
                "time_unit": time_unit,
                "sample_count": int(selected.size),
                "total_distance": float(deltas.sum()),
                "max_step_distance": float(deltas.max()),
                "max_reference_distance": float(reference_distance.max()),
                "median_step_distance": step_stats["median"],
                "mean_step_distance": step_stats["mean"],
                "step_mad": step_stats["mad"],
                "step_robust_sigma": step_stats["robust_sigma"],
                "step_std": step_stats["std"],
                "event_method": event_method,
                "event_sensitivity": event_sensitivity,
                "event_threshold": event_threshold,
                "event_count": int(event_indices.size),
                "event_rate": float(event_indices.size / deltas.size),
                "event_space": "descriptor",
                "detection_note": "Event detection uses descriptor-space step distances; the PCA panel is a visualization only.",
                "pc1_explained_variance": float(pc_explained_variance[0]),
                "pc2_explained_variance": float(pc_explained_variance[1]),
                "pc_explained_variance_sum": float(pc_explained_variance.sum()),
                "events": event_details,
            },
            "warnings": warnings,
        }

    @staticmethod
    def drift(reference: SampleMatrix, query: SampleMatrix, params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
        # All drift statistics must use the same reference-scaled coordinates.
        # Otherwise coverage is measured in raw units while MMD/centroid/
        # covariance shifts are measured in standardized units.
        drift_params = dict(params or {})
        if not drift_params.get("preprocess"):
            drift_params["preprocess"] = "standardized"
        result = AnalysisEngine.coverage(reference, query, drift_params, progress)
        distances = result["arrays"]["distances"]
        ref, qry, drift_warnings, _keep = _preprocess_reference_query(reference.values, query.values, drift_params, "standardized")
        limit = min(_int_param(drift_params, "distribution_samples", 500, 20), 2_000)
        a = ref[_bounded_indices(ref.shape[0], limit)]
        b = qry[_bounded_indices(qry.shape[0], limit)]
        combined = np.vstack([a, b])
        combined_distances = _pairwise_matrix(combined, "euclidean")
        nonzero = combined_distances[combined_distances > np.finfo(np.float64).eps]
        bandwidth = _float_param(drift_params, "bandwidth", float(np.median(nonzero)) if nonzero.size else 1.0, np.finfo(np.float64).eps)
        gamma = 1.0 / (2.0 * bandwidth * bandwidth)
        kernels = _safe_import("sklearn.metrics.pairwise", "scikit-learn")
        kxx = kernels.rbf_kernel(a, a, gamma=gamma)
        kyy = kernels.rbf_kernel(b, b, gamma=gamma)
        kxy = kernels.rbf_kernel(a, b, gamma=gamma)
        mmd2 = max(float(kxx.mean() + kyy.mean() - 2.0 * kxy.mean()), 0.0)
        centroid_distance = float(np.linalg.norm(a.mean(axis=0) - b.mean(axis=0)))
        covariance_shift: float | None
        if a.shape[0] < 2 or b.shape[0] < 2:
            covariance_shift = None
            drift_warnings.append("covariance shift requires at least two samples in both sets")
        else:
            covariance_a = np.atleast_2d(np.cov(a, rowvar=False))
            covariance_b = np.atleast_2d(np.cov(b, rowvar=False))
            covariance_shift = float(np.linalg.norm(covariance_a - covariance_b) / max(np.linalg.norm(covariance_a), 1e-15))
        result["preview"] = {**result["preview"], "kind": "drift", "mean_distance": float(distances.mean()), "median_distance": float(np.median(distances)), "max_distance": float(distances.max()), "mmd": float(np.sqrt(mmd2)), "mmd_squared": mmd2, "bandwidth": bandwidth, "centroid_distance": centroid_distance, "covariance_shift": covariance_shift}
        result["warnings"] = list(dict.fromkeys([*result.get("warnings", []), *drift_warnings]))
        return result

    @staticmethod
    def sensitivity(runs: list[tuple[dict, SampleMatrix]], params: dict, progress: Callable[[float, str], None] | None = None) -> dict:
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
