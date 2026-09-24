"""Local-environment novelty: per-atom distances, robustly aggregated.

For candidate X with atom-environment descriptors z_1..z_N and archive A:

    d_i = min_{a in A} ||z_i - a||

The raw per-atom distances are aggregated per structure. ``max`` is offered
only as a diagnostic — one pathological atom would otherwise dominate the
whole structure's score. The optimisation default is the mean of the top
``top_fraction`` distances: structures that bring *many* new environments
win over structures with one freak atom.

Aggregation choices:
    mean              (1/N) Σ d_i
    top_fraction_mean mean of the largest ceil(top_fraction·N) distances
    quantile          the q-quantile of {d_i}
    max               max_i d_i  (diagnostic only)
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np

from ..archive import LocalEnvironmentArchive
from .base import ObjectiveBatchResult

_AGGREGATIONS = ("mean", "top_fraction_mean", "quantile", "max")


def _aggregate(distances: np.ndarray, aggregation: str, top_fraction: float, quantile: float) -> float:
    if distances.size == 0:
        return 0.0
    if aggregation == "mean":
        return float(distances.mean())
    if aggregation == "max":
        return float(distances.max())
    if aggregation == "quantile":
        return float(np.quantile(distances, quantile))
    # top_fraction_mean
    k = max(1, int(np.ceil(top_fraction * distances.size)))
    return float(np.partition(distances, distances.size - k)[-k:].mean())


class LocalEnvironmentNoveltyObjective:
    name = "local_environment_novelty"
    needs_atomic = True

    def __init__(
        self,
        aggregation: str = "top_fraction_mean",
        top_fraction: float = 0.2,
        quantile: float = 0.5,
        novelty_threshold: float | None = None,
    ) -> None:
        if aggregation not in _AGGREGATIONS:
            raise ValueError(f"aggregation must be one of {', '.join(_AGGREGATIONS)}")
        if not 0.0 < top_fraction <= 1.0:
            raise ValueError("top_fraction must be in (0, 1]")
        if not 0.0 < quantile <= 1.0:
            raise ValueError("quantile must be in (0, 1]")
        self.aggregation = aggregation
        self.top_fraction = float(top_fraction)
        self.quantile = float(quantile)
        self.novelty_threshold = novelty_threshold

    def evaluate_batch(self, structure_values, atomic_values, row_offsets, structure_archive, local_archive: LocalEnvironmentArchive, penalties):
        if atomic_values is None or row_offsets is None:
            raise ValueError("local_environment_novelty requires atom-level descriptor rows")
        atomic = np.asarray(atomic_values, dtype=np.float64)
        offsets = np.asarray(row_offsets, dtype=np.int64)
        per_atom = local_archive.nearest_per_row(atomic)
        n_candidates = offsets.size - 1
        local = np.empty(n_candidates, dtype=np.float64)
        novel_counts = np.empty(n_candidates, dtype=np.float64) if self.novelty_threshold is not None else None
        counts = np.empty(n_candidates, dtype=np.float64)

        def _score_candidate(i: int) -> tuple[float, int, float | None]:
            lo, hi = int(offsets[i]), int(offsets[i + 1])
            rows = per_atom[lo:hi]
            count = float((rows > self.novelty_threshold).sum()) if self.novelty_threshold is not None else None
            return _aggregate(rows, self.aggregation, self.top_fraction, self.quantile), rows.size, count

        workers = min(max(1, int(getattr(local_archive, "workers", 1))), n_candidates)
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                candidate_scores = pool.map(_score_candidate, range(n_candidates))
                for i, (score, count, novel_count) in enumerate(candidate_scores):
                    local[i] = score
                    counts[i] = float(count)
                    if novel_counts is not None:
                        novel_counts[i] = float(novel_count)
        else:
            for i in range(n_candidates):
                score, count, novel_count = _score_candidate(i)
                local[i] = score
                counts[i] = float(count)
                if novel_counts is not None:
                    novel_counts[i] = float(novel_count)
        fitness = local - np.asarray(penalties, dtype=np.float64)
        components = {
            "local_diversity": local,
            "atom_count": counts,
        }
        if novel_counts is not None:
            components["novel_fraction"] = novel_counts / np.maximum(counts, 1.0)
        return ObjectiveBatchResult(
            novelty=None,
            local_diversity=local,
            novel_environment_count=novel_counts,
            fitness=fitness,
            components=components,
        )
