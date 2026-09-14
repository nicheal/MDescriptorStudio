"""Composite feature blocks for sampling spaces that span several descriptors.

A sampling space is not always one descriptor matrix.  A training set may also
need to be representative in lattice geometry, chemical composition, or energy
range.  :class:`FeatureBlock` packages one such signal, and
:func:`combine_feature_blocks` turns several of them into a single Euclidean
space:

    X_combined = [ w_1·Z_1/√D_1 , w_2·Z_2/√D_2 , … ]

Each block is scaled on its own (raw / standardized / robust) so that one block's
arbitrary unit does not dictate the distance, and divided by √D so that a wide
block cannot outvote a narrow one.  The weight then expresses how much the user
wants that block to matter.

This is deliberately *not* NepTrainKit's physics FPS: no block is implied by a
descriptor or a trained model.  Callers assemble the blocks they mean, and the
sampling layer only ever sees matrices.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .preprocessing import SCALING_MODES, apply_scaling, fit_scaling


@dataclass(frozen=True)
class FeatureBlock:
    """One named signal entering a composite sampling space.

    ``values`` is a ``(n_structures, n_features)`` matrix; ``weight`` scales the
    block's influence after normalisation; ``scaling`` is applied to this block
    alone before it is combined.
    """

    name: str
    values: np.ndarray
    weight: float = 1.0
    scaling: str = "robust"


@dataclass(frozen=True)
class CompositeSpace:
    """A combined matrix plus the layout needed to explain it in a report."""

    values: np.ndarray
    layout: list[dict]
    warnings: list[str]


def combine_feature_blocks(
    blocks: list[FeatureBlock],
    *,
    scaling: str | None = None,
    reference: list[FeatureBlock] | None = None,
) -> tuple[CompositeSpace, CompositeSpace | None]:
    """Combine blocks into one sampling space.

    Returns ``(candidate_space, reference_space_or_None)``.  When ``reference``
    blocks are given (a warm-start training set), each block's scaling is fitted
    on the reference values and applied unchanged to both matrices — the same
    reference-side convention the single-matrix path uses, and the only choice
    that keeps candidate and existing distances comparable.

    ``scaling`` overrides every block's own setting when given; the GUI exposes
    one control for all blocks, so that is the usual path.
    """
    prepared = _prepare_blocks(blocks, scaling)
    reference_by_name = None
    if reference is not None:
        reference_by_name = {str(block.name): block for block in reference}
        if len(reference_by_name) != len(reference):
            raise ValueError("reference feature blocks must have unique names")

    warnings: list[str] = []
    combined: list[np.ndarray] = []
    reference_combined: list[np.ndarray] = []
    layout: list[dict] = []
    offset = 0
    for name, values, weight, mode in prepared:
        dimension = int(values.shape[1])
        if reference_by_name is None:
            fitted, block_warnings = fit_scaling(values, mode)
            reference_values = None
        else:
            reference_block = reference_by_name.get(name)
            if reference_block is None:
                raise ValueError(f"reference set has no feature block named {name!r}")
            reference_values = _validate_block(reference_block.values, name)
            if reference_values.shape[1] != dimension:
                raise ValueError(
                    f"feature block {name!r} has dimension {dimension} for the candidate set"
                    f" but {reference_values.shape[1]} for the existing set"
                )
            # Fit on the existing set so a warm start compares both matrices in
            # one common frame instead of two independently normalized ones.
            fitted, block_warnings = fit_scaling(reference_values, mode)
        warnings.extend(f"{name}: {message}" for message in block_warnings)
        # 1/√D keeps a 256-column descriptor block and a 6-column lattice block
        # on the same footing; the weight is the only deliberate override.
        coefficient = weight / float(np.sqrt(dimension))
        combined.append(apply_scaling(fitted, values) * coefficient)
        if reference_values is not None:
            reference_combined.append(apply_scaling(fitted, reference_values) * coefficient)
        layout.append({"name": name, "dimension": dimension, "weight": float(weight), "scaling": mode, "offset": offset})
        offset += dimension

    space = CompositeSpace(np.hstack(combined), layout, warnings)
    reference_space = CompositeSpace(np.hstack(reference_combined), layout, []) if reference_combined else None
    return space, reference_space


def _prepare_blocks(blocks: list[FeatureBlock], scaling: str | None) -> list[tuple[str, np.ndarray, float, str]]:
    if not blocks:
        raise ValueError("composite sampling requires at least one feature block")
    prepared: list[tuple[str, np.ndarray, float, str]] = []
    row_count: int | None = None
    for block in blocks:
        name = str(block.name or "").strip()
        if not name:
            raise ValueError("feature blocks must have a non-empty name")
        values = _validate_block(block.values, name)
        if row_count is None:
            row_count = values.shape[0]
        elif values.shape[0] != row_count:
            raise ValueError(f"feature block {name!r} has {values.shape[0]} rows but other blocks have {row_count}")
        try:
            weight = float(block.weight)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"feature block {name!r} has a non-numeric weight") from exc
        if not np.isfinite(weight) or weight <= 0:
            raise ValueError(f"feature block {name!r} weight must be a positive finite number")
        mode = str(scaling or block.scaling or "robust")
        if mode not in SCALING_MODES:
            raise ValueError(f"feature block {name!r} scaling must be one of {', '.join(SCALING_MODES)}")
        prepared.append((name, values, weight, mode))
    return prepared


def _validate_block(values: np.ndarray, name: str) -> np.ndarray:
    block = np.asarray(values, dtype=np.float64)
    if block.ndim == 1:
        block = block.reshape(-1, 1)
    if block.ndim != 2 or block.shape[0] == 0 or block.shape[1] == 0:
        raise ValueError(f"feature block {name!r} must be a non-empty 2D matrix")
    if not np.isfinite(block).all():
        bad = int((~np.isfinite(block)).sum())
        raise ValueError(f"feature block {name!r} contains {bad} NaN or Inf value(s)")
    return block
