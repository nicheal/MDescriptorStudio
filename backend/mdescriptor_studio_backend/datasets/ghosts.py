"""Periodic-image ghost atoms for boundary-cut bonds (Explore page rendering)."""

from __future__ import annotations

import numpy as np

from ..lattice import image_shift_limits

DEFAULT_BOND_CUTOFF = 2.4


def periodic_boundary_ghosts(
    symbols: list[str], positions: np.ndarray, cell: np.ndarray,
    cutoff: float = DEFAULT_BOND_CUTOFF, max_ghosts: int = 3000,
    pbc: np.ndarray | None = None,
) -> list[tuple[str, np.ndarray, int]]:
    """Periodic-image atoms that complete bonds cut by the cell boundary.

    An atom whose *wrapped* fractional position lies within `cutoff` of a
    cell face contributes candidate images at the lattice shifts required by
    that cutoff (not only ±1); a candidate is kept only when it lands within
    `cutoff` of a displayed atom (and does not coincide with one), so only
    bond-completing images survive. The distance check is what keeps unwrapped
    frames (deepmd sets are often centered on the origin, with negative
    coordinates) from spraying stray atoms outside the structure. Returns
    (element, position, parent_index) triples — parent_index is the real atom
    the image mirrors — capped at max_ghosts.
    """
    pos = np.asarray(positions, dtype=np.float64)
    if len(symbols) == 0:
        return []
    periodic_axes = np.ones(3, dtype=bool) if pbc is None else np.asarray(pbc, dtype=bool).reshape(3)
    axes = tuple(int(axis) for axis in np.flatnonzero(periodic_axes))
    if not axes:
        return []
    try:
        a_inv = np.linalg.inv(cell)
    except np.linalg.LinAlgError:
        return []  # singular cell: no invertible lattice, so no well-defined images
    frac = pos @ a_inv
    frac_w = frac - np.floor(frac)  # face test needs in-cell fraction
    spacing = 1.0 / np.linalg.norm(a_inv, axis=0)  # interplanar distance per axis
    near_face = (frac_w * spacing < cutoff) | ((1.0 - frac_w) * spacing < cutoff)
    near_face &= periodic_axes[None, :]
    cand = np.nonzero(near_face.any(axis=1))[0]
    if cand.size == 0:
        return []
    # The shared bound keeps local-shell visualization correct when the cutoff
    # spans more than one unit cell, and refuses the degenerate cells whose
    # shift count would otherwise explode into the billions.
    limits = image_shift_limits(cell, cutoff, axes=axes)
    if limits is None:
        return []  # no image stencil for this cell/cutoff: draw no ghosts
    offsets = np.array(
        np.meshgrid(*[np.arange(-limit, limit + 1) for limit in limits], indexing="ij")
    ).reshape(len(axes), -1).T
    shifts = np.zeros((offsets.shape[0], 3), dtype=np.float64)
    shifts[:, axes] = offsets
    shifts = shifts[np.any(shifts != 0.0, axis=1)] @ cell
    pos_sq = (pos * pos).sum(axis=1)
    out: list[tuple[str, np.ndarray, int]] = []
    # One block holds (candidates x shifts) x atoms of squared distances, so the
    # budget has to divide by both multipliers: bounding candidates alone let a
    # 4000-atom frame allocate gigabytes for a single block.
    block_cells = max(1, len(shifts) * len(symbols))
    chunk = max(1, int(4_000_000 // block_cells))
    for start in range(0, cand.size, chunk):
        idx = cand[start:start + chunk]
        imgs = (pos[idx][:, None, :] + shifts[None, :, :]).reshape(-1, 3)
        d2 = (imgs * imgs).sum(axis=1)[:, None] - 2.0 * (imgs @ pos.T) + pos_sq[None, :]
        d2min = np.maximum(d2.min(axis=1), 0.0)
        # bonded to something on screen, and not exactly standing on an atom
        keep = (d2min <= cutoff * cutoff) & (d2min > 1e-6)
        if not keep.any():
            continue
        src = np.repeat(idx, len(shifts))[keep]
        for i, p in zip(src, imgs[keep]):
            # parent index lets the GUI map a click on an image atom back to
            # the real atom it mirrors (real atoms occupy 0..len(symbols)-1).
            out.append((symbols[i], p, int(i)))
            if len(out) >= max_ghosts:
                return out
    return out
