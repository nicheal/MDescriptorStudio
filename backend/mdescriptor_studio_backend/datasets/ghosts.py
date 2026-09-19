"""Periodic-image ghost atoms for boundary-cut bonds (Explore page rendering)."""

from __future__ import annotations

import numpy as np

DEFAULT_BOND_CUTOFF = 2.4
# How many images along one axis a bond cutoff can genuinely require. Beyond
# this the lattice vector is shorter than a fraction of the cutoff, which is a
# numerically degenerate direction rather than a periodic one worth rendering.
MAX_IMAGES_PER_AXIS = 4


def periodic_boundary_ghosts(
    symbols: list[str], positions: np.ndarray, cell: np.ndarray,
    cutoff: float = DEFAULT_BOND_CUTOFF, max_ghosts: int = 3000,
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
    try:
        a_inv = np.linalg.inv(cell)
    except np.linalg.LinAlgError:
        return []  # singular cell: no invertible lattice, so no well-defined images
    frac = pos @ a_inv
    frac_w = frac - np.floor(frac)  # face test needs in-cell fraction
    spacing = 1.0 / np.linalg.norm(a_inv, axis=0)  # interplanar distance per axis
    near_face = (frac_w * spacing < cutoff) | ((1.0 - frac_w) * spacing < cutoff)
    cand = np.nonzero(near_face.any(axis=1))[0]
    if cand.size == 0:
        return []
    # The inverse-cell columns bound the lattice coefficients of any
    # displacement with norm <= cutoff.  The extra one accounts for the
    # wrapped fractional separation between two atoms. This keeps local-shell
    # visualization correct when the cutoff spans more than one unit cell.
    limits: list[int] = []
    for axis in range(3):
        limit = max(1, int(np.ceil(cutoff * np.linalg.norm(a_inv[:, axis]))) + 1)
        if limit > MAX_IMAGES_PER_AXIS:
            # A cell vector this short relative to the cutoff makes the shift
            # count explode into the billions, freezing the request thread for
            # a picture no real system produces. Draw no images instead.
            return []
        limits.append(limit)
    shifts = np.array(
        [
            (dx, dy, dz)
            for dx in range(-limits[0], limits[0] + 1)
            for dy in range(-limits[1], limits[1] + 1)
            for dz in range(-limits[2], limits[2] + 1)
            if (dx, dy, dz) != (0, 0, 0)
        ],
        dtype=np.float64,
    ) @ cell
    pos_sq = (pos * pos).sum(axis=1)
    out: list[tuple[str, np.ndarray, int]] = []
    chunk = max(1, int(4_000_000 // max(len(symbols), 1)))
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
