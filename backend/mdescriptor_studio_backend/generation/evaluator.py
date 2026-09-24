"""Align raw engine output with the structures that produced it.

Lifted from ``services/job_runner.py::_computed_structure_values`` so every
caller — the perturbation-sensitivity runner today, generation optimizers
from G1 — shares one alignment implementation instead of re-deriving the
row_offsets contract.

Two consumers need different granularities from the same compute result:

* structure-level objectives (novelty vs. an archive of structure
  descriptors) consume ``structure_values`` — atom/pair rows mean-pooled
  per structure, exactly what the sensitivity runner always did;
* local-environment objectives (G2) need the per-atom rows themselves, so
  the pooling applied for ``structure_values`` must never be the only copy.
  ``atomic_values`` + ``row_offsets`` are kept alongside, un-averaged.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..errors import ANALYSIS_INPUT_INVALID, AppError


@dataclass(frozen=True)
class DescriptorEvaluation:
    """One engine batch, aligned both ways.

    ``structure_values`` always has exactly ``frame_count`` rows.
    ``atomic_values``/``row_offsets`` are present only when the engine
    reported per-atom rows (one structure may own zero rows).
    """

    structure_values: np.ndarray  # (n_structures, n_features)
    atomic_values: np.ndarray | None  # (n_atoms_total, n_features)
    row_offsets: np.ndarray | None  # (n_structures + 1,)


def _as_finite_2d(values, what: str) -> np.ndarray:
    matrix = np.asarray(values, dtype=np.float64)
    if matrix.ndim > 2:
        matrix = matrix.reshape(matrix.shape[0], -1)
    if matrix.ndim == 1:
        matrix = matrix.reshape(-1, 1)
    if matrix.ndim != 2 or not np.isfinite(matrix).all():
        raise AppError(ANALYSIS_INPUT_INVALID, f"{what} is not a finite 2D matrix")
    return matrix


def _valid_offsets(offsets, n_rows: int) -> bool:
    try:
        return bool(
            offsets is not None
            and offsets.ndim == 1
            and offsets.size >= 2
            and int(offsets[0]) == 0
            and int(offsets[-1]) == n_rows
            and np.all(np.diff(offsets) >= 0)
        )
    except (TypeError, ValueError):
        return False


def evaluate_batch(computed, frame_count: int, *, what: str = "descriptor result") -> DescriptorEvaluation:
    """Align one engine compute result with ``frame_count`` structures.

    Row semantics (mirrors the engine contract the sensitivity runner pinned):
    valid ``row_offsets`` of length ``frame_count + 1`` mean per-atom rows
    pooled into per-structure values; otherwise the result must already carry
    one row per structure. Anything else is a misaligned input, not a number
    to average over.
    """
    values = _as_finite_2d(getattr(computed, "values", computed), what)
    raw_offsets = getattr(computed, "row_offsets", None)
    offsets = np.asarray(raw_offsets, dtype=np.int64) if raw_offsets is not None else None
    if _valid_offsets(offsets, values.shape[0]) and offsets.size == frame_count + 1:
        pooled = np.empty((frame_count, values.shape[1]), dtype=np.float64)
        for index in range(frame_count):
            lo, hi = int(offsets[index]), int(offsets[index + 1])
            pooled[index] = values[lo:hi].mean(axis=0) if hi > lo else 0.0
        return DescriptorEvaluation(structure_values=pooled, atomic_values=values, row_offsets=offsets)
    if values.shape[0] == frame_count:
        return DescriptorEvaluation(structure_values=values, atomic_values=None, row_offsets=None)
    raise AppError(
        ANALYSIS_INPUT_INVALID,
        f"{what} cannot be aligned to structures",
        {"rows": int(values.shape[0]), "structures": frame_count},
    )


def structure_values(computed, frame_count: int) -> np.ndarray:
    """Per-structure matrix only — the perturbation-sensitivity contract."""
    return evaluate_batch(computed, frame_count, what="perturbed descriptor result").structure_values


class DescriptorEvaluator:
    """Owns one built descriptor and batch-evaluates candidates through it.

    Built once per run (the adapter reuses the engine's warmup gate), and
    every round's candidates go through ONE ``compute`` call — descriptor
    evaluation dominates the cost of a run, so the batch shape is a
    performance contract, not a convenience.
    """

    def __init__(
        self,
        adapter,
        descriptor_name: str,
        descriptor_parameters: dict,
        device: str = "cpu",
        num_threads: int | None = None,
    ) -> None:
        self._adapter = adapter
        self._descriptor = adapter.build(
            descriptor_name,
            descriptor_parameters,
            device=device,
            num_threads=num_threads,
        )

    def evaluate(self, candidates, *, return_atomic: bool = False, control=None) -> DescriptorEvaluation:
        if not candidates:
            raise ValueError("no candidates to evaluate")
        batch = self._adapter.to_structure_batch([c.to_frame() for c in candidates])
        computed = self._adapter.compute(self._descriptor, batch, control)
        aligned = evaluate_batch(computed, len(candidates), what="generation descriptor result")
        if return_atomic:
            return aligned
        return DescriptorEvaluation(
            structure_values=aligned.structure_values,
            atomic_values=None,
            row_offsets=None,
        )
