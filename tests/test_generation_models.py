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

    def test_displacement_max_is_the_true_per_atom_norm(self):
        rng = np.random.default_rng(0)
        parent = _candidate()
        child = AtomicDisplacement(0.1).apply(parent, rng, {"max_sigma": 0.1})
        delta = child.positions - parent.positions
        expected = float(np.linalg.norm(delta, axis=1).max())
        # The metadata must understate nothing: max |Δx| component ≤ ||Δr||.
        assert child.metadata["displacement_max"] == pytest.approx(expected, rel=1e-12)
        assert child.metadata["displacement_max"] >= float(np.abs(delta).max()) - 1e-12

    def test_hard_cutoff_bounds_every_displacement_norm(self):
        rng = np.random.default_rng(0)
        parent = _candidate()
        child = AtomicDisplacement(0.4).apply(parent, rng, {"max_sigma": 0.4, "hard_cutoff": 0.05})
        delta = child.positions - parent.positions
        norms = np.linalg.norm(delta, axis=1)
        assert norms.max() <= 0.05 + 1e-12
        # Directions are preserved: rescaled atoms keep their unit vector.
        assert "hard_cutoff" in child.operator_params
        assert child.operator_params["hard_cutoff"] == 0.05

    def test_no_cutoff_keeps_the_gaussian_tail(self):
        rng = np.random.default_rng(0)
        parent = _candidate()
        uncapped = AtomicDisplacement(0.4).apply(parent, rng, {"max_sigma": 0.4})
        capped = AtomicDisplacement(0.4).apply(parent, rng, {"max_sigma": 0.4, "hard_cutoff": 0.01})
        # Without a cutoff the sampled geometry is untouched by the operator.
        assert np.linalg.norm(uncapped.positions - parent.positions, axis=1).max() > 0.01
        assert np.linalg.norm(capped.positions - parent.positions, axis=1).max() <= 0.01 + 1e-12

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

    def test_accepted_coverage_radius_measures_generated_coverage(self):
        from mdescriptor_studio_backend.generation.models import ArchiveEntry

        reference = np.array([[0.0, 0.0], [10.0, 10.0]])
        scaling, _ = fit_scaling(reference, "raw")
        archive = DescriptorArchive(reference, scaling)
        # Nothing accepted yet: there is no generated coverage to measure.
        assert archive.accepted_coverage_radius() is None

        archive.add(np.array([[9.0, 9.0]]), [ArchiveEntry("c0", 0, 0.0, 0.0, 1)])
        first = archive.accepted_coverage_radius()
        # The reference point (0,0) is the farthest from the single accept.
        assert first == pytest.approx(9.0 * np.sqrt(2.0))
        # The metric must not silently fall back to the reference rows, which
        # cover the domain exactly and would always report 0.0.
        assert first > 0.0

        archive.add(np.array([[0.5, 0.5]]), [ArchiveEntry("c1", 1, 0.0, 0.0, 1)])
        second = archive.accepted_coverage_radius()
        assert second == pytest.approx(np.sqrt(2.0))
        assert second < first  # non-increasing as accepts accumulate

        # Arbitrary query domains work too.
        assert archive.accepted_coverage_radius(query=np.array([[9.0, 9.0]])) == pytest.approx(0.0)

    def test_local_environment_novel_counts(self):
        reference = np.array([[0.0], [1.0], [2.0], [3.0]])
        scaling, _ = fit_scaling(reference, "raw")
        archive = LocalEnvironmentArchive(reference, scaling)
        counts = archive.novel_count(np.array([[0.1], [8.0]]), 0.25)
        np.testing.assert_array_equal(counts, [0, 1])

    def test_empty_reference_rejected(self):
        with pytest.raises(ValueError):
            DescriptorArchive(np.zeros((0, 3)), fit_scaling(np.ones((2, 3)), "raw")[0])


# ---------------------------------------------------------------- budget parsing
class TestBudgetParsing:
    def test_zero_discovery_threshold_is_preserved(self):
        from mdescriptor_studio_backend.generation.models import parse_budget
        from mdescriptor_studio_backend.errors import AppError

        budget = parse_budget({"min_novel_per_100_evals": 0.0, "discovery_window": 5})
        # 0.0 is a legal value ("never stop on the discovery rate") — the old
        # `or 1.0` parser silently turned it into the default.
        assert budget.min_novel_per_100_evals == 0.0

    def test_discovery_threshold_defaults_when_absent(self):
        from mdescriptor_studio_backend.generation.models import parse_budget

        assert parse_budget({}).min_novel_per_100_evals == 1.0
        assert parse_budget({"min_novel_per_100_evals": None}).min_novel_per_100_evals == 1.0
        assert parse_budget({"min_novel_per_100_evals": 2.5}).min_novel_per_100_evals == 2.5

    def test_negative_discovery_threshold_rejected(self):
        from mdescriptor_studio_backend.generation.models import parse_budget
        from mdescriptor_studio_backend.errors import AppError

        with pytest.raises(AppError):
            parse_budget({"min_novel_per_100_evals": -0.1})


