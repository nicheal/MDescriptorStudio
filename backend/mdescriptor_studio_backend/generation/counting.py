"""Strict unique-environment counting — the single definition family.

One definition backs the round metric, the discovery-rate /
screening-bottleneck stops and the local selection strategy (P0-02 /
P1-01 / P1-03 / 2026-09-30 local-selection audit): the greedy strict dedup
in :func:`count_strict_unique_environments` (gen-4, visit-order defined)
and its permutation-invariant counterpart
:func:`count_strict_unique_environments_v2` (gen-5, canonical order).

Space contract shared by both: every batch-internal comparison
(already-counted dedup, within-candidate dedup) runs in the archive's
scaled space — the space the threshold is defined in — while the
frozen-archive mask queries RAW rows because ``nearest_per_row`` applies
the scaling itself. Never pass scaled rows to the archive mask, that would
scale twice.
"""

from __future__ import annotations

import numpy as np

from ._distance import min_sqdist_to_set
from ..analysis.sampling import apply_scaling


def _candidate_novel_rows(
    rows: np.ndarray,
    archive_novel: np.ndarray,
    counted: np.ndarray | None,
    threshold: float,
) -> np.ndarray:
    """The rows one candidate contributes to the strict novel set right now.

    Shared by the round metric and the local selection strategy so both use
    one definition: archive-near rows are dropped, rows within ``threshold``
    of an already-counted row are dropped, and within-candidate duplicates
    are deduped greedily in fixed row order.

    Space contract (2026-09-30 audit P0): ``rows`` must already live in the
    space every batch-internal comparison is defined in (the archive's
    scaled space) and ``archive_novel`` must be a mask over those same row
    indices — computed by querying the archive with the RAW rows, since
    ``nearest_per_row`` scales internally.
    """
    fresh = rows[archive_novel]
    if fresh.shape[0] and counted is not None:
        d2 = min_sqdist_to_set(fresh, counted, workers=1)
        fresh = fresh[np.sqrt(np.clip(d2, 0.0, None)) > threshold]
    kept: list[np.ndarray] = []
    kept_matrix: np.ndarray | None = None
    for row in fresh:
        if kept_matrix is not None:
            d2 = min_sqdist_to_set(row[None, :], kept_matrix, workers=1)[0]
            if not np.sqrt(max(float(d2), 0.0)) > threshold:
                continue
        kept.append(row)
        kept_matrix = row[None, :] if kept_matrix is None else np.vstack([kept_matrix, row[None, :]])
    if not kept:
        return np.empty((0, rows.shape[1]), dtype=np.float64)
    return np.vstack(kept) if len(kept) > 1 else kept[0][None, :]


def count_strict_unique_environments(
    selected: list[int],
    atomic_values: np.ndarray,
    row_offsets: np.ndarray,
    threshold: float,
    local_archive,
) -> int:
    """Strict greedy union dedup of novel environments over ``selected``.

    Rows are visited in fixed candidate order, then fixed atomic row order.
    A row is counted only when its distance to the frozen archive AND to
    every already-counted novel row is strictly greater than ``threshold``;
    only counted rows join the novel set. Two accepted structures that found
    the same new region therefore contribute it once, within-candidate
    duplicates count once, and archive-near rows can never repel a later
    novel row. Environments accepted in earlier rounds reach this comparison
    only through the formal local_archive update. This one function is the
    single definition behind the round metric, the discovery-rate stop and
    the local selection strategy (P0-02/P1-01/P1-03).

    Every batch-internal comparison (already-counted dedup, within-candidate
    dedup) runs in the archive's scaled space — the space the threshold is
    defined in and the local strategy optimizes in (2026-09-30 audit P0: a
    raw-space counter over-counted when the archive scale > 1 and
    under-counted when < 1). The frozen-archive mask still queries raw rows
    because ``nearest_per_row`` applies the scaling itself — never pass it
    scaled rows, that would scale twice.

    The count is defined for the given visit order (selection order, then
    stored row order): threshold nearness is not transitive, so permuting
    candidates or atomic rows may legitimately change the greedy count. A
    permutation-invariant metric needs stable candidate identities and a
    versioned redefinition, not a silent change here.
    """
    atomic_values = np.asarray(atomic_values, dtype=np.float64)
    threshold = float(threshold)
    scaled = apply_scaling(local_archive.scaling, atomic_values)
    counted: np.ndarray | None = None
    unique = 0
    for index in selected:
        lo, hi = int(row_offsets[index]), int(row_offsets[index + 1])
        rows = atomic_values[lo:hi]
        if rows.shape[0] == 0:
            continue
        archive_novel = local_archive.nearest_per_row(rows) > threshold
        block = _candidate_novel_rows(scaled[lo:hi], archive_novel, counted, threshold)
        if block.shape[0]:
            unique += int(block.shape[0])
            counted = block if counted is None else np.vstack([counted, block])
    return unique


