"""Geometry constraints: reject non-physical candidates before descriptors run.

The pipeline order is deliberate — every constraint here is orders of
magnitude cheaper than one descriptor evaluation:

    generate → cheap geometry filter → descriptor → fitness

Running these checks first is what keeps the compute budget spent on
structures that can contribute to the archive.

Minimum-distance checks come in two modes (matching the UI contract):

* ``absolute``  — reject any pair closer than a fixed distance in Å;
* ``covalent``  — reject pairs with r_ij < α·(r_i^cov + r_j^cov), the same
  Cordero table the dataset health check uses. α defaults to 0.7 and is a
  user parameter, not a universal physical law.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ...datasets.covalent_radii import radii_for
from ...lattice import image_shift_limits
from ..models import ConstraintResult, StructureCandidate

# Pairs are visited row-by-row; one row's N distances are a few hundred KB at
# the dataset sizes this studio targets, never an N² matrix.
_CONTACT_CAP = 2.0
_IMAGE_CAP_TOTAL = 1024
# A contact this close (Å) is reported as zero-distance for the reason string.
_EPS = 1e-12


def _contact_margin(
    candidate: StructureCandidate,
    *,
    covalent: bool,
    factor: float,
    absolute: float | None,
) -> tuple[float, str | None]:
    """Minimum over all pairs/images of (distance − threshold).

    Non-negative means no contact violation. Returns (margin, reason); reason
    is set when the periodic stencil cannot be bounded (degenerate cell).
    """
    positions = np.asarray(candidate.positions, dtype=np.float64)
    n = positions.shape[0]
    if n < 2:
        return float("inf"), None
    numbers = np.asarray(candidate.atomic_numbers, dtype=np.int64)
    cell = np.asarray(candidate.cell, dtype=np.float64)
    pbc = np.asarray(candidate.pbc, dtype=bool)
    periodic_axes = tuple(int(a) for a in np.flatnonzero(pbc))
    periodic = bool(periodic_axes) and abs(float(np.linalg.det(cell))) > 1e-10

    if covalent:
        radii = radii_for(numbers)
        cap = _CONTACT_CAP * factor
    else:
        radii = None
        cap = float(absolute)

    shifts = np.zeros((1, 3), dtype=np.float64)
    if periodic:
        limits = image_shift_limits(
            cell,
            cap,
            axes=periodic_axes,
            max_total_images=_IMAGE_CAP_TOTAL,
        )
        if limits is None:
            return 0.0, "periodic cell is too skewed to bound the contact search"
        axes = list(periodic_axes)
        grid = np.meshgrid(*[np.arange(-limits[k], limits[k] + 1, dtype=np.float64) for k in range(len(axes))], indexing="ij")
        combos = np.stack([g.reshape(-1) for g in grid], axis=1)
        shifts = np.zeros((combos.shape[0], 3), dtype=np.float64)
        for col, axis in enumerate(axes):
            shifts[:, axis] = combos[:, col]
        shifts = shifts @ cell  # fractional image offsets → Cartesian

    margin = np.inf
    for i in range(n - 1):
        delta = positions[i + 1 :] - positions[i]  # (m, 3)
        for shift in shifts:
            d = np.sqrt(np.einsum("ij,ij->i", delta - shift, delta - shift) + 1e-300)
            if covalent:
                thresholds = factor * (radii[i] + radii[i + 1 :])
            else:
                thresholds = float(absolute)
            margin = min(margin, float((d - thresholds).min()))
    return margin, None


@dataclass(frozen=True)
class GeometryConstraints:
    """Hard geometry filters; any violation rejects the candidate."""

    min_distance_mode: str = "none"  # none | absolute | covalent
    min_distance: float | None = None  # Å, absolute mode
    min_distance_factor: float = 0.7  # covalent mode
    max_displacement: float | None = None  # Å, from operator metadata
    max_volume_change: float | None = None  # fraction, from operator metadata
    composition_locked: bool = True
    atom_count_locked: bool = True

    def validate(self, candidate: StructureCandidate) -> ConstraintResult:
        reasons: list[str] = []
        positions = np.asarray(candidate.positions, dtype=np.float64)
        cell = np.asarray(candidate.cell, dtype=np.float64)
        if not np.isfinite(positions).all():
            reasons.append("positions contain NaN or Inf")
        if not np.isfinite(cell).all():
            reasons.append("cell contains NaN or Inf")
        if not reasons:
            pbc = np.asarray(candidate.pbc, dtype=bool)
            if bool(pbc.any()):
                det = float(np.linalg.det(cell)) if cell.shape == (3, 3) else 0.0
                if not np.isfinite(det) or abs(det) < 1e-10:
                    reasons.append("periodic cell is singular")
        if not reasons and self.min_distance_mode != "none":
            margin, reason = _contact_margin(
                candidate,
                covalent=self.min_distance_mode == "covalent",
                factor=self.min_distance_factor,
                absolute=self.min_distance,
            )
            if reason is not None:
                reasons.append(reason)
            elif margin < 0.0:
                reasons.append(
                    "interatomic contact below the minimum distance"
                    if margin < -_EPS
                    else "atoms overlap"
                )
        if not reasons and self.max_displacement is not None:
            moved = float(candidate.metadata.get("displacement_max", 0.0))
            if moved > self.max_displacement:
                reasons.append(f"displacement {moved:.3f} Å exceeds {self.max_displacement:.3f} Å")
        if not reasons and self.max_volume_change is not None:
            changed = abs(float(candidate.metadata.get("volume_change", 0.0)))
            if changed > self.max_volume_change:
                reasons.append(f"volume change {changed:.1%} exceeds {self.max_volume_change:.1%}")
        if not reasons and self.composition_locked:
            want = candidate.metadata.get("parent_composition")
            if want is not None and np.sort(candidate.atomic_numbers).tolist() != list(want):
                reasons.append("composition differs from the parent structure")
        if not reasons and self.atom_count_locked:
            want = candidate.metadata.get("parent_atom_count")
            if want is not None and int(candidate.atomic_numbers.size) != int(want):
                reasons.append("atom count differs from the parent structure")
        return ConstraintResult(valid=not reasons, penalty=0.0, reasons=reasons)


def build_constraints(params: dict) -> GeometryConstraints:
    """Build the constraint set from the (already validated) request dict."""
    params = dict(params or {})
    mode = params.get("min_distance_mode", "none")
    absolute = params.get("min_distance")
    if mode == "absolute":
        if isinstance(absolute, bool) or not isinstance(absolute, (int, float)) or absolute <= 0:
            from ...errors import AppError, INVALID_PARAMS

            raise AppError(INVALID_PARAMS, "min_distance must be a positive number for absolute mode")
        absolute = float(absolute)
    else:
        absolute = None
    factor = params.get("min_distance_factor", 0.7)

    def _cap(key):
        value = params.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(float(value)) or float(value) <= 0:
            from ...errors import AppError, INVALID_PARAMS

            raise AppError(INVALID_PARAMS, f"{key} must be a positive finite number")
        return float(value)

    return GeometryConstraints(
        min_distance_mode=mode,
        min_distance=absolute,
        min_distance_factor=float(factor),
        max_displacement=_cap("max_displacement"),
        max_volume_change=_cap("max_volume_change"),
        composition_locked=bool(params.get("composition_locked", True)),
        atom_count_locked=bool(params.get("atom_count_locked", True)),
    )
