"""Distance consistency: does descriptor distance track structural/physical distance?

The global counterpart of the degeneracy search (review 2026-10-03 §12): over a
bounded sample of pairs, compare d_D against the structural fingerprint
distance and - when per-sample energies exist - against |delta E|.  The output
is the calibration picture (correlations, a binned mean structural distance per
descriptor-distance bin) plus the collapse metrics: which fraction of pairs sit
in the dangerous corner "d_D in the bottom quantile, d_struct in the top
quantile", and how that compares to the independence baseline.

Both distances enter on their own metric: descriptor distances are raw (the
diagnostic is about the descriptor's own geometry), structural distances come
from the service-built fingerprint matrix.
"""

from __future__ import annotations

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, ANALYSIS_INSUFFICIENT_SAMPLES, AppError


def _pearson(a: np.ndarray, b: np.ndarray) -> float | None:
    if a.size < 2:
        return None
    std_a = np.std(a)
    std_b = np.std(b)
    if std_a <= 0 or std_b <= 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    if a.size < 2:
        return None
    return _pearson(np.argsort(np.argsort(a)).astype(np.float64), np.argsort(np.argsort(b)).astype(np.float64))


def _pairwise_descriptor_distances(values: np.ndarray) -> np.ndarray:
    """Full euclidean distance matrix of the (bounded) descriptor subset."""
    squared = np.sum(values * values, axis=1)
    d2 = squared[:, None] + squared[None, :] - 2.0 * (values @ values.T)
    np.maximum(d2, 0.0, out=d2)
    np.fill_diagonal(d2, 0.0)
    return np.sqrt(d2)


