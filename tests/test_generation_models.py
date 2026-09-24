"""Generation unit tests: operator math, geometry constraints, evaluator
alignment, and archive distances vs. brute force."""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis.sampling import fit_scaling
from mdescriptor_studio_backend.datasets.base import DatasetFrame
from mdescriptor_studio_backend.generation.archive import DescriptorArchive, LocalEnvironmentArchive
from mdescriptor_studio_backend.generation.constraints import GeometryConstraints, build_constraints
from mdescriptor_studio_backend.generation.evaluator import evaluate_batch
from mdescriptor_studio_backend.generation.models import StructureCandidate
from mdescriptor_studio_backend.generation.operators import (
    AnisotropicStrain,
    AtomicDisplacement,
    CellShear,
    IsotropicStrain,
    displaced,
    strained,
)


def _frame(positions=None, cell_scale=5.0, numbers=(14, 14)) -> DatasetFrame:
    positions = np.asarray(positions if positions is not None else [[0.0, 0.0, 0.0], [2.0, 2.0, 2.0]])
    return DatasetFrame(
        numbers=np.asarray(numbers, dtype=np.int64),
        positions=positions,
        cell=np.eye(3) * cell_scale,
        pbc=np.ones(3, dtype=bool),
    )


def _candidate(frame=None) -> StructureCandidate:
    return StructureCandidate.from_frame(
        frame or _frame(),
        candidate_id="seed_0",
        parent_frame=0,
        metadata={"parent_composition": [14, 14], "parent_atom_count": 2},
    )


# ---------------------------------------------------------------- operators
class TestOperators:
    def test_displacement_preserves_composition_and_lineage(self):
        rng = np.random.default_rng(0)
        parent = _candidate()
        child = AtomicDisplacement(0.1).apply(parent, rng, {"max_sigma": 0.1})
        assert child.operator == "atomic_displacement"
        assert child.generation == 1
        assert child.parent_candidate_id == "seed_0"
        np.testing.assert_array_equal(child.atomic_numbers, parent.atomic_numbers)
        assert 0.0 < child.metadata["displacement_max"] <= 0.1 * 4.0  # ~4 sigma tail
        assert not np.allclose(child.positions, parent.positions)

    @pytest.mark.parametrize(
        "operator",
        [
            IsotropicStrain(0.05),
            AnisotropicStrain(0.05),
            CellShear(0.05),
        ],
    )
    def test_strain_operators_preserve_fractional_coordinates(self, operator):
        rng = np.random.default_rng(1)
        frame = _frame(positions=[[1.0, 1.5, 2.0], [3.0, 0.5, 4.0]], cell_scale=6.0)
        parent = _candidate(frame)
        child = operator.apply(parent, rng, {})
        parent_frac = parent.positions @ np.linalg.inv(parent.cell)
        child_frac = child.positions @ np.linalg.inv(child.cell)
        np.testing.assert_allclose(child_frac, parent_frac, atol=1e-12)
        assert "volume_change" in child.metadata

    def test_isotropic_strain_matches_frame_strain(self):
        frame = _frame()
        parent = _candidate(frame)
        child = IsotropicStrain(0.05).apply(parent, np.random.default_rng(4), {"max_strain": 1e-9})
        scale = child.operator_params["strain"] + 1.0
        np.testing.assert_allclose(child.positions, frame.positions * scale, rtol=1e-9)
        np.testing.assert_allclose(child.cell, frame.cell * scale, rtol=1e-9)

    def test_displaced_frame_function(self):
        frame = _frame()
        delta = np.ones_like(frame.positions) * 0.1
        moved = displaced(frame, delta)
        np.testing.assert_allclose(moved.positions, frame.positions + 0.1)
        assert moved.cell is frame.cell or np.allclose(moved.cell, frame.cell)


