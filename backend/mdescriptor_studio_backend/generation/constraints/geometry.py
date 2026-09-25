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
from ...datasets.statistics import has_short_contact
from ...lattice import MAX_IMAGES_PER_AXIS, image_shift_limits
from ..models import ConstraintResult, StructureCandidate, normalize_pair_min_distances

_IMAGE_CAP_TOTAL = 1024


def _contact_violation(
    candidate: StructureCandidate,
    *,
    covalent: bool,
    factor: float,
    absolute: float | None,
    pair_cutoff_matrix: np.ndarray | None = None,
) -> tuple[bool, str | None]:
    """Check pair cutoffs with the shared periodic spatial-neighbor scan.

    The reason is set when the periodic image stencil cannot be bounded.
    """
    positions = np.asarray(candidate.positions, dtype=np.float64)
    n = positions.shape[0]
    if n < 2:
        return False, None
    numbers = np.asarray(candidate.atomic_numbers, dtype=np.int64)
    cell = np.asarray(candidate.cell, dtype=np.float64)
    pbc = np.asarray(candidate.pbc, dtype=bool)
    periodic_axes = tuple(int(a) for a in np.flatnonzero(pbc))
    periodic = bool(periodic_axes) and abs(float(np.linalg.det(cell))) > 1e-10

    pair_radii = radii_for(numbers) if covalent else np.full(n, 0.5, dtype=np.float64)
    coefficient = factor if covalent else (float(absolute) if absolute is not None else 0.0)
    cap = coefficient * 2.0 * float(pair_radii.max())
    if pair_cutoff_matrix is not None:
        present_numbers = np.unique(numbers)
        cap = max(cap, float(pair_cutoff_matrix[np.ix_(present_numbers, present_numbers)].max()))
    if periodic and image_shift_limits(
        cell,
        cap,
        axes=periodic_axes,
        max_total_images=_IMAGE_CAP_TOTAL,
    ) is None:
        return False, "periodic cell is too skewed to bound the contact search"
    if has_short_contact(
        positions,
        numbers,
        cell,
        pbc,
        coefficient=coefficient,
        max_images_per_axis=MAX_IMAGES_PER_AXIS,
        include_self_images=False,
        pair_radii=pair_radii,
        pair_cutoff_matrix=pair_cutoff_matrix,
    ):
        return True, None
    return False, None


@dataclass(frozen=True)
class GeometryConstraints:
    """Hard geometry filters; any violation rejects the candidate."""

    min_distance_mode: str = "none"  # none | absolute | covalent
    min_distance: float | None = None  # Å, absolute mode
    min_distance_factor: float = 0.7  # covalent mode
    pair_cutoff_matrix: np.ndarray | None = None
    max_displacement: float | None = None  # Å, from operator metadata
    max_volume_change: float | None = None  # fraction, from operator metadata
    min_volume_per_atom: float | None = None  # Å³/atom, fully periodic structures only
    max_volume_per_atom: float | None = None  # Å³/atom, fully periodic structures only
    composition_locked: bool = True
    atom_count_locked: bool = True

    def validate(self, candidate: StructureCandidate) -> ConstraintResult:
        reasons: list[str] = []
        positions = np.asarray(candidate.positions, dtype=np.float64)
        cell = np.asarray(candidate.cell, dtype=np.float64)
        if np.asarray(candidate.atomic_numbers).size == 0:
            reasons.append("structure contains no atoms")
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
        if not reasons and (self.min_distance_mode != "none" or self.pair_cutoff_matrix is not None):
            contact, reason = _contact_violation(
                candidate,
                covalent=self.min_distance_mode == "covalent",
                factor=self.min_distance_factor,
                absolute=self.min_distance,
                pair_cutoff_matrix=self.pair_cutoff_matrix,
            )
            if reason is not None:
                reasons.append(reason)
            elif contact:
                reasons.append("interatomic contact below the minimum distance")
        if not reasons and self.max_displacement is not None:
            moved = float(candidate.metadata.get("displacement_max", 0.0))
            if moved > self.max_displacement:
                reasons.append(f"displacement {moved:.3f} Å exceeds {self.max_displacement:.3f} Å")
        if not reasons and self.max_volume_change is not None:
            changed = abs(float(candidate.metadata.get("volume_change", 0.0)))
            if changed > self.max_volume_change:
                reasons.append(f"volume change {changed:.1%} exceeds {self.max_volume_change:.1%}")
        if not reasons and bool(np.asarray(candidate.pbc, dtype=bool).all()):
            n_atoms = int(candidate.atomic_numbers.size)
            if n_atoms and (self.min_volume_per_atom is not None or self.max_volume_per_atom is not None):
                volume_per_atom = abs(float(np.linalg.det(cell))) / n_atoms
                if self.min_volume_per_atom is not None and volume_per_atom < self.min_volume_per_atom:
                    reasons.append(
                        f"volume per atom {volume_per_atom:.3f} Å³/atom is below minimum {self.min_volume_per_atom:.3f} Å³/atom"
                    )
                elif self.max_volume_per_atom is not None and volume_per_atom > self.max_volume_per_atom:
                    reasons.append(
                        f"volume per atom {volume_per_atom:.3f} Å³/atom is above maximum {self.max_volume_per_atom:.3f} Å³/atom"
                    )
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

    pair_distances = normalize_pair_min_distances(params.get("min_distance_pairs"))
    pair_cutoff_matrix = None
    if pair_distances:
        from ...datasets.deepmd_symbols import _SYMBOL_TO_Z

        max_atomic_number = max(_SYMBOL_TO_Z.values())
        pair_cutoff_matrix = np.zeros((max_atomic_number + 1, max_atomic_number + 1), dtype=np.float64)
        for pair, distance in pair_distances.items():
            first, second = (_SYMBOL_TO_Z[symbol] for symbol in pair.split("-"))
            pair_cutoff_matrix[first, second] = distance
            pair_cutoff_matrix[second, first] = distance
        pair_cutoff_matrix.setflags(write=False)

    from ...errors import AppError, INVALID_PARAMS

    def _cap(key):
        value = params.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AppError(INVALID_PARAMS, f"{key} must be a positive finite number")
        try:
            value = float(value)
        except (OverflowError, ValueError) as exc:
            raise AppError(INVALID_PARAMS, f"{key} must be a positive finite number") from exc
        if not np.isfinite(value) or value <= 0:
            raise AppError(INVALID_PARAMS, f"{key} must be a positive finite number")
        return value

    min_volume_per_atom = _cap("min_volume_per_atom")
    max_volume_per_atom = _cap("max_volume_per_atom")
    if min_volume_per_atom is not None and max_volume_per_atom is not None and min_volume_per_atom > max_volume_per_atom:
        raise AppError(INVALID_PARAMS, "min_volume_per_atom must not exceed max_volume_per_atom")

    return GeometryConstraints(
        min_distance_mode=mode,
        min_distance=absolute,
        min_distance_factor=float(factor),
        pair_cutoff_matrix=pair_cutoff_matrix,
        max_displacement=_cap("max_displacement"),
        max_volume_change=_cap("max_volume_change"),
        min_volume_per_atom=min_volume_per_atom,
        max_volume_per_atom=max_volume_per_atom,
        # Locked-by-default is the single scientific default shared with the
        # dataclass, parse_request and the UI catalog: perturbation operators
        # must not silently change composition or atom count. Count-changing
        # operators require the client to unlock explicitly.
        composition_locked=bool(params.get("composition_locked", True)),
        atom_count_locked=bool(params.get("atom_count_locked", True)),
    )
