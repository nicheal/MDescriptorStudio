"""Typed containers passed between dataset loading and analysis algorithms.

The concrete structure/atom classes carry granularity in the type instead of a
string branch scattered through every algorithm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..errors import ANALYSIS_INPUT_INVALID, AppError


def _matrix(values: Any) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 1:
        array = array.reshape(-1, 1)
    if array.ndim != 2 or 0 in array.shape:
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "analysis input must be a non-empty 2D matrix",
            {"shape": list(array.shape)},
        )
    return array


def _aligned_array(value: Any, name: str, count: int, *, dtype=None) -> np.ndarray:
    array = np.asarray(value, dtype=dtype) if dtype is not None else np.asarray(value)
    if array.ndim == 0 or array.shape[0] != count:
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            f"{name} length does not match descriptor samples",
            {
                "field": name,
                "expected": count,
                "actual": int(array.shape[0]) if array.ndim else 0,
            },
        )
    return array


def validate_matrix_consistency(
    values: Any,
    frame: Any,
    *,
    row: Any | None = None,
    sample_ids: list[str] | tuple[str, ...] | None = None,
    elements: Any | None = None,
    properties: dict[str, Any] | None = None,
    positions: Any | None = None,
    cells: Any | None = None,
    pbc: Any | None = None,
) -> tuple[
    np.ndarray,
    np.ndarray,
    list[str],
    np.ndarray | None,
    np.ndarray | None,
    dict[str, np.ndarray],
    np.ndarray | None,
    np.ndarray | None,
    np.ndarray | None,
]:
    """Normalize and validate all arrays that share the matrix row axis.

    This is deliberately a single trust-boundary check. Numerical finiteness
    stays with each algorithm because feature-variance intentionally accepts
    non-finite values as diagnostic input.
    """
    matrix = _matrix(values)
    frames = _aligned_array(frame, "frame", matrix.shape[0], dtype=np.int64).reshape(-1)
    ids = list(sample_ids or [])
    if not ids:
        ids = [f"frame:{int(value)}" for value in frames.tolist()]
    if len(ids) != matrix.shape[0]:
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "sample_ids length does not match descriptor samples",
            {"field": "sample_ids", "expected": matrix.shape[0], "actual": len(ids)},
        )
    rows = _aligned_array(row, "row", matrix.shape[0], dtype=np.int64).reshape(-1) if row is not None else None
    atom_elements = _aligned_array(elements, "elements", matrix.shape[0], dtype=np.int64).reshape(-1) if elements is not None else None

    normalized_properties: dict[str, np.ndarray] = {}
    for name, value in (properties or {}).items():
        normalized_properties[str(name)] = _aligned_array(value, f"properties[{name}]", matrix.shape[0])

    normalized_positions = None
    if positions is not None:
        normalized_positions = _aligned_array(positions, "positions", matrix.shape[0], dtype=np.float64)
        if normalized_positions.shape != (matrix.shape[0], 3):
            raise AppError(ANALYSIS_INPUT_INVALID, "positions must have shape (n_samples, 3)")
    normalized_cells = None
    if cells is not None:
        normalized_cells = _aligned_array(cells, "cells", matrix.shape[0], dtype=np.float64)
        if normalized_cells.shape != (matrix.shape[0], 3, 3):
            raise AppError(ANALYSIS_INPUT_INVALID, "cells must have shape (n_samples, 3, 3)")
    normalized_pbc = None
    if pbc is not None:
        normalized_pbc = _aligned_array(pbc, "pbc", matrix.shape[0], dtype=bool)
        if normalized_pbc.shape != (matrix.shape[0], 3):
            raise AppError(ANALYSIS_INPUT_INVALID, "pbc must have shape (n_samples, 3)")
    return (
        matrix,
        frames,
        ids,
        rows,
        atom_elements,
        normalized_properties,
        normalized_positions,
        normalized_cells,
        normalized_pbc,
    )


@dataclass
class DescriptorMatrix:
    """Common aligned storage without a runtime granularity flag."""

    values: np.ndarray
    frame: np.ndarray
    row: np.ndarray | None = None
    sample_ids: list[str] = field(default_factory=list)
    elements: np.ndarray | None = None
    warnings: list[str] = field(default_factory=list)
    properties: dict[str, np.ndarray] = field(default_factory=dict)
    positions: np.ndarray | None = None
    cells: np.ndarray | None = None
    pbc: np.ndarray | None = None

    def __post_init__(self) -> None:
        (
            self.values,
            self.frame,
            self.sample_ids,
            self.row,
            self.elements,
            self.properties,
            self.positions,
            self.cells,
            self.pbc,
        ) = validate_matrix_consistency(
            self.values,
            self.frame,
            row=self.row,
            sample_ids=self.sample_ids,
            elements=self.elements,
            properties=self.properties,
            positions=self.positions,
            cells=self.cells,
            pbc=self.pbc,
        )
        if self.elements is not None:
            self.elements = np.asarray(self.elements, dtype=np.int64).reshape(-1)

    @property
    def n_samples(self) -> int:
        return int(self.values.shape[0])

    @property
    def n_features(self) -> int:
        return int(self.values.shape[1])

    def subset(self, indices: Any) -> "DescriptorMatrix":
        selected = np.asarray(indices, dtype=np.int64).reshape(-1)
        kwargs = {
            "values": self.values[selected],
            "frame": self.frame[selected],
            "row": self.row[selected] if self.row is not None else None,
            "sample_ids": [self.sample_ids[int(index)] for index in selected.tolist()],
            "elements": self.elements[selected] if self.elements is not None else None,
            "warnings": list(self.warnings),
            "properties": {key: value[selected] for key, value in self.properties.items()},
            "positions": self.positions[selected] if self.positions is not None else None,
            "cells": self.cells[selected] if self.cells is not None else None,
            "pbc": self.pbc[selected] if self.pbc is not None else None,
        }
        return type(self)(**kwargs)


@dataclass
class StructureDescriptorMatrix(DescriptorMatrix):
    """One descriptor row per structure/frame."""


@dataclass
class AtomDescriptorMatrix(DescriptorMatrix):
    """One descriptor row per atom/local environment."""


__all__ = [
    "AtomDescriptorMatrix",
    "DescriptorMatrix",
    "StructureDescriptorMatrix",
    "validate_matrix_consistency",
]