# ---------------------------------------------------------------- constraints
class TestGeometryConstraints:
    def test_covalent_min_distance_rejects_close_contact(self):
        frame = _frame(positions=[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])  # 1.0 Å << 1.55 Å
        rules = build_constraints({"min_distance_mode": "covalent", "min_distance_factor": 0.7})
        verdict = rules.validate(_candidate(frame))
        assert not verdict.valid
        assert any("minimum distance" in r for r in verdict.reasons)

    def test_absolute_min_distance(self):
        close = _candidate(_frame(positions=[[0, 0, 0], [1.0, 0, 0]]))
        far = _candidate(_frame(positions=[[0, 0, 0], [3.0, 0, 0]]))
        rules = build_constraints({"min_distance_mode": "absolute", "min_distance": 2.0})
        assert not rules.validate(close).valid
        assert rules.validate(far).valid

    def test_none_mode_skips_contact_check(self):
        close = _candidate(_frame(positions=[[0, 0, 0], [0.5, 0, 0]]))
        rules = build_constraints({"min_distance_mode": "none"})
        assert rules.validate(close).valid

    def test_nan_positions_rejected(self):
        frame = _frame()
        bad = _candidate(frame)
        bad.positions = bad.positions.copy()
        bad.positions[0, 0] = np.nan
        assert not GeometryConstraints().validate(bad).valid

    def test_singular_periodic_cell_rejected(self):
        frame = DatasetFrame(
            numbers=np.array([14]),
            positions=np.zeros((1, 3)),
            cell=np.zeros((3, 3)),
            pbc=np.ones(3, dtype=bool),
        )
        verdict = GeometryConstraints().validate(_candidate(frame))
        assert not verdict.valid

    def test_displacement_and_volume_caps_use_operator_metadata(self):
        rules = build_constraints({"max_displacement": 0.1, "max_volume_change": 0.05})
        candidate = _candidate()
        candidate.metadata["displacement_max"] = 0.2
        assert not rules.validate(candidate).valid
        candidate.metadata["displacement_max"] = 0.05
        candidate.metadata["volume_change"] = 0.2
        verdict = rules.validate(candidate)
        assert not verdict.valid
        assert any("volume" in r for r in verdict.reasons)

    def test_composition_and_atom_count_locks(self):
        rules = GeometryConstraints()
        candidate = _candidate()
        candidate.atomic_numbers = np.array([14, 8])  # differs from parent [14, 14]
        assert not rules.validate(candidate).valid
        candidate = _candidate()
        candidate.metadata["parent_atom_count"] = 3
        assert not rules.validate(candidate).valid


# ---------------------------------------------------------------- evaluator
class _Computed:
    def __init__(self, values, row_offsets=None):
        self.values = values
        self.row_offsets = row_offsets


class TestEvaluatorAlignment:
    def test_pools_structure_values_and_keeps_atomic_rows(self):
        values = np.arange(8, dtype=np.float64).reshape(4, 2)
        computed = _Computed(values, np.array([0, 2, 2, 4]))
        result = evaluate_batch(computed, 3)
        np.testing.assert_allclose(result.structure_values[0], values[0:2].mean(axis=0))
        np.testing.assert_allclose(result.structure_values[1], np.zeros(2))  # empty structure
        np.testing.assert_allclose(result.atomic_values, values)
        np.testing.assert_array_equal(result.row_offsets, [0, 2, 2, 4])

    def test_identity_path_for_structure_level_results(self):
        values = np.arange(6, dtype=np.float64).reshape(3, 2)
        result = evaluate_batch(_Computed(values), 3)
        np.testing.assert_allclose(result.structure_values, values)
        assert result.atomic_values is None
        assert result.row_offsets is None

    def test_misaligned_result_is_an_error(self):
        with pytest.raises(Exception):
            evaluate_batch(_Computed(np.zeros((5, 2)), np.array([0, 5])), 3)


# ---------------------------------------------------------------- archive
def _brute_nearest(query, reference):
    d2 = ((query[:, None, :] - reference[None, :, :]) ** 2).sum(axis=2)
    return np.sqrt(np.clip(d2.min(axis=1), 0.0, None))


class TestArchive:
    def test_nearest_matches_brute_force(self):
        rng = np.random.default_rng(7)
        reference = rng.normal(size=(50, 6))
        scaling, _ = fit_scaling(reference, "robust")
        archive = DescriptorArchive(reference, scaling)
        query = rng.normal(size=(20, 6))
        scaled_query = (query - scaling.center) / scaling.scale
        scaled_reference = (reference - scaling.center) / scaling.scale
        np.testing.assert_allclose(archive.nearest(query), _brute_nearest(scaled_query, scaled_reference), rtol=1e-10)

    def test_accepted_rows_change_nearest(self):
        reference = np.array([[0.0, 0.0], [1.0, 1.0]])
        scaling, _ = fit_scaling(reference, "raw")
        archive = DescriptorArchive(reference, scaling)
        far = np.array([[5.0, 5.0]])
        assert archive.nearest(far)[0] > 4.0
        from mdescriptor_studio_backend.generation.models import ArchiveEntry

        archive.add(np.array([[4.9, 4.9]]), [ArchiveEntry("c", 0, 0.0, 0.0, 1)])
        assert archive.size == 1
        assert archive.nearest(far)[0] < 0.2

    def test_coverage_radius_and_contains_near(self):
        reference = np.array([[0.0, 0.0], [10.0, 10.0]])
        scaling, _ = fit_scaling(reference, "raw")
        archive = DescriptorArchive(reference, scaling)
        radius = archive.coverage_radius()
        assert radius == pytest.approx(0.0)  # reference covers itself
        assert archive.contains_near(np.array([[0.1, 0.1]]), 0.5)[0]

    def test_local_environment_novel_counts(self):
        reference = np.array([[0.0], [1.0], [2.0], [3.0]])
        scaling, _ = fit_scaling(reference, "raw")
        archive = LocalEnvironmentArchive(reference, scaling)
        counts = archive.novel_count(np.array([[0.1], [8.0]]), 0.25)
        np.testing.assert_array_equal(counts, [0, 1])

    def test_empty_reference_rejected(self):
        with pytest.raises(ValueError):
            DescriptorArchive(np.zeros((0, 3)), fit_scaling(np.ones((2, 3)), "raw")[0])
