"""Property-based geometry tests (audit R5.2).

Random small cells and single-atom periodic cells, checked against
hand-rolled brute-force image scans and structural invariants. hypothesis
is not a dependency here, so the properties run over a fixed bank of seeded
random cases — enough to cover the failure modes the audit lists (single
atoms, short lattice vectors, partial periodicity, atom permutation).
"""

from __future__ import annotations

from itertools import product

import numpy as np

from mdescriptor_studio_backend.datasets.base import DatasetFrame
from mdescriptor_studio_backend.generation.constraints import build_constraints
from mdescriptor_studio_backend.generation.constraints.geometry import _contact_violation
from mdescriptor_studio_backend.generation.models import StructureCandidate
from mdescriptor_studio_backend.lattice import MAX_IMAGES_PER_AXIS

RNG = np.random.default_rng(20260930)
CASES = 40


def _random_cell(rng: np.random.Generator) -> np.ndarray:
    """Well-conditioned random cell: random orientation, 1.5–4 Å row norms."""
    q, _ = np.linalg.qr(rng.normal(size=(3, 3)))
    scales = rng.uniform(1.5, 4.0, size=3)
    return (np.diag(scales) @ q.T) * (1.0 if rng.uniform() < 0.7 else 1.0)


def _random_candidate(rng: np.random.Generator, n_atoms: int, cell: np.ndarray, pbc):
    fractional = rng.uniform(0.05, 0.95, size=(n_atoms, 3))
    frame = DatasetFrame(
        numbers=rng.choice(np.array([6, 14, 29]), size=n_atoms).astype(np.int64),
        positions=fractional @ cell,
        cell=cell,
        pbc=np.asarray(pbc, dtype=bool),
        index=0,
    )
    return StructureCandidate.from_frame(frame, candidate_id="p", parent_frame=0)


def _absolute_rules(distance: float):
    return build_constraints({"min_distance_mode": "absolute", "min_distance": distance})


def _brute_force_min_distance(
    positions: np.ndarray,
    cell: np.ndarray,
    pbc: np.ndarray,
    t_max: float,
) -> float:
    """Naive reference scan: same stencil limits the shared kernel uses."""
    inverse = np.linalg.inv(cell)
    limits = [
        min(MAX_IMAGES_PER_AXIS, int(t_max * np.linalg.norm(inverse[:, axis])) + 1) if pbc[axis] else 0
        for axis in range(3)
    ]
    n = positions.shape[0]
    best = float("inf")
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            best = min(best, float(np.linalg.norm(positions[i] - positions[j])))
    for shift in product(*[range(-l, l + 1) for l in limits]):
        shift = np.asarray(shift, dtype=np.float64)
        if not shift.any():
            continue
        for i in range(n):
            for j in range(n):
                distance = float(np.linalg.norm(positions[i] - (positions[j] + shift @ cell)))
                best = min(best, distance)
    return best


class TestSingleAtomCells:
    def test_cubic_single_atom_exact_boundary(self):
        # For a one-atom cubic cell the minimum self-image distance is the
        # lattice constant a, so the verdict must be exactly (a < cutoff).
        for _ in range(CASES):
            a = float(RNG.uniform(1.0, 4.0))
            cutoff = float(RNG.uniform(1.0, 3.0))
            candidate = _random_candidate(RNG, 1, np.eye(3) * a, [True, True, True])
            contact, reason = _contact_violation(candidate, covalent=False, factor=0.7, absolute=cutoff)
            assert reason is None
            assert contact == (a < cutoff), (a, cutoff, contact)

    def test_random_single_atom_cells_match_brute_force(self):
        skipped = 0
        for _ in range(CASES):
            cell = _random_cell(RNG)
            candidate = _random_candidate(RNG, 1, cell, [True, True, True])
            cutoff = float(RNG.uniform(0.8, 2.0))
            contact, reason = _contact_violation(candidate, covalent=False, factor=0.7, absolute=cutoff)
            if reason is not None:
                skipped += 1  # stencil cap: outside the property's domain
                continue
            brute = _brute_force_min_distance(candidate.positions, cell, candidate.pbc, cutoff)
            assert contact == (brute < cutoff), (cell.tolist(), cutoff, contact, brute)
        assert skipped <= CASES // 4


