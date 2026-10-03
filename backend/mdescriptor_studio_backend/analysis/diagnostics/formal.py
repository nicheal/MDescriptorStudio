"""Formal correctness diagnostics: invariance under symmetry and precision.

The first layer of descriptor validation (review 2026-10-03, P0): before any
PCA/FPS/coverage statement means anything, D(x) must be invariant under
translation, rotation, same-species permutation and - for the invariant
descriptor family this backend targets - reflection, and its response must not
depend on the float precision of the input coordinates.

Each check rebuilds the structure, recomputes the descriptor through the
service-provided callable and reports the relative deviation

    epsilon = ||D(x') - D(x)|| / max(||D(x)||, ||D(x')||, 1e-12)

per structure (structure granularity) or per frame worst-atom (atom
granularity).  A reflection-only failure with all other checks passing is the
signature of a chiral-sensitive descriptor and is reported as such, not as a
generic invariance failure.
"""

from __future__ import annotations

from typing import Callable, Sequence

import numpy as np

from ...errors import ANALYSIS_INPUT_INVALID, AppError
from .geometry import (
    DescriptorRecompute,
    permute_frame,
    reflection_matrix,
    random_rotation,
    replace_geometry,
    transform_frame,
)

_CHECKS = ("translation", "rotation", "reflection", "permutation", "precision")


def _relative_deviation(baseline_rows: np.ndarray, perturbed_rows: np.ndarray) -> np.ndarray:
    """Row-wise relative deviation between two aligned descriptor matrices."""
    numerator = np.linalg.norm(perturbed_rows - baseline_rows, axis=1)
    denominator = np.maximum(
        np.linalg.norm(baseline_rows, axis=1),
        np.linalg.norm(perturbed_rows, axis=1),
    )
    return numerator / np.maximum(denominator, 1e-12)


def _same_species_permutation(numbers: np.ndarray, rng: np.random.Generator) -> np.ndarray | None:
    """Shuffle atom labels within species groups; None when no group has >= 2 atoms."""
    parts = []
    permutable = False
    for species in np.unique(numbers):
        indices = np.flatnonzero(numbers == species)
        if indices.size < 2:
            parts.append(indices)
            continue
        permutable = True
        parts.append(rng.permutation(indices))
    if not permutable:
        return None
    return np.concatenate(parts).astype(np.int64)


