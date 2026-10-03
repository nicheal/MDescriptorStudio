"""Degenerate-pair search: environments the descriptor collapses that physics does not.

The dangerous region of a descriptor (review 2026-10-03) is where
d_D(x1, x2) is small while the structures (or their energies) differ
substantially - the collapse that makes "far in descriptor space" unreliable as
a novelty signal.  Rather than scanning all pairs, the search follows the
descriptor's own claim: candidate pairs are k-nearest neighbors in descriptor
space, and only those are tested against structural and energetic difference.

The service owns sample selection and passes an already-bounded matrix; the
structural distance arrives as a lazy per-pair callable (structure granularity:
sorted minimum-image pair-distance fingerprints; atom granularity: local
environment fingerprints), so only kNN candidates pay for geometry.  A pair
with different atom counts is structurally infinite by construction and
reported with an explicit atom_count reason.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, ANALYSIS_INSUFFICIENT_SAMPLES, AppError


def _topk_pairs(values: np.ndarray, k: int, block: int = 128) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """k-nearest-neighbor pairs in descriptor space, computed in row blocks.

    Returns deduplicated unordered candidate pairs and their descriptor
    distances.  Chunking keeps the pairwise matrix bounded for large pools.
    """
    n = values.shape[0]
    pair_set: dict[tuple[int, int], float] = {}
    for start in range(0, n, block):
        stop = min(start + block, n)
        distances = np.linalg.norm(values[start:stop, None, :] - values[None, :, :], axis=2)
        for row_in_block in range(stop - start):
            row = start + row_in_block
            row_distances = distances[row_in_block]
            row_distances[row] = np.inf
            neighbor_count = min(k, n - 1)
            nearest = np.argpartition(row_distances, neighbor_count - 1)[:neighbor_count]
            for other in nearest:
                a, b = (row, int(other)) if row < int(other) else (int(other), row)
                distance = float(row_distances[other])
                if (a, b) not in pair_set or distance < pair_set[(a, b)]:
                    pair_set[(a, b)] = distance
    pairs = sorted(pair_set)
    pair_a = np.asarray([a for a, _ in pairs], dtype=np.int64)
    pair_b = np.asarray([b for _, b in pairs], dtype=np.int64)
    pair_d = np.asarray([pair_set[pair] for pair in pairs], dtype=np.float64)
    return pair_a, pair_b, pair_d


def _quantile_or_none(values: np.ndarray, q: float) -> float | None:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None
    return float(np.quantile(finite, q))


def degeneracy_search(
    values: np.ndarray,
    structural_distance: Callable[[int, int], float],
    params: dict,
    sample_ids: list[str] | None = None,
    energy: np.ndarray | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> dict:
    """Search descriptor-close pairs that are structurally or energetically far.

    ``values`` is the bounded raw descriptor matrix (the search runs in the
    descriptor's own metric; scaling is deliberately not applied).
    ``structural_distance(i, j)`` indexes the same rows and may return ``inf``
    for structurally incomparable pairs.  ``energy`` is a per-sample physical
    quantity (energy per atom at structure granularity) used as the second
    relevance signal when present.
    """
    values = np.asarray(values, dtype=np.float64)
    if values.ndim != 2 or values.shape[0] < 2:
        raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "degeneracy search requires at least two samples")
    try:
        k = int(params.get("k_neighbors", 8))
    except (TypeError, ValueError) as exc:
        raise AppError(ANALYSIS_INPUT_INVALID, "k_neighbors must be an integer") from exc
    k = max(1, min(k, 64))
    try:
        d_quantile = float(params.get("d_quantile", 0.05))
        structure_quantile = float(params.get("structure_quantile", 0.9))
        energy_quantile = float(params.get("energy_quantile", 0.9))
    except (TypeError, ValueError) as exc:
        raise AppError(ANALYSIS_INPUT_INVALID, "quantile thresholds must be numbers") from exc
    if not 0 < d_quantile < 1 or not 0.5 <= structure_quantile < 1 or not 0.5 <= energy_quantile < 1:
        raise AppError(ANALYSIS_INPUT_INVALID, "quantile thresholds are out of range")
    try:
        max_pairs = int(params.get("max_pairs", 50))
    except (TypeError, ValueError) as exc:
        raise AppError(ANALYSIS_INPUT_INVALID, "max_pairs must be an integer") from exc
    max_pairs = max(1, min(max_pairs, 500))

    n = values.shape[0]
    warnings: list[str] = []
    if sample_ids is not None and len(sample_ids) == n:
        ids = [str(value) for value in sample_ids]
    else:
        ids = [f"sample:{index}" for index in range(n)]

    pair_a, pair_b, pair_d = _topk_pairs(values, k)

    structural = np.full(pair_a.size, np.inf)
    delta_energy = np.full(pair_a.size, np.nan) if energy is not None else None
    for index in range(pair_a.size):
        if progress and index % 512 == 0:
            progress(index / max(pair_a.size, 1), "comparing candidate pairs")
        structural[index] = structural_distance(int(pair_a[index]), int(pair_b[index]))
        if energy is not None:
            delta_energy[index] = abs(float(energy[pair_a[index]]) - float(energy[pair_b[index]]))

    d_threshold = _quantile_or_none(pair_d, d_quantile)
    struct_threshold = _quantile_or_none(structural, structure_quantile)
    energy_threshold = _quantile_or_none(delta_energy, energy_quantile) if delta_energy is not None else None
    if d_threshold is None or struct_threshold is None:
        raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "not enough candidate pairs to set degeneracy thresholds")

    close = pair_d <= d_threshold
    far_structural = structural >= struct_threshold
    far_energy = delta_energy >= energy_threshold if energy_threshold is not None else np.zeros(pair_a.size, dtype=bool)
    dangerous = close & (far_structural | far_energy)

    reasons: list[str] = []
    for index in range(pair_a.size):
        if not dangerous[index]:
            reasons.append("")
            continue
        by_structure = bool(far_structural[index])
        by_energy = bool(far_energy[index])
        if by_structure and by_energy:
            reasons.append("structure+energy")
        elif by_structure:
            reasons.append("structure" if np.isfinite(structural[index]) else "atom_count")
        else:
            reasons.append("energy")

    order = np.flatnonzero(dangerous)
    severity = np.where(np.isfinite(structural), structural / np.maximum(pair_d, 1e-12), np.inf)
    order = order[np.argsort(-severity[order], kind="stable")][:max_pairs]

    table = []
    for index in order:
        table.append(
            {
                "sample_a": ids[int(pair_a[index])],
                "sample_b": ids[int(pair_b[index])],
                "descriptor_distance": float(pair_d[index]),
                "structural_distance": None if not np.isfinite(structural[index]) else float(structural[index]),
                "delta_energy": None if delta_energy is None or not np.isfinite(delta_energy[index]) else float(delta_energy[index]),
                "reason": reasons[index],
            }
        )

    preview = {
        "kind": "degeneracy_search",
        "sample_count": int(n),
        "k_neighbors": k,
        "candidate_pairs": int(pair_a.size),
        "thresholds": {
            "descriptor_distance": d_threshold,
            "structural_distance": struct_threshold,
            "energy": energy_threshold,
        },
        "n_dangerous": int(np.count_nonzero(dangerous)),
        "reason_counts": {
            reason: reasons.count(reason)
            for reason in ("structure", "atom_count", "energy", "structure+energy")
            if reasons.count(reason)
        },
        "different_atom_count_pairs": int(np.count_nonzero(np.isinf(structural))),
        "pairs": table,
        "energy_used": energy is not None,
    }
    if pair_a.size and not np.any(np.isfinite(structural) & (structural > 0)):
        warnings.append(
            "every candidate pair has zero structural distance: the structural fingerprint is degenerate on this set"
        )
    # The published arrays must be finite (the artifact store rejects NaN/Inf)
    # and they exist for the scatter view, so they carry the comparable pairs
    # only; the preview table and counts keep the full candidate picture
    # including atom-count-mismatched (structurally infinite) pairs.
    publish = np.isfinite(structural)
    if delta_energy is not None:
        publish &= np.isfinite(delta_energy)
    return {
        "arrays": {
            "pair_a": pair_a[publish],
            "pair_b": pair_b[publish],
            "descriptor_distance": pair_d[publish],
            "structural_distance": structural[publish],
            # No energy signal: a zero placeholder keeps the array finite and
            # aligned; the preview states energy_used=false and the view does
            # not color by it.
            "delta_energy": delta_energy[publish] if delta_energy is not None else np.zeros(int(np.count_nonzero(publish))),
        },
        "preview": preview,
        "warnings": warnings,
    }