class TestRandomSmallCells:
    def test_multi_atom_cells_match_brute_force(self):
        # Two and three atoms in small random cells: the shared kernel must
        # agree with a naive stencil scan over exactly the same image set.
        skipped = 0
        for _ in range(CASES):
            cell = _random_cell(RNG)
            n_atoms = int(RNG.integers(2, 4))
            candidate = _random_candidate(RNG, n_atoms, cell, [True, True, True])
            cutoff = float(RNG.uniform(0.8, 2.0))
            contact, reason = _contact_violation(candidate, covalent=False, factor=0.7, absolute=cutoff)
            if reason is not None:
                skipped += 1
                continue
            brute = _brute_force_min_distance(candidate.positions, cell, candidate.pbc, cutoff)
            assert contact == (brute < cutoff), (cell.tolist(), n_atoms, cutoff, contact, brute)
        assert skipped <= CASES // 4

    def test_partial_periodicity_agrees_with_brute_force(self):
        # Only the periodic axes contribute images: a short non-periodic
        # lattice vector must not trip the check.
        for _ in range(CASES):
            cell = _random_cell(RNG)
            cell[:, 0] = 1.2 * np.sign(cell[0, 0] or 1.0)  # short a-axis
            pbc = [bool(RNG.integers(0, 2)) for _ in range(3)]
            candidate = _random_candidate(RNG, 2, cell, pbc)
            cutoff = 1.5
            contact, reason = _contact_violation(candidate, covalent=False, factor=0.7, absolute=cutoff)
            if reason is not None:
                continue
            brute = _brute_force_min_distance(candidate.positions, cell, np.asarray(pbc, dtype=bool), cutoff)
            assert contact == (brute < cutoff), (pbc, contact, brute)


class TestStructuralInvariants:
    def test_atom_permutation_preserves_the_verdict(self):
        for _ in range(CASES):
            cell = _random_cell(RNG)
            candidate = _random_candidate(RNG, int(RNG.integers(2, 6)), cell, [True, True, True])
            covalent = bool(RNG.integers(0, 2))
            rules = (
                build_constraints({"min_distance_mode": "covalent", "min_distance_factor": 0.7})
                if covalent
                else _absolute_rules(float(RNG.uniform(1.0, 2.5)))
            )
            verdict = rules.validate(candidate)
            order = RNG.permutation(candidate.atomic_numbers.size)
            shuffled = StructureCandidate(
                candidate_id=candidate.candidate_id,
                atomic_numbers=candidate.atomic_numbers[order],
                positions=candidate.positions[order],
                cell=candidate.cell,
                pbc=candidate.pbc,
                parent_frame=candidate.parent_frame,
                parent_candidate_id=candidate.parent_candidate_id,
                generation=candidate.generation,
                operator=candidate.operator,
            )
            assert rules.validate(shuffled).valid == verdict.valid

    def test_periodic_lattice_translation_preserves_the_verdict(self):
        for _ in range(CASES):
            cell = _random_cell(RNG)
            candidate = _random_candidate(RNG, int(RNG.integers(1, 4)), cell, [True, True, True])
            rules = _absolute_rules(float(RNG.uniform(1.0, 2.5)))
            verdict = rules.validate(candidate)
            lattice_shift = np.asarray(RNG.integers(-2, 3, size=3), dtype=np.float64) @ cell
            shifted = StructureCandidate(
                candidate_id=candidate.candidate_id,
                atomic_numbers=candidate.atomic_numbers,
                positions=candidate.positions + lattice_shift,
                cell=candidate.cell,
                pbc=candidate.pbc,
                parent_frame=candidate.parent_frame,
                parent_candidate_id=candidate.parent_candidate_id,
                generation=candidate.generation,
                operator=candidate.operator,
            )
            assert rules.validate(shifted).valid == verdict.valid

    def test_scaling_a_single_atom_cell_monotonically_relaxes_the_check(self):
        # For 1-atom cells the minimum image distance scales linearly with
        # the cell: a cell that passes a cutoff cannot fail a smaller one.
        cell = _random_cell(RNG)
        base = _random_candidate(RNG, 1, cell, [True, True, True])
        cutoff = 1.5
        contact, _ = _contact_violation(base, covalent=False, factor=0.7, absolute=cutoff)
        if not contact:
            scaled = StructureCandidate(
                candidate_id=base.candidate_id,
                atomic_numbers=base.atomic_numbers,
                positions=base.positions,
                cell=base.cell * 1.5,
                pbc=base.pbc,
                parent_frame=base.parent_frame,
                parent_candidate_id=base.parent_candidate_id,
                generation=base.generation,
                operator=base.operator,
            )
            still, reason = _contact_violation(scaled, covalent=False, factor=0.7, absolute=cutoff)
            assert reason is None and not still