def formal_invariance(
    frames: Sequence,
    baseline: DescriptorRecompute,
    recompute: Callable[[Sequence], DescriptorRecompute],
    params: dict,
    progress: Callable[[float, str], None] | None = None,
) -> dict:
    """Run the formal invariance battery over bounded, preselected frames.

    The service owns frame selection, the descriptor instance and the recompute
    closure; this layer owns the transforms, the epsilon statistics and the
    verdicts.  One recompute per transformed copy keeps the engine cost at
    (n_frames x transforms) small-structure evaluations.
    """
    granularity = str(params.get("granularity") or "structure").lower()
    if granularity not in ("structure", "atom"):
        raise AppError(ANALYSIS_INPUT_INVALID, "granularity must be structure or atom")
    if granularity == "atom" and (baseline.atomic_values is None or baseline.row_offsets is None):
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "atom-granularity invariance requires a descriptor run with per-atom rows and verified row_offsets",
        )
    tolerance = float(params.get("tolerance", 1e-6))
    if not np.isfinite(tolerance) or tolerance <= 0:
        raise AppError(ANALYSIS_INPUT_INVALID, "tolerance must be a positive number")
    try:
        n_rotations = int(params.get("n_rotations", 3))
    except (TypeError, ValueError) as exc:
        raise AppError(ANALYSIS_INPUT_INVALID, "n_rotations must be an integer") from exc
    if not 1 <= n_rotations <= 16:
        raise AppError(ANALYSIS_INPUT_INVALID, "n_rotations must be between 1 and 16")
    requested_checks = params.get("checks") or list(_CHECKS)
    if isinstance(requested_checks, str):
        requested_checks = [requested_checks]
    checks = [str(check).lower() for check in requested_checks]
    unknown = [check for check in checks if check not in _CHECKS]
    if unknown:
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "unknown invariance checks",
            {"unknown": unknown, "supported": list(_CHECKS)},
        )
    try:
        seed = int(params.get("seed", 42))
    except (TypeError, ValueError) as exc:
        raise AppError(ANALYSIS_INPUT_INVALID, "seed must be an integer") from exc
    rng = np.random.default_rng(seed)

    frame_count = len(frames)
    if frame_count < 1:
        raise AppError(ANALYSIS_INPUT_INVALID, "formal invariance requires at least one structure")

    if granularity == "structure":
        baseline_values = np.asarray(baseline.structure_values, dtype=np.float64)
        offsets = None
    else:
        baseline_values = np.asarray(baseline.atomic_values, dtype=np.float64)
        offsets = np.asarray(baseline.row_offsets, dtype=np.int64)

    worst: dict[str, np.ndarray] = {check: np.full(frame_count, np.nan) for check in checks}

    def record(check: str, frame_index: int, deviation: float) -> None:
        current = worst[check][frame_index]
        worst[check][frame_index] = deviation if not np.isfinite(current) else max(current, deviation)

    def rows_of(recomputed: DescriptorRecompute) -> np.ndarray:
        values = (
            recomputed.structure_values
            if granularity == "structure"
            else recomputed.atomic_values
        )
        rows = np.asarray(values, dtype=np.float64)
        if rows.shape != expected_shape:
            raise AppError(
                ANALYSIS_INPUT_INVALID,
                "recomputed descriptor shape does not match the baseline",
                {"baseline": list(expected_shape), "recomputed": list(rows.shape)},
            )
        return rows

    for index, frame in enumerate(frames):
        if progress:
            progress((index + 1) / frame_count, "formal invariance checks")
        if granularity == "structure":
            base_rows = baseline_values[index : index + 1]
            expected_shape = base_rows.shape
        else:
            base_rows = baseline_values[offsets[index] : offsets[index + 1]]
            expected_shape = base_rows.shape

        def compare(check: str, rows: np.ndarray, baseline_alignment: np.ndarray | None = None) -> None:
            """Record the worst deviation of one recomputed copy.

            ``baseline_alignment`` reindexes the baseline rows before comparison:
            for the permutation check new row k is the environment old atom
            permutation[k] had, so baseline row permutation[k] is its reference.
            """
            reference = base_rows if baseline_alignment is None else base_rows[baseline_alignment]
            deviations = _relative_deviation(reference, rows)
            record(check, index, float(np.max(deviations)))

        def evaluate(check: str, transformed, baseline_alignment: np.ndarray | None = None) -> None:
            compare(check, rows_of(recompute([transformed])), baseline_alignment)

        if "translation" in checks:
            direction = rng.normal(size=3)
            norm = np.linalg.norm(direction)
            shift = 0.25 * direction / norm if norm > 1e-12 else np.array([0.25, 0.0, 0.0])
            evaluate("translation", transform_frame(frame, translation=shift))

        if "rotation" in checks:
            for _ in range(n_rotations):
                evaluate("rotation", transform_frame(frame, linear=random_rotation(rng)))

        if "reflection" in checks:
            evaluate("reflection", transform_frame(frame, linear=reflection_matrix(rng)))

        if "permutation" in checks:
            permutation = _same_species_permutation(np.asarray(frame.numbers), rng)
            if permutation is None:
                # Nothing to permute: record a trivial pass so the check's frame
                # coverage stays explicit instead of silently missing frames.
                record("permutation", index, 0.0)
            else:
                # Only atom-granularity rows need reindexing by the permutation;
                # a structure row pools over all atoms and compares directly.
                alignment = permutation if granularity == "atom" else None
                evaluate("permutation", permute_frame(frame, permutation), alignment)

        if "precision" in checks:
            positions32 = np.asarray(frame.positions, dtype=np.float32).astype(np.float64)
            cell32 = np.asarray(frame.cell, dtype=np.float32).astype(np.float64)
            evaluate("precision", replace_geometry(frame, positions=positions32, cell=cell32))

    summary = {}
    for check in checks:
        eps = worst[check]
        measured = eps[np.isfinite(eps)]
        failures = int(np.count_nonzero(measured > tolerance)) if measured.size else 0
        summary[check] = {
            "frames_measured": int(measured.size),
            "worst_epsilon": float(np.max(measured)) if measured.size else None,
            "mean_epsilon": float(np.mean(measured)) if measured.size else None,
            "failures": failures,
            "passed": failures == 0,
        }
    overall = all(item["passed"] for item in summary.values())
    chiral = (
        "reflection" in summary
        and not summary["reflection"]["passed"]
        and all(summary[check]["passed"] for check in summary if check != "reflection")
    )
    warnings: list[str] = []
    if chiral:
        warnings.append(
            "reflection deviates while translation/rotation/permutation pass: the descriptor appears chirality-sensitive"
        )
    return {
        "arrays": {f"epsilon_{check}": worst[check] for check in checks},
        "preview": {
            "kind": "formal_invariance",
            "granularity": granularity,
            "tolerance": tolerance,
            "n_rotations": n_rotations,
            "frame_count": frame_count,
            "checks": summary,
            "passed": overall,
            "chirality_sensitive": chiral,
        },
        "warnings": warnings,
    }