# ---------------------------------------------------------------- request parsing
def _request(**overrides) -> dict:
    payload = {
        "dataset_id": "ds",
        "descriptor_run_id": "run",
        "optimizer": "random",
        "optimizer_params": {"children_per_seed": 4, "batch_accept": 2, "n_seeds": 8},
        "objective": {"type": "novelty"},
        "operators": {"atomic_displacement": {"enabled": True, "max_sigma": 0.1}},
        "constraints": {"min_distance_mode": "none"},
        "budget": {"max_evaluations": 100},
        "seed": 42,
    }
    payload.update(overrides)
    return payload


class TestRequestParsing:
    def test_locks_default_to_true_across_every_layer(self):
        from mdescriptor_studio_backend.generation.models import parse_request
        from mdescriptor_studio_backend.generation.registry import GENERATION_REGISTRY

        request = parse_request(_request())
        # The single locked-by-default scientific default: dataclass,
        # builder, parser and catalog must agree (review §15).
        assert request.constraints["composition_locked"] is True
        assert request.constraints["atom_count_locked"] is True
        built = build_constraints({})
        assert built.composition_locked and built.atom_count_locked
        catalog = GENERATION_REGISTRY.catalog()["constraints"]
        assert catalog["composition_locked"] is True and catalog["atom_count_locked"] is True

    def test_count_changing_operator_with_implicit_lock_is_rejected(self):
        from mdescriptor_studio_backend.generation.models import parse_request
        from mdescriptor_studio_backend.errors import AppError

        # Omitting the lock flags now means "locked", so a vacancy payload
        # must unlock explicitly instead of silently changing composition.
        with pytest.raises(AppError, match="unlocked"):
            parse_request(_request(operators={"vacancy": {"enabled": True}}))

    def test_optimizer_params_are_normalized_integers(self):
        from mdescriptor_studio_backend.generation.models import parse_request

        request = parse_request(_request())
        assert request.optimizer_params["n_seeds"] == 8
        assert request.optimizer_params["children_per_seed"] == 4
        assert request.optimizer_params["batch_accept"] == 2

    def test_fractional_and_out_of_range_optimizer_params_rejected(self):
        from mdescriptor_studio_backend.generation.models import parse_request
        from mdescriptor_studio_backend.errors import AppError

        with pytest.raises(AppError, match="integer"):
            parse_request(_request(optimizer_params={"children_per_seed": 2.5}))
        with pytest.raises(AppError, match="between"):
            parse_request(_request(optimizer_params={"n_seeds": 0}))
        # batch_accept cannot exceed one round's proposal count.
        with pytest.raises(AppError, match="between"):
            parse_request(_request(optimizer_params={"n_seeds": 2, "children_per_seed": 2, "batch_accept": 5}))

    def test_unknown_optimizer_param_rejected(self):
        from mdescriptor_studio_backend.generation.models import parse_request
        from mdescriptor_studio_backend.errors import AppError

        with pytest.raises(AppError, match="unknown optimizer params"):
            parse_request(_request(optimizer_params={"mutation_rate": 0.2}))

    def test_displacement_sigma_and_cutoff_validated_at_submit(self):
        from mdescriptor_studio_backend.generation.models import parse_request
        from mdescriptor_studio_backend.errors import AppError

        ok = parse_request(
            _request(operators={"atomic_displacement": {"enabled": True, "max_sigma": 0.2, "hard_cutoff": 0.5}})
        )
        assert ok.operators[0].params["hard_cutoff"] == 0.5
        with pytest.raises(AppError, match="hard_cutoff"):
            parse_request(
                _request(operators={"atomic_displacement": {"enabled": True, "max_sigma": 0.2, "hard_cutoff": -1}})
            )
        with pytest.raises(AppError, match="max_sigma"):
            parse_request(_request(operators={"atomic_displacement": {"enabled": True, "max_sigma": 9.0}}))
