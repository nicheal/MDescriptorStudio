"""Lattice geometry shared by the readers, the viewer and the analysis math.

One place for the periodic-image stencil so a caller cannot re-derive the
bound with a subtly different (and wrong) formula: the lattice coefficients
of any Cartesian displacement ``d`` obey ``|s_k| = |d . inv[:, k]| <=
||d|| * ||inv[:, k]||``, i.e. the bound comes from the *columns* of the
inverse cell.  The extra image absorbs the wrapped fractional separation
between two atoms.
"""

from __future__ import annotations

import numpy as np

# How many images along one axis a cutoff can genuinely require. Beyond this
# the "lattice vector" is a fraction of the cutoff: a numerically degenerate
# direction rather than a periodic one. The exact bound reaches ~2.4e9 for such
# a cell (a 1e-9 Å vector still has an invertible cell matrix), which has
# already frozen a worker thread while it enumerated shifts.
MAX_IMAGES_PER_AXIS = 4


def image_shift_limits(
    cell,
    cutoff: float,
    axes: tuple[int, ...] = (0, 1, 2),
    max_per_axis: int = MAX_IMAGES_PER_AXIS,
    max_total_images: int | None = None,
) -> list[int] | None:
    """Per-axis lattice image counts for a Cartesian *cutoff*.

    Returns ``None`` when the cell is singular, or when the stencil it implies
    is past the caller's bounds — the signal to stop rather than enumerate a
    meaningless (or astronomically large) set of shifts.

    ``max_per_axis`` alone is the right rule for drawing bond-completing ghosts
    (a short edge is not a periodic direction worth rendering). Enumeration that
    feeds a neighbour search needs ``max_total_images`` as well, because a
    perfectly ordinary cell can be skewed enough that a conservative per-axis
    bound costs several images on every axis at once.
    """
    matrix = np.asarray(cell, dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or not np.isfinite(cutoff) or cutoff <= 0.0:
        return None
    try:
        inverse = np.linalg.inv(matrix)
    except np.linalg.LinAlgError:
        return None
    limits: list[int] = []
    total = 1
    for axis in axes:
        limit = max(1, int(np.ceil(float(cutoff) * np.linalg.norm(inverse[:, axis]))) + 1)
        if limit > max_per_axis:
            return None
        total *= 2 * limit + 1
        limits.append(limit)
    if max_total_images is not None and total > max_total_images:
        return None
    return limits