def _rank_mask_upper(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Upper-triangle indices and their distance values."""
    n = matrix.shape[0]
    rows, cols = np.triu_indices(n, k=1)
    return rows, cols, matrix[rows, cols]


def distance_consistency(
    values: np.ndarray,
    structural_matrix: np.ndarray,
    params: dict,
    sample_ids: list[str] | None = None,
    energy: np.ndarray | None = None,
    progress=None,
) -> dict:
    """Correlate descriptor distances with structural (and energetic) distances.

    ``structural_matrix`` is the full pairwise structural fingerprint distance
    of the (already subsampled) samples; ``inf`` entries mark different atom
    counts and are excluded from the correlation but reported as a count.
    """
    values = np.asarray(values, dtype=np.float64)
    structural_matrix = np.asarray(structural_matrix, dtype=np.float64)
    if values.ndim != 2 or values.shape[0] < 3:
        raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "distance consistency requires at least three samples")
    if structural_matrix.shape != (values.shape[0], values.shape[0]):
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "structural distance matrix shape does not match the sample count",
            {"samples": int(values.shape[0]), "matrix": list(structural_matrix.shape)},
        )
    try:
        n_bins = int(params.get("n_bins", 12))
    except (TypeError, ValueError) as exc:
        raise AppError(ANALYSIS_INPUT_INVALID, "n_bins must be an integer") from exc
    if not 4 <= n_bins <= 32:
        raise AppError(ANALYSIS_INPUT_INVALID, "n_bins must be between 4 and 32")
    try:
        low_q = float(params.get("d_quantile", 0.05))
        high_q = float(params.get("structure_quantile", 0.95))
    except (TypeError, ValueError) as exc:
        raise AppError(ANALYSIS_INPUT_INVALID, "quantiles must be numbers") from exc
    if not 0 < low_q < 0.5 or not 0.5 < high_q < 1:
        raise AppError(ANALYSIS_INPUT_INVALID, "quantiles are out of range")

    descriptor_distances = _pairwise_descriptor_distances(values)
    rows, cols, d_descriptor = _rank_mask_upper(descriptor_distances)
    d_structural = structural_matrix[rows, cols]

    finite = np.isfinite(d_structural)
    if not np.any(finite):
        raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "no comparable structural pairs (all atom counts differ)")
    pearson = _pearson(d_descriptor[finite], d_structural[finite])
    spearman = _spearman(d_descriptor[finite], d_structural[finite])

    bin_edges = np.quantile(d_descriptor[finite], np.linspace(0.0, 1.0, n_bins + 1))
    bin_edges[0] -= 1e-12
    bin_edges[-1] += 1e-12
    bin_index = np.digitize(d_descriptor, bin_edges[1:-1], right=False)
    binned = []
    for bin_ordinal in range(n_bins):
        in_bin = (bin_index == bin_ordinal) & finite
        if not np.any(in_bin):
            binned.append({"bin": bin_ordinal, "d_descriptor_max": None, "count": 0})
            continue
        binned.append(
            {
                "bin": bin_ordinal,
                "d_descriptor_max": float(np.max(d_descriptor[in_bin])),
                "count": int(np.count_nonzero(in_bin)),
                "structural_mean": float(np.mean(d_structural[in_bin])),
                "structural_median": float(np.median(d_structural[in_bin])),
                "structural_p90": float(np.quantile(d_structural[in_bin], 0.90)),
            }
        )

    d_low = np.quantile(d_descriptor[finite], low_q)
    s_high = np.quantile(d_structural[finite], high_q)
    collapse = (d_descriptor <= d_low) & finite & (d_structural >= s_high)
    collapse_fraction = float(np.mean(collapse))
    independent_baseline = low_q * (1.0 - high_q)
    enrichment = collapse_fraction / independent_baseline if independent_baseline > 0 else None

    energy_rows = None
    energy_stats = None
    if energy is not None:
        energy = np.asarray(energy, dtype=np.float64)
        delta_energy = np.abs(energy[rows] - energy[cols])
        energy_rows = delta_energy
        both = finite & np.isfinite(delta_energy)
        energy_stats = {
            "pearson": _pearson(d_descriptor[both], delta_energy[both]),
            "spearman": _spearman(d_descriptor[both], delta_energy[both]),
            "pairs": int(np.count_nonzero(both)),
        }

    # Dangerous-pair table: low descriptor distance, high structural distance.
    danger_order = np.flatnonzero(finite & (d_descriptor <= d_low))
    danger_order = danger_order[np.argsort(-d_structural[danger_order], kind="stable")][:25]
    ids = sample_ids if sample_ids is not None and len(sample_ids) == values.shape[0] else [f"sample:{i}" for i in range(values.shape[0])]
    pairs = [
        {
            "sample_a": ids[int(rows[index])],
            "sample_b": ids[int(cols[index])],
            "descriptor_distance": float(d_descriptor[index]),
            "structural_distance": float(d_structural[index]),
            "delta_energy": None if energy_rows is None else float(energy_rows[index]),
        }
        for index in danger_order
    ]

    warnings: list[str] = []
    n_infinite = int(np.count_nonzero(~finite))
    if n_infinite:
        warnings.append(f"{n_infinite} pair(s) excluded from correlations: different atom counts")
    return {
        "arrays": {
            "d_descriptor": d_descriptor,
            "d_structural": d_structural,
            "delta_energy": energy_rows if energy_rows is not None else np.full(d_descriptor.shape, np.nan),
        },
        "preview": {
            "kind": "distance_consistency",
            "sample_count": int(values.shape[0]),
            "pair_count": int(d_descriptor.size),
            "comparable_pairs": int(np.count_nonzero(finite)),
            "pearson_descriptor_structural": pearson,
            "spearman_descriptor_structural": spearman,
            "binned": binned,
            "collapse": {
                "d_quantile": low_q,
                "structure_quantile": high_q,
                "d_threshold": float(d_low),
                "structural_threshold": float(s_high),
                "fraction": collapse_fraction,
                "independence_baseline": independent_baseline,
                "enrichment": enrichment,
                "count": int(np.count_nonzero(collapse)),
            },
            "energy": energy_stats,
            "dangerous_pairs": pairs,
        },
        "warnings": warnings,
    }
