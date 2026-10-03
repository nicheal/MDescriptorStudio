"""Cutoff smoothness diagnostics: D(r) continuity across the neighbor cutoff.

A hard neighbor-list cutoff makes the descriptor a piecewise function of the
cutoff radius.  Ideal crystals routinely place many pairs exactly on a neighbor
shell, so a cutoff set on top of a shell turns every such pair into a
discontinuity - the descriptor "sees" a neighbor appear or vanish for an
infinitesimal change of the cutoff parameter.

This diagnostic rebuilds the descriptor at rcut + delta over a symmetric grid,
computes the per-structure response curve ||D(rcut+delta) - D(rcut)|| relative
to ||D(rcut)||, its finite-difference derivative magnitudes, and a jump ratio
that contrasts the +/-delta1 straddle of rcut against the same straddle deeper
in the grid (where the response is expected to be smooth).
"""

from __future__ import annotations

from typing import Callable, Sequence

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, AppError
from .geometry import DescriptorRecompute


def cutoff_smoothness(
    frames: Sequence,
    recompute_at: Callable[[float], DescriptorRecompute],
    params: dict,
    progress: Callable[[float, str], None] | None = None,
) -> dict:
    """Scan the cutoff parameter and report per-structure response curves.

    ``recompute_at(offset)`` rebuilds the descriptor at ``base_cutoff + offset``
    and recomputes every frame (service-owned).  The reference row is the
    offset = 0 rebuild, not the stored run: a rebuild that disagrees with the
    stored matrix is reported as a warning (the overridden parameter is
    probably not the cutoff the run actually used) instead of silently
    poisoning the curves.  ``params["_stored_baseline"]`` carries that stored
    matrix for the check.
    """
    stored_raw = params.get("_stored_baseline")
    stored = np.asarray(stored_raw, dtype=np.float64) if stored_raw is not None else None

    try:
        max_delta = float(params.get("max_delta", 0.1))
    except (TypeError, ValueError) as exc:
        raise AppError(ANALYSIS_INPUT_INVALID, "max_delta must be a number") from exc
    if not np.isfinite(max_delta) or max_delta <= 0 or max_delta > 5.0:
        raise AppError(ANALYSIS_INPUT_INVALID, "max_delta must be in (0, 5] angstrom")
    try:
        n_steps = int(params.get("n_steps", 9))
    except (TypeError, ValueError) as exc:
        raise AppError(ANALYSIS_INPUT_INVALID, "n_steps must be an integer") from exc
    if not 5 <= n_steps <= 41 or n_steps % 2 == 0:
        raise AppError(ANALYSIS_INPUT_INVALID, "n_steps must be an odd integer between 5 and 41")
    try:
        jump_threshold = float(params.get("jump_threshold", 3.0))
    except (TypeError, ValueError) as exc:
        raise AppError(ANALYSIS_INPUT_INVALID, "jump_threshold must be a number") from exc
    if not np.isfinite(jump_threshold) or jump_threshold <= 1.0:
        raise AppError(ANALYSIS_INPUT_INVALID, "jump_threshold must be > 1")

    frame_count = len(frames)
    if frame_count < 1:
        raise AppError(ANALYSIS_INPUT_INVALID, "cutoff smoothness requires at least one structure")
    deltas = np.linspace(-max_delta, max_delta, n_steps)
    mid = n_steps // 2

    rows: list[np.ndarray] = []
    rebuild_reference: np.ndarray | None = None
    for step, delta in enumerate(deltas):
        recomputed = recompute_at(float(delta))
        values = np.asarray(recomputed.structure_values, dtype=np.float64)
        if values.shape[0] != frame_count:
            raise AppError(
                ANALYSIS_INPUT_INVALID,
                "recomputed descriptor row count does not match the selected frames",
                {"expected": frame_count, "actual": int(values.shape[0])},
            )
        if step == 0:
            rebuild_reference = values
        rows.append(values)
        if progress:
            progress((step + 1) / n_steps, "scanning the cutoff parameter")
    response = np.stack(rows, axis=1)  # (n_frames, n_steps, n_features)
    zero = response[:, mid, :]

    norms = np.linalg.norm(zero, axis=1)
    scale = np.maximum(norms, 1e-12)
    curves = np.linalg.norm(response - zero[:, None, :], axis=2) / scale[:, None]

    # dD/dr and d2D/dr2 magnitudes per structure, over the delta grid.
    first = np.linalg.norm(np.gradient(response, deltas, axis=1), axis=2)
    second = np.linalg.norm(np.gradient(np.gradient(response, deltas, axis=1), deltas, axis=1), axis=2)

    jump_ratio = np.zeros(frame_count, dtype=np.float64)
    for index in range(frame_count):
        # Consecutive-step changes: the two steps adjacent to the cutoff versus
        # the interior steps.  A discontinuity at rcut makes the adjacent steps
        # O(1) while a smooth response varies comparably across the whole grid;
        # comparing against the interior mean keeps the ratio scale-free.
        steps = np.linalg.norm(np.diff(response[index], axis=0), axis=1) / scale[index]
        adjacent = [float(steps[mid - 1]), float(steps[mid])]
        interior = [float(steps[k]) for k in range(steps.size) if k not in (mid - 1, mid)]
        reference = max(float(np.mean(interior)) if interior else 0.0, 1e-15)
        jump_ratio[index] = max(adjacent) / reference

    warnings: list[str] = []
    if stored is not None and rebuild_reference is not None:
        mismatch = np.linalg.norm(stored - rebuild_reference, axis=1) / np.maximum(np.linalg.norm(stored, axis=1), 1e-12)
        worst = float(np.max(mismatch)) if mismatch.size else 0.0
        if worst > 1e-6:
            warnings.append(
                f"rebuilt descriptor at delta = 0 deviates from the stored run by up to {worst:.3e} (relative); "
                "the overridden cutoff parameter may not be the cutoff the run actually used"
            )

    flagged = jump_ratio > jump_threshold
    return {
        "arrays": {
            "deltas": deltas,
            "response_curves": curves,
            "dd_dr": first,
            "d2d_dr2": second,
            "jump_ratio": jump_ratio,
        },
        "preview": {
            "kind": "cutoff_smoothness",
            "frame_count": frame_count,
            "max_delta": max_delta,
            "n_steps": n_steps,
            "jump_threshold": jump_threshold,
            "worst_jump_ratio": float(np.max(jump_ratio)),
            "n_flagged": int(np.count_nonzero(flagged)),
            "frames": [
                {
                    "frame_index": int(index),
                    "sample_id": str(getattr(frames[index], "id", "") or f"frame:{index}"),
                    "jump_ratio": float(jump_ratio[index]),
                    "worst_relative_response": float(np.max(curves[index])),
                    "max_dd_dr": float(np.max(first[index])),
                    "flagged": bool(flagged[index]),
                }
                for index in range(frame_count)
            ],
        },
        "warnings": warnings,
    }
