"""Generic farthest point sampling (FPS) on a plain feature matrix.

The module is deliberately independent of ASE, the GUI, and any descriptor
implementation: it only ever sees a 2-D matrix ``X`` with shape
``(n_structures, n_features)``.

Math (greedy max-min covering):

    d_i^(k)   = min_{j in S_k} || x_i - x_j ||_2
    i_{k+1}   = argmax_i d_i^(k)
    d^(k+1)   = min(d^(k), || x_i - x_{i_{k+1}} ||)

The loop keeps squared distances only — ``sqrt`` is applied once, when the
result reports true Euclidean distances.  Each step updates ``d`` against a
single point, so the total cost is O(N·K·D) time and O(N·D + N) memory; a full
N×N distance matrix is never built.

Warm start: when ``selected_features`` (an existing training set) is given,
``d`` is initialised from the distance to that set, so FPS picks the candidate
regions the existing data covers *worst* instead of merely spreading points
among themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .grouping import group_sizes, sqrt_quota, validate_group_labels

INITIAL_MODES = ("center", "first", "random")

# Candidate/reference block side for the warm-start initialisation: each
# Gram block costs block² floats (~33 MB in float64), keeping the N×M
# cross-distance pass memory-bounded without a SciPy dependency.
_BLOCK = 2048

_STOP_TARGET = "target"
_STOP_MIN_DISTANCE = "min_distance"
_STOP_EXHAUSTED = "exhausted"
_STOP_COVERAGE = "coverage"


@dataclass(frozen=True)
class FPSResult:
    """Selection plus the coverage evidence needed to judge it.

    ``indices`` is in *selection order* so ``selection_distances[k]`` — the
    distance of the k-th pick to everything selected (or warm-started) before
    it — lines up with it.  ``coverage_*_curve`` record the covering radius
    ``R_k = max_i min_{s in S_k} ||x_i - s||``, the mean residual, and the
    explained-spread fraction

        R²_coverage = 1 - Σ_i d_i² / Σ_i ||x_i - x̄||²

    after each pick.  Their plateau is the signal that more samples stop
    paying off, and ``R²_coverage`` is what ``target_coverage`` stops on.
    """

    indices: np.ndarray
    nearest_distances: np.ndarray
    selection_distances: np.ndarray
    coverage_radius_curve: np.ndarray
    coverage_mean_curve: np.ndarray
    coverage_r2_curve: np.ndarray
    coverage_radius: float
    coverage_r2: float
    total_spread: float
    n_selected: int
    stopped_by: str


def farthest_point_sampling(
    features: np.ndarray,
    n_samples: int = 100,
    min_distance: float = 0.0,
    initial: str | int = "center",
    selected_features: np.ndarray | None = None,
    *,
    target_coverage: float | None = None,
    seed: int | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> FPSResult:
    """Greedily select up to ``n_samples`` rows of ``features``.

    ``initial`` seeds the first pick: ``"center"`` (the row closest to the
    descriptor-space centroid — deterministic and independent of input order),
    ``"first"``, ``"random"`` (uses ``seed``), or an explicit row index.
    ``min_distance`` stops early once the farthest remaining candidate is
    closer than the threshold, i.e. no candidate adds meaningful new coverage.

    ``target_coverage`` stops once ``R²_coverage`` reaches that fraction, which
    turns ``n_samples`` into an upper bound and lets the data decide the budget.
    """
    x = _validate_matrix(features, "features")
    n = x.shape[0]
    if isinstance(n_samples, bool) or not isinstance(n_samples, (int, np.integer)):
        raise ValueError("n_samples must be an integer")
    if n_samples < 1:
        raise ValueError("n_samples must be >= 1")
    if min_distance < 0:
        raise ValueError("min_distance must be >= 0")
    if target_coverage is not None:
        target_coverage = float(target_coverage)
        if not np.isfinite(target_coverage) or not 0.0 < target_coverage < 1.0:
            raise ValueError("target_coverage must be a fraction strictly between 0 and 1")
    start_mode = _check_initial(initial)
    target = min(int(n_samples), n)
    rng = np.random.default_rng(seed)
    total_spread = _total_spread(x)

    x_sq = np.einsum("ij,ij->i", x, x)
    if selected_features is not None:
        existing = _validate_matrix(selected_features, "selected_features")
        if existing.shape[1] != x.shape[1]:
            raise ValueError(
                f"candidate features have dimension {x.shape[1]} but the existing set has {existing.shape[1]}"
            )
        # Warm start: the first pick is the candidate farthest from everything
        # the existing set already covers; ``initial`` does not apply.
        d2 = _min_sqdist_to_set(x, x_sq, existing)
        if target_coverage is not None and _r2_coverage(d2, total_spread) >= target_coverage:
            # The existing set already explains the requested spread: adding
            # candidates would not buy coverage, so the honest answer is none.
            return _empty_result(d2, total_spread, _STOP_COVERAGE)
        first = int(np.argmax(d2))
    else:
        d2 = None
        first = _resolve_initial(x, x_sq, start_mode, rng)
    return _run_loop(x, x_sq, d2, first, target, float(min_distance), int(n_samples), total_spread, target_coverage, progress)


def _total_spread(x: np.ndarray) -> float:
    """Σ_i ||x_i - x̄||², the denominator of R²_coverage."""
    return float(x.shape[0] * np.sum(x.var(axis=0)))


def _r2_coverage(d2: np.ndarray, total_spread: float) -> float:
    if total_spread <= 0.0:
        # A constant feature space has no spread to explain; nothing to cover.
        return 1.0
    return 1.0 - float(np.clip(d2, 0.0, None).sum()) / total_spread


def _empty_result(d2: np.ndarray, total_spread: float, stopped_by: str) -> FPSResult:
    radius, _mean = _residual_stats(d2)
    return FPSResult(
        indices=np.empty(0, dtype=np.int64),
        nearest_distances=np.sqrt(np.clip(d2, 0.0, None)),
        selection_distances=np.empty(0, dtype=np.float64),
        coverage_radius_curve=np.empty(0, dtype=np.float64),
        coverage_mean_curve=np.empty(0, dtype=np.float64),
        coverage_r2_curve=np.empty(0, dtype=np.float64),
        coverage_radius=radius,
        coverage_r2=_r2_coverage(d2, total_spread),
        total_spread=total_spread,
        n_selected=0,
        stopped_by=stopped_by,
    )


def _validate_matrix(values: np.ndarray, name: str) -> np.ndarray:
    x = np.asarray(values, dtype=np.float64)
    if x.ndim == 1:
        x = x.reshape(-1, 1)
    if x.ndim != 2 or x.shape[0] == 0 or x.shape[1] == 0:
        raise ValueError(f"{name} must be a non-empty 2D matrix with shape (n_structures, n_features)")
    if not np.isfinite(x).all():
        bad = int((~np.isfinite(x)).sum())
        raise ValueError(f"{name} contains {bad} NaN or Inf value(s)")
    return x


def _check_initial(initial: str | int) -> str | int:
    if isinstance(initial, bool):
        raise ValueError("initial must be 'center', 'first', 'random', or a structure index")
    if isinstance(initial, (int, np.integer)):
        if initial < 0:
            raise ValueError("initial structure index must be >= 0")
        return int(initial)
    if isinstance(initial, str) and initial.lower() in INITIAL_MODES:
        return initial.lower()
    raise ValueError(f"initial must be one of {', '.join(INITIAL_MODES)} or a structure index")


def _resolve_initial(x: np.ndarray, x_sq: np.ndarray, initial: str | int, rng: np.random.Generator) -> int:
    if isinstance(initial, int):
        if initial >= x.shape[0]:
            raise ValueError(f"initial structure index {initial} is out of range for {x.shape[0]} structures")
        return initial
    if initial == "center":
        # Nearest real structure to the descriptor-space centroid: deterministic,
        # order-independent for distinct data, and starts from typical environments.
        return int(np.argmin(_sqdist_to_point(x, x_sq, x.mean(axis=0))))
    if initial == "first":
        return 0
    return int(rng.integers(x.shape[0]))


def _sqdist_to_point(x: np.ndarray, x_sq: np.ndarray, point: np.ndarray) -> np.ndarray:
    # |x - p|² = |x|² - 2·x·p + |p|²: a BLAS mat-vec with O(N) memory instead
    # of an O(N·D) temporary.  Clamping removes tiny negative rounding noise.
    return np.maximum(x_sq - 2.0 * (x @ point) + float(point @ point), 0.0)


def _min_sqdist_to_set(x: np.ndarray, x_sq: np.ndarray, existing: np.ndarray) -> np.ndarray:
    d2 = np.full(x.shape[0], np.inf, dtype=np.float64)
    for ref_start in range(0, existing.shape[0], _BLOCK):
        ref = existing[ref_start : ref_start + _BLOCK]
        ref_sq = np.einsum("ij,ij->i", ref, ref)
        for cand_start in range(0, x.shape[0], _BLOCK):
            cand_slice = slice(cand_start, cand_start + _BLOCK)
            gram = x[cand_slice] @ ref.T
            block = np.maximum(x_sq[cand_slice, None] + ref_sq - 2.0 * gram, 0.0)
            np.minimum(d2[cand_slice], block.min(axis=1), out=d2[cand_slice])
    return d2


def _run_loop(
    x: np.ndarray,
    x_sq: np.ndarray,
    d2: np.ndarray | None,
    first: int,
    target: int,
    min_distance: float,
    requested: int,
    total_spread: float,
    target_coverage: float | None,
    progress: Callable[[float, str], None] | None,
) -> FPSResult:
    n = x.shape[0]
    indices = np.empty(target, dtype=np.int64)
    selection_distances = np.empty(target, dtype=np.float64)
    radius_curve = np.empty(target, dtype=np.float64)
    mean_curve = np.empty(target, dtype=np.float64)
    r2_curve = np.empty(target, dtype=np.float64)

    if d2 is None:
        d2 = _sqdist_to_point(x, x_sq, x[first])
        selection_distances[0] = 0.0
    else:
        selection_distances[0] = float(np.sqrt(max(d2[first], 0.0)))
    indices[0] = first
    # -1 sentinel: selected rows can never win argmax again, while duplicate
    # (distance-zero) rows remain selectable under the default min_distance=0.
    d2[first] = -1.0
    radius_curve[0], mean_curve[0] = _residual_stats(d2)
    r2_curve[0] = _r2_coverage(d2, total_spread)
    report_every = max(1, target // 100)
    if progress:
        progress(1.0 / max(target, 1), "farthest-point sampling")

    stopped_by = _STOP_TARGET if requested <= n else _STOP_EXHAUSTED
    filled = 1
    for step in range(1, target):
        if target_coverage is not None and r2_curve[step - 1] >= target_coverage:
            # Checked before picking, so the run stops on the state it already
            # reached instead of adding one sample past the target.
            stopped_by = _STOP_COVERAGE
            break
        nxt = int(np.argmax(d2))
        nearest = float(np.sqrt(max(d2[nxt], 0.0)))
        if nearest < min_distance:
            stopped_by = _STOP_MIN_DISTANCE
            break
        indices[step] = nxt
        selection_distances[step] = nearest
        np.minimum(d2, _sqdist_to_point(x, x_sq, x[nxt]), out=d2)
        d2[nxt] = -1.0
        radius_curve[step], mean_curve[step] = _residual_stats(d2)
        r2_curve[step] = _r2_coverage(d2, total_spread)
        filled = step + 1
        if progress and (step % report_every == 0 or step == target - 1):
            progress(step / max(target, 1), "farthest-point sampling")

    # A run that used its whole budget and landed exactly on the target stopped
    # for the coverage reason, not for lack of budget — report the useful one.
    if stopped_by == _STOP_TARGET and target_coverage is not None and r2_curve[filled - 1] >= target_coverage:
        stopped_by = _STOP_COVERAGE

    return FPSResult(
        indices=indices[:filled],
        # Per-structure residual in the original row order; selected rows clip
        # from the -1 sentinel to their true residual of zero.
        nearest_distances=np.sqrt(np.clip(d2, 0.0, None)),
        selection_distances=selection_distances[:filled],
        coverage_radius_curve=radius_curve[:filled],
        coverage_mean_curve=mean_curve[:filled],
        coverage_r2_curve=r2_curve[:filled],
        coverage_radius=float(radius_curve[filled - 1]),
        coverage_r2=float(r2_curve[filled - 1]),
        total_spread=total_spread,
        n_selected=int(filled),
        stopped_by=stopped_by,
    )


def _residual_stats(d2: np.ndarray) -> tuple[float, float]:
    """Covering radius and mean residual after one selection.

    The -1 selection sentinels clip to zero, which is their true residual.
    """
    clipped = np.clip(d2, 0.0, None)
    return float(np.sqrt(clipped.max())), float(np.sqrt(clipped).mean())


def coverage_statistics(nearest_distances: np.ndarray) -> dict[str, float]:
    """Summary residuals of a selection: mean, quantiles, and covering radius."""
    d = np.asarray(nearest_distances, dtype=np.float64).reshape(-1)
    if d.size == 0:
        raise ValueError("nearest_distances must not be empty")
    return {
        "mean": float(d.mean()),
        "p50": float(np.quantile(d, 0.50)),
        "p90": float(np.quantile(d, 0.90)),
        "p95": float(np.quantile(d, 0.95)),
        "p99": float(np.quantile(d, 0.99)),
        "max": float(d.max()),
    }


@dataclass(frozen=True)
class GroupedFPSResult(FPSResult):
    """Grouped selection: per-pick fields stay group-local, coverage is global.

    ``selection_distances`` measure each pick against its *own* group (plus the
    warm-start set), while ``nearest_distances`` and the coverage curves are
    computed over the merged selection against the full candidate matrix — the
    coverage of the final set is a global property, not a per-group one.
    """

    group_names: list[str]
    group_sizes: np.ndarray
    group_quota: np.ndarray


def grouped_farthest_point_sampling(
    features: np.ndarray,
    groups: np.ndarray,
    n_samples: int = 100,
    min_distance: float = 0.0,
    initial: str | int = "center",
    selected_features: np.ndarray | None = None,
    *,
    target_coverage: float | None = None,
    seed: int | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> GroupedFPSResult:
    """Run FPS independently per group under a √N_g quota.

    Every non-empty group receives at least one sample whenever the target can
    cover all groups, so minority composition classes are never drowned out.
    The warm-start set (when given) initializes the distances of *every* group,
    i.e. each group is sampled against the full existing coverage.

    ``target_coverage`` is evaluated on the *merged* selection: it stops the
    remaining groups once the combined coverage reaches the target, which is the
    honest reading of a global coverage goal for a composition-stratified set.
    """
    x = _validate_matrix(features, "features")
    n = x.shape[0]
    if isinstance(n_samples, bool) or not isinstance(n_samples, (int, np.integer)):
        raise ValueError("n_samples must be an integer")
    if n_samples < 1:
        raise ValueError("n_samples must be >= 1")
    if min_distance < 0:
        raise ValueError("min_distance must be >= 0")
    if target_coverage is not None:
        target_coverage = float(target_coverage)
        if not np.isfinite(target_coverage) or not 0.0 < target_coverage < 1.0:
            raise ValueError("target_coverage must be a fraction strictly between 0 and 1")
    start_mode = _check_initial(initial)
    if not isinstance(start_mode, str):
        # A raw structure index is a property of the full matrix and has no
        # meaning inside a group slice; grouped runs seed every group the same
        # string-initialized way.
        raise ValueError("initial must be 'center', 'first', or 'random' for grouped sampling")
    labels = validate_group_labels(groups, n)
    names, sizes = group_sizes(labels)
    quota = sqrt_quota(sizes, min(int(n_samples), n))

    if selected_features is not None:
        existing = _validate_matrix(selected_features, "selected_features")
        if existing.shape[1] != x.shape[1]:
            raise ValueError(
                f"candidate features have dimension {x.shape[1]} but the existing set has {existing.shape[1]}"
            )
    else:
        existing = None

    x_sq = np.einsum("ij,ij->i", x, x)
    total_spread = _total_spread(x)
    # Global residual tracker for the merged coverage curve: seeded from the
    # warm-start set so R(k) answers "distance to existing ∪ picked so far".
    global_d2 = _min_sqdist_to_set(x, x_sq, existing) if existing is not None else np.full(n, np.inf, dtype=np.float64)
    radius_curve = np.empty(quota.sum(), dtype=np.float64)
    mean_curve = np.empty(quota.sum(), dtype=np.float64)
    r2_curve = np.empty(quota.sum(), dtype=np.float64)
    picked_indices: list[np.ndarray] = []
    picked_distances: list[np.ndarray] = []
    filled = 0
    stopped_by = _STOP_TARGET if n_samples <= n else _STOP_EXHAUSTED

    for group_index, name in enumerate(names):
        members = np.flatnonzero(labels == name)
        count = int(quota[group_index])
        if count == 0:
            continue
        if target_coverage is not None and _r2_coverage(global_d2, total_spread) >= target_coverage:
            # The merged selection already explains the requested spread; the
            # remaining groups would only add redundancy.
            stopped_by = _STOP_COVERAGE
            break
        run = farthest_point_sampling(
            x[members],
            n_samples=count,
            min_distance=min_distance,
            initial=start_mode,
            selected_features=existing,
            seed=seed,
        )
        if run.n_selected < count:
            stopped_by = _STOP_MIN_DISTANCE
        picked_indices.append(members[run.indices])
        picked_distances.append(run.selection_distances)
        for index in picked_indices[-1].tolist():
            np.minimum(global_d2, _sqdist_to_point(x, x_sq, x[index]), out=global_d2)
            radius_curve[filled], mean_curve[filled] = _residual_stats(global_d2)
            r2_curve[filled] = _r2_coverage(global_d2, total_spread)
            filled += 1
        if progress:
            progress(filled / max(int(quota.sum()), 1), "grouped farthest-point sampling")

    indices = np.concatenate(picked_indices) if picked_indices else np.empty(0, dtype=np.int64)
    selection_distances = np.concatenate(picked_distances) if picked_distances else np.empty(0, dtype=np.float64)
    # A run that used its whole budget and landed exactly on the target stopped
    # for the coverage reason, not for lack of budget — report the useful one.
    if stopped_by == _STOP_TARGET and target_coverage is not None and filled and r2_curve[filled - 1] >= target_coverage:
        stopped_by = _STOP_COVERAGE
    return GroupedFPSResult(
        indices=indices,
        nearest_distances=np.sqrt(np.clip(global_d2, 0.0, None)),
        selection_distances=selection_distances,
        coverage_radius_curve=radius_curve[:filled],
        coverage_mean_curve=mean_curve[:filled],
        coverage_r2_curve=r2_curve[:filled],
        coverage_radius=float(radius_curve[filled - 1]) if filled else 0.0,
        coverage_r2=float(r2_curve[filled - 1]) if filled else 0.0,
        total_spread=total_spread,
        n_selected=int(filled),
        stopped_by=stopped_by,
        group_names=names,
        group_sizes=sizes,
        group_quota=quota,
    )
