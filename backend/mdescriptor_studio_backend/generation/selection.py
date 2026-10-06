"""Batch selection strategies for the generation loop.

Two strategies share one elite-order helper so a pool cutoff or budget
that falls inside an equal-fitness group leaves the same elite membership
and pick order in both:

- ``structure_fps_v1`` — novelty ranking, then farthest-point sampling
  inside the fitness-elite pool (the historical baseline).
- ``local_incremental_maximin_v1`` — iterative acceptance by marginal
  strictly-new-environment count, ties broken on maximin structure
  distance (optimizes the strict metric the benchmark reports).

``SELECTION_STRATEGIES`` is the request-facing vocabulary; the engine
constructor validates against it and ``models.parse_request`` validates
the same tuple at submit time.
"""

from __future__ import annotations

import numpy as np

from ._distance import min_sqdist_to_set
from ..analysis.sampling import apply_scaling
from ..analysis.sampling.fps import farthest_point_sampling
from .counting import _candidate_novel_rows


def _fitness_elite_order(fitness: np.ndarray, finite: np.ndarray, pool_size: int) -> np.ndarray:
    """Descending-fitness elite order: stable ascending argsort, reversed.

    This is ``structure_fps_v1``'s historical order, kept bit-identical on
    purpose — equal-fitness ties resolve to the *later* input index first.
    Both selection strategies share this one helper so a pool cutoff or
    budget that falls inside an equal-fitness group leaves the same elite
    membership and pick order in both (2026-09-30 audit P1: the local
    strategy used to sort ``-fitness`` stably and kept *earlier* ties, so
    the two strategies could pick different identities from one tie group).
    """
    return finite[np.argsort(fitness[finite], kind="stable")[::-1][:pool_size]]


def select_diverse_batch(
    fitness: np.ndarray,
    candidate_values: np.ndarray,
    budget: int,
    *,
    top_pool_factor: int = 4,
) -> list[int]:
    """Novelty ranking, then farthest-point sampling inside the batch (§16).

    Without the FPS pass the top-K could be K near-identical structures that
    all sit far from the archive — the budget would be spent on one region.
    FPS runs on the fitness-elite pool only (top ``budget * top_pool_factor``),
    so selection stays a coverage decision among the candidates that already
    scored well, not a re-ranking by geometry alone.
    """
    fitness = np.asarray(fitness, dtype=np.float64)
    candidate_values = np.asarray(candidate_values, dtype=np.float64)
    finite = np.flatnonzero(np.isfinite(fitness))
    if finite.size == 0 or budget <= 0:
        return []
    pool_size = min(max(int(budget) * int(top_pool_factor), int(budget)), int(finite.size))
    ranked = _fitness_elite_order(fitness, finite, pool_size)
    pool = candidate_values[ranked]
    picked = farthest_point_sampling(pool, n_samples=min(int(budget), pool.shape[0]))
    return [int(ranked[i]) for i in picked.indices]


SELECTION_STRATEGIES = ("structure_fps_v1", "local_incremental_maximin_v1")


def select_local_incremental_batch(
    fitness: np.ndarray,
    candidate_values: np.ndarray,
    atomic_values: np.ndarray,
    row_offsets: np.ndarray,
    budget: int,
    *,
    local_archive,
    threshold: float,
    top_pool_factor: int = 4,
    max_candidates: int = 128,
) -> list[int]:
    """``local_incremental_maximin_v1``: batch selection in environment space.

    A fixed fitness-elite sub-pool (same shape as the structure-FPS baseline)
    is accepted iteratively: each step picks the candidate with the largest
    marginal count of strictly new environments versus the frozen archive
    plus the environments already claimed by this batch (exactly the
    ``count_strict_unique_environments`` semantics, so the strategy optimizes
    the metric the benchmark reports). Ties break on the maximin structure
    distance to the already-selected candidates — before the first selection
    the ranked order decides, which is the shared descending-fitness elite
    order of :func:`_fitness_elite_order` (ties in reverse input order,
    identical to the FPS baseline so equal-fitness cutoffs compare fairly) —
    and zero-gain candidates still fill the batch so accepted counts stay
    comparable with the baseline at equal budget.

    Compute is bounded by design (audit R3.3): the elite pool caps the
    per-step candidate sweep at ``max_candidates`` (on top of the
    ``budget * top_pool_factor`` bound) — but never below the requested
    budget, so acceptance counts stay equal to the uncapped FPS baseline
    whenever the budget exceeds the cap (2026-09-30 audit case J) —, all
    distance passes run through the blocked ``min_sqdist_to_set`` kernel (no
    materialized N×M matrix), and the batch memory holds only the counted
    novel rows. Atomic rows are scaled once with the archive's own scaling
    so every comparison lives in the space the threshold is defined in.
    """
    fitness = np.asarray(fitness, dtype=np.float64)
    finite = np.flatnonzero(np.isfinite(fitness))
    if finite.size == 0 or budget <= 0:
        return []
    pool_size = min(
        max(int(budget) * int(top_pool_factor), int(budget)),
        int(finite.size),
        # The elite cap bounds the sweep, but truncating the pool below the
        # budget would silently accept fewer candidates than the FPS
        # baseline at the same budget — the cap may only limit the elite
        # surplus beyond it.
        max(int(budget), max(1, int(max_candidates))),
    )
    ranked = _fitness_elite_order(fitness, finite, pool_size)
    candidate_values = np.asarray(candidate_values, dtype=np.float64)
    atomic_values = np.asarray(atomic_values, dtype=np.float64)
    threshold = float(threshold)

    # One scaling pass: batch-internal distances must live in the same space
    # the frozen archive's threshold is defined in (nearest_per_row scales
    # internally, so the frozen-archive mask below stays consistent).
    scaled_rows = apply_scaling(local_archive.scaling, atomic_values)
    archive_novel = local_archive.nearest_per_row(atomic_values) > threshold

    memory: np.ndarray | None = None
    selected: list[int] = []
    selected_structures: list[np.ndarray] = []
    remaining = [int(index) for index in ranked]

    def _marginal_gain(index: int) -> tuple[int, np.ndarray]:
        lo, hi = int(row_offsets[index]), int(row_offsets[index + 1])
        rows = scaled_rows[lo:hi]
        if rows.shape[0] == 0:
            return 0, np.empty((0, rows.shape[1] if rows.ndim == 2 else 0), dtype=np.float64)
        block = _candidate_novel_rows(rows, archive_novel[lo:hi], memory, threshold)
        return int(block.shape[0]), block

    while len(selected) < int(budget) and remaining:
        best_index: int | None = None
        best_gain = -1
        best_struct_dist = -np.inf
        best_block: np.ndarray | None = None
        for index in remaining:
            gain, block = _marginal_gain(index)
            structure = candidate_values[index]
            if selected_structures:
                d2 = min_sqdist_to_set(structure[None, :], np.asarray(selected_structures), workers=1)[0]
                struct_dist = float(np.sqrt(max(float(d2), 0.0)))
            else:
                struct_dist = 0.0  # first pick: ranked order decides ties
            if gain > best_gain or (gain == best_gain and struct_dist > best_struct_dist):
                best_index, best_gain, best_struct_dist, best_block = index, gain, struct_dist, block
        assert best_index is not None
        selected.append(best_index)
        remaining.remove(best_index)
        selected_structures.append(candidate_values[best_index])
        if best_block is not None and best_block.shape[0]:
            memory = best_block if memory is None else np.vstack([memory, best_block])
    return selected
