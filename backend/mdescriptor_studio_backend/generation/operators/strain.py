"""Homogeneous isotropic strain: one affine scale on cell and positions."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ..models import StructureCandidate


def strained(frame, scale: float):
    """Return ``frame`` scaled homogeneously by ``scale`` about the cell origin.

    A homogeneous strain is a single affine map, so the same scale has to act
    on the cell *and* on the positions about the same origin — that is what
    keeps every atom's fractional coordinate (and therefore its relation to
    the periodic box, which the descriptor sees) unchanged. Scaling the
    positions about their centroid instead moved the cluster inside a box
    that grew elsewhere: an unintended rigid translation whose size depended
    on where the box origin happened to sit.
    """
    scale = float(scale)
    positions = np.asarray(frame.positions, dtype=np.float64) * scale
    cell = np.asarray(frame.cell, dtype=np.float64)
    if cell.shape == (3, 3) and abs(float(np.linalg.det(cell))) > 1e-10:
        cell = cell * scale
    return replace(frame, positions=positions, cell=cell)


def _affine_strain(candidate: StructureCandidate, deformation: np.ndarray, operator: str, params: dict) -> StructureCandidate:
    """Right-multiply positions and cell by the same deformation matrix.

    With row-vector Cartesian coordinates, ``A = inv(cell) @ cell'`` maps
    fractional coordinates to themselves exactly when positions take the
    same map, so one affine matrix keeps every atom's fractional placement
    (the strain semantics the perturbation-sensitivity runner pinned).
    """
    positions = np.asarray(candidate.positions, dtype=np.float64)
    cell = np.asarray(candidate.cell, dtype=np.float64)
    periodic = bool(np.asarray(candidate.pbc).any()) and abs(float(np.linalg.det(cell))) > 1e-10
    new_positions = positions @ deformation
    new_cell = cell @ deformation if periodic else cell
    volume_change = abs(float(np.linalg.det(deformation))) - 1.0
    return candidate.child(
        candidate_id=f"{candidate.candidate_id}_s{int(volume_change * 1e6)}",
        positions=new_positions,
        cell=new_cell,
        operator=operator,
        operator_params=params,
        metadata={"volume_change": volume_change},
    )


class IsotropicStrain:
    """Homogeneous scale of cell and positions by 1 + ε, ε ~ U(−max, +max)."""

    name = "isotropic_strain"

    def __init__(self, max_strain: float = 0.05) -> None:
        self.max_strain = float(max_strain)

    def apply(self, parent: StructureCandidate, rng: np.random.Generator, params: dict) -> StructureCandidate:
        max_strain = float(params.get("max_strain", self.max_strain))
        if not 0.0 < max_strain < 1.0:
            raise ValueError("max_strain must be in (0, 1)")
        eps = float(rng.uniform(-max_strain, max_strain))
        return _affine_strain(
            parent,
            np.eye(3) * (1.0 + eps),
            self.name,
            {"max_strain": max_strain, "strain": eps},
        )


class AnisotropicStrain:
    """Independent per-axis scale factors, each ~ U(−max, +max)."""

    name = "anisotropic_strain"

    def __init__(self, max_strain: float = 0.05) -> None:
        self.max_strain = float(max_strain)

    def apply(self, parent: StructureCandidate, rng: np.random.Generator, params: dict) -> StructureCandidate:
        max_strain = float(params.get("max_strain", self.max_strain))
        if not 0.0 < max_strain < 1.0:
            raise ValueError("max_strain must be in (0, 1)")
        scales = 1.0 + rng.uniform(-max_strain, max_strain, size=3)
        return _affine_strain(
            parent,
            np.diag(scales),
            self.name,
            {"max_strain": max_strain, "strains": scales.tolist()},
        )