def _canonical_row_order(rows: np.ndarray) -> np.ndarray:
    """Canonical within-candidate row order: lexicographic by descriptor row.

    ``np.lexsort`` with the column order reversed makes column 0 the primary
    key; it is stable, so exact duplicate rows keep their stored order and the
    permutation is deterministic. Per-dimension scaling with positive scales
    preserves the first-differing coordinate and the sign of that difference,
    so the order is identical whether rows are raw or scaled.
    """
    if rows.shape[0] <= 1:
        return np.arange(rows.shape[0])
    return np.lexsort(rows.T[::-1])


def count_strict_unique_environments_v2(
    selected: list[int],
    candidate_ids: list[str],
    atomic_values: np.ndarray,
    row_offsets: np.ndarray,
    threshold: float,
    local_archive,
) -> int:
    """Permutation-invariant strict count (gen-5 ``strict_unique_v2``).

    Same greedy strict-dedup semantics as :func:`count_strict_unique_environments`
    — a row is counted only when strictly farther than ``threshold`` from the
    frozen archive and from every already-counted novel row — but the visit
    order is CANONICAL instead of arrival-defined: candidates are processed
    sorted by ``candidate_id`` (the stable unique identity invariant added by
    the 2026-10-02 audit P0 fix) and each candidate's atomic rows are processed
    in :func:`_canonical_row_order` order. The count is therefore a pure
    function of the selected row SET — permuting the arrival order or the
    stored atom-row order cannot change it (both permutations are pinned by
    tests). Canonical greedy is still greedy: it is not a maximum-spacing
    representative set, and not a connected-component clustering — those are
    different metric definitions (2026-09-30 local-selection audit §7.6).

    The gen-4 visit-order count stays on the same record beside this one;
    published R4 numbers are gen-4-caliber and remain valid. Space contract
    identical to the gen-4 counter: batch-internal comparisons in the
    archive's scaled space, frozen-archive mask queried with RAW rows.
    """
    atomic_values = np.asarray(atomic_values, dtype=np.float64)
    threshold = float(threshold)
    scaled = apply_scaling(local_archive.scaling, atomic_values)
    blocks: list[tuple[str, int, int]] = []
    for index in selected:
        lo, hi = int(row_offsets[index]), int(row_offsets[index + 1])
        if hi > lo:
            blocks.append((candidate_ids[index], lo, hi))
    blocks.sort(key=lambda item: item[0])
    counted: np.ndarray | None = None
    unique = 0
    for _, lo, hi in blocks:
        order = _canonical_row_order(scaled[lo:hi])
        rows = atomic_values[lo:hi][order]
        if rows.shape[0] == 0:
            continue
        archive_novel = local_archive.nearest_per_row(rows) > threshold
        block = _candidate_novel_rows(scaled[lo:hi][order], archive_novel, counted, threshold)
        if block.shape[0]:
            unique += int(block.shape[0])
            counted = block if counted is None else np.vstack([counted, block])
    return unique
