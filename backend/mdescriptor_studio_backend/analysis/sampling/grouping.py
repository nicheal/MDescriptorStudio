"""Group labels and the √N quota allocation for grouped sampling.

Grouped FPS keeps multi-element datasets from developing a composition bias:
structures are grouped by their element set ("C", "C-O", "C-O-Si", …) and each
group is sampled independently.  The budget is allocated by

    w_g = √N_g ,  n_g ∝ w_g  (largest-remainder rounding)

with at least one sample per group whenever the target can cover every group.
√ weights keep small composition classes from being drowned by the largest
one, which a linear N_g share would do.
"""

from __future__ import annotations

import numpy as np


def validate_group_labels(groups: np.ndarray, n_samples: int) -> np.ndarray:
    """Normalize group labels to a 1-D object array aligned with the samples."""
    labels = np.asarray(groups, dtype=object).reshape(-1)
    if labels.size != n_samples:
        raise ValueError(f"group labels cover {labels.size} sample(s) but the feature matrix has {n_samples}")
    for label in labels:
        if not isinstance(label, str) or not label:
            raise ValueError("group labels must be non-empty strings")
    return labels


def group_sizes(labels: np.ndarray) -> tuple[list[str], np.ndarray]:
    """Deterministic (label-sorted) group names and member counts."""
    names, counts = np.unique(labels, return_counts=True)
    return [str(name) for name in names.tolist()], counts.astype(np.int64)


def sqrt_quota(sizes: np.ndarray, total: int) -> np.ndarray:
    """Allocate ``total`` samples across groups by a √N_g share.

    Returns one integer count per group; the counts sum to exactly
    ``min(total, sizes.sum())``, never exceed a group's size, and give every
    non-empty group at least one sample whenever ``total`` covers all groups.
    """
    sizes = np.asarray(sizes, dtype=np.int64).reshape(-1)
    if total < 1:
        raise ValueError("total must be >= 1")
    if bool((sizes < 0).any()):
        raise ValueError("group sizes must be non-negative")
    capacity = int(sizes.sum())
    total = min(int(total), capacity)
    if total == 0:
        return np.zeros(sizes.size, dtype=np.int64)

    nonempty = sizes > 0
    weights = np.sqrt(sizes.astype(np.float64))
    ideal = total * weights / weights.sum()
    counts = np.floor(ideal).astype(np.int64)
    minimum = 1 if total >= int(nonempty.sum()) else 0
    counts[nonempty] = np.maximum(counts[nonempty], minimum)
    counts = np.minimum(counts, sizes)
    # Largest-remainder fill/trim until the budget is exact.  Fill prefers the
    # group whose ideal share is least satisfied; trim respects the minimum.
    while int(counts.sum()) < total:
        eligible = np.flatnonzero(nonempty & (counts < sizes))
        if eligible.size == 0:
            break
        residual = ideal[eligible] - counts[eligible]
        counts[int(eligible[int(np.argmax(residual))])] += 1
    while int(counts.sum()) > total:
        eligible = np.flatnonzero(nonempty & (counts > minimum))
        if eligible.size == 0:
            break
        residual = ideal[eligible] - counts[eligible]
        counts[int(eligible[int(np.argmin(residual))])] -= 1
    return counts
