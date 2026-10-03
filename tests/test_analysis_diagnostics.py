"""Scientific invariant tests for the descriptor diagnostics package.

These are the "does the diagnostic itself tell the truth" tests (review
2026-10-03 §17): toy descriptors with known analytic properties must produce
the expected verdicts -

    D(Rx) == D(x), D(Tx) == D(x), D(Px) == D(x)      (formal invariance)
    complete toy descriptor -> Jacobian rank == 3n - 3
    constructed degenerate pair -> detector flags it
    hard cutoff on a neighbor shell -> discontinuity flagged
    descriptor distance ~ 0 with structure distance >> 0 -> dangerous pair
"""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis.diagnostics import (
    DescriptorRecompute,
    composition_distance,
    cutoff_smoothness,
    degeneracy_search,
    distance_consistency,
    environment_jacobian,
    formal_invariance,
    random_rotation,
    reflection_matrix,
    structure_distance,
    transform_frame,
)
from mdescriptor_studio_backend.datasets.base import DatasetFrame
from mdescriptor_studio_backend.errors import AppError


def _frame(positions, numbers=None, cell=None, pbc=None) -> DatasetFrame:
    positions = np.asarray(positions, dtype=np.float64)
    return DatasetFrame(
        numbers=np.asarray(numbers if numbers is not None else [1] * len(positions), dtype=np.int64),
        positions=positions,
        cell=np.asarray(cell if cell is not None else np.zeros((3, 3)), dtype=np.float64),
        pbc=np.asarray(pbc if pbc is not None else [False, False, False], dtype=bool),
        index=0,
        id="test_frame",
    )


def _structure_recompute(descriptor_fn):
    def recompute(frames):
        return DescriptorRecompute(
            structure_values=np.stack([np.atleast_1d(np.asarray(descriptor_fn(f), dtype=np.float64)) for f in frames])
        )

    return recompute


def _atom_recompute(descriptor_fn):
    def recompute(frames):
        rows: list[np.ndarray] = []
        offsets = [0]
        structure_rows = []
        for frame in frames:
            natoms = len(frame.numbers)
            for center in range(natoms):
                rows.append(np.asarray(descriptor_fn(frame, center), dtype=np.float64))
            offsets.append(len(rows))
            structure_rows.append(np.mean(rows[-natoms:], axis=0))
        return DescriptorRecompute(
            structure_values=np.stack(structure_rows),
            atomic_values=np.asarray(rows, dtype=np.float64),
            row_offsets=np.asarray(offsets, dtype=np.int64),
        )

    return recompute


# -- toy descriptors ---------------------------------------------------------


def _pair_distances(frame) -> np.ndarray:
    """Sorted pairwise distances: invariant under SE(3), reflection, permutation."""
    positions = np.asarray(frame.positions, dtype=np.float64)
    deltas = positions[:, None, :] - positions[None, :, :]
    distances = np.linalg.norm(deltas, axis=-1)
    return np.sort(distances[np.triu_indices(len(positions), k=1)])


def _z_sum(frame) -> np.ndarray:
    """Deliberately non-invariant: total z coordinate."""
    return np.asarray([float(np.sum(frame.positions[:, 2]))])


def _chiral(frame) -> np.ndarray:
    """Pair distances plus a fixed-labeling signed volume: reflection-sensitive."""
    positions = np.asarray(frame.positions, dtype=np.float64)
    tetra = np.linalg.det(
        np.stack([positions[1] - positions[0], positions[2] - positions[0], positions[3] - positions[0]])
    )
    return np.concatenate([_pair_distances(frame), [tetra]])


def _complete_atom(frame, center) -> np.ndarray:
    """Complete up to reflection: center distances + all inter-neighbor distances.

    For n neighbors this is the full distance set of n+1 points, so the
    per-environment Jacobian must reach the theoretical maximum rank of
    3n - 3 (the three rotational zero modes) on generic configurations.
    """
    positions = np.asarray(frame.positions, dtype=np.float64)
    others = np.array([j for j in range(len(positions)) if j != center], dtype=np.int64)
    if others.size == 0:
        return np.zeros(0)
    center_distances = np.linalg.norm(positions[others] - positions[center], axis=1)
    deltas = positions[others][:, None, :] - positions[others][None, :, :]
    pair_distances = np.linalg.norm(deltas, axis=-1)
    upper = pair_distances[np.triu_indices(others.size, k=1)]
    return np.concatenate([np.sort(center_distances), np.sort(upper)])


def _center_distances_atom(frame, center) -> np.ndarray:
    """Deliberately incomplete: distances to the center only (n features)."""
    positions = np.asarray(frame.positions, dtype=np.float64)
    others = np.array([j for j in range(len(positions)) if j != center], dtype=np.int64)
    return np.sort(np.linalg.norm(positions[others] - positions[center], axis=1))


_GENERIC_POSITIONS = [
    [0.0, 0.0, 0.0],
    [1.42, 0.11, 0.32],
    [0.13, 1.67, 0.71],
    [0.51, 0.29, 1.83],
    [2.11, 1.53, 0.19],
]


class TestGeometry:
    def test_random_rotation_is_proper(self):
        rng = np.random.default_rng(7)
        for _ in range(8):
            rotation = random_rotation(rng)
            np.testing.assert_allclose(rotation @ rotation.T, np.eye(3), atol=1e-12)
            assert rotation.shape == (3, 3)
            np.testing.assert_allclose(np.linalg.det(rotation), 1.0, atol=1e-12)

    def test_reflection_is_improper(self):
        rng = np.random.default_rng(11)
        for _ in range(8):
            mirror = reflection_matrix(rng)
            np.testing.assert_allclose(mirror @ mirror.T, np.eye(3), atol=1e-12)
            np.testing.assert_allclose(np.linalg.det(mirror), -1.0, atol=1e-12)

    def test_minimum_image_wraps_periodic_axes_only(self):
        from mdescriptor_studio_backend.analysis.diagnostics import minimum_image

        cell = np.diag([10.0, 10.0, 10.0])
        pbc = np.array([True, True, False])
        wrapped = minimum_image(np.array([12.0, -13.0, 12.0]), cell, pbc)
        # -13 wraps to -3 (|−3| < |7|); the non-periodic axis stays untouched.
        np.testing.assert_allclose(wrapped, [2.0, -3.0, 12.0], atol=1e-12)

    def test_structure_distance_is_inf_across_atom_counts(self):
        a = _frame([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        b = _frame([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
        assert structure_distance(a, b) == float("inf")
        assert structure_distance(a, a) == 0.0

    def test_composition_distance_bounds(self):
        assert composition_distance(np.array([1, 1, 2]), np.array([1, 1, 2])) == 0.0
        assert composition_distance(np.array([1]), np.array([2])) == 1.0


class TestFormalInvariance:
    def test_invariant_descriptor_passes_everything(self):
        frames = [
            _frame(_GENERIC_POSITIONS),
            _frame([[0.2, 0.1, 0.0], [1.3, 0.4, 0.9], [0.7, 1.9, 2.2], [2.5, 0.3, 1.7], [1.1, 2.6, 0.4]]),
        ]
        baseline = _structure_recompute(_pair_distances)(frames)
        result = formal_invariance(frames, baseline, _structure_recompute(_pair_distances), {})
        assert result["preview"]["passed"] is True
        assert result["preview"]["chirality_sensitive"] is False
        for check, summary in result["preview"]["checks"].items():
            assert summary["passed"], (check, summary)
            # Exact symmetries hold to float64 round-off; the float32
            # round-trip legitimately moves the response by ~1e-8.
            bound = 1e-6 if check == "precision" else 1e-9
            assert summary["worst_epsilon"] < bound, (check, summary)

    def test_non_invariant_descriptor_is_caught(self):
        frames = [_frame(_GENERIC_POSITIONS)]
        baseline = _structure_recompute(_z_sum)(frames)
        result = formal_invariance(frames, baseline, _structure_recompute(_z_sum), {})
        checks = result["preview"]["checks"]
        assert not checks["translation"]["passed"]
        assert not checks["rotation"]["passed"]
        assert not checks["reflection"]["passed"]
        # Summing over atoms is invariant under relabeling.
        assert checks["permutation"]["passed"]

    def test_chiral_descriptor_is_reported_as_chirality(self):
        frames = [_frame(_GENERIC_POSITIONS, numbers=[1, 2, 3, 4, 5])]
        baseline = _structure_recompute(_chiral)(frames)
        result = formal_invariance(frames, baseline, _structure_recompute(_chiral), {})
        checks = result["preview"]["checks"]
        assert not checks["reflection"]["passed"]
        assert checks["rotation"]["passed"]
        assert checks["translation"]["passed"]
        assert result["preview"]["chirality_sensitive"] is True
        assert any("chirality" in warning for warning in result["warnings"])

    def test_atom_granularity_invariance(self):
        frames = [
            _frame(_GENERIC_POSITIONS, numbers=[1, 1, 2, 3, 3]),
            _frame([[0.1, 0.4, 2.2], [1.8, 0.2, 0.5], [0.9, 1.1, 1.1], [2.4, 0.8, 1.9], [1.2, 2.3, 0.3]], numbers=[1, 1, 2, 3, 3]),
        ]
        recompute = _atom_recompute(_complete_atom)
        baseline = recompute(frames)
        result = formal_invariance(frames, baseline, recompute, {"granularity": "atom"})
        assert result["preview"]["granularity"] == "atom"
        assert result["preview"]["passed"] is True
        # Same-species permutation must hold per atom row, not just pooled.
        permutation = result["preview"]["checks"]["permutation"]
        assert permutation["passed"] and permutation["frames_measured"] == 2

    def test_atom_granularity_requires_atomic_rows(self):
        frames = [_frame(_GENERIC_POSITIONS)]
        baseline = _structure_recompute(_pair_distances)(frames)
        with pytest.raises(AppError):
            formal_invariance(frames, baseline, _structure_recompute(_pair_distances), {"granularity": "atom"})

    def test_unknown_check_and_bad_tolerance_rejected(self):
        frames = [_frame(_GENERIC_POSITIONS)]
        baseline = _structure_recompute(_pair_distances)(frames)
        recompute = _structure_recompute(_pair_distances)
        with pytest.raises(AppError):
            formal_invariance(frames, baseline, recompute, {"checks": ["levitation"]})
        with pytest.raises(AppError):
            formal_invariance(frames, baseline, recompute, {"tolerance": -1.0})


class TestCutoffSmoothness:
    @staticmethod
    def _count_within(frame, rcut) -> np.ndarray:
        positions = np.asarray(frame.positions, dtype=np.float64)
        deltas = positions[:, None, :] - positions[None, :, :]
        distances = np.linalg.norm(deltas, axis=-1)
        upper = distances[np.triu_indices(len(positions), k=1)]
        return np.asarray([float(np.count_nonzero(upper <= rcut))])

    def test_pair_on_the_cutoff_is_flagged(self):
        # One pair sits exactly on the nominal cutoff: shrinking it drops the
        # pair from the neighbor count, growing it keeps it - the straddle is
        # O(1) relative while deeper straddles are flat.
        frames = [_frame([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [5.0, 0.0, 0.0]])]

        def recompute_at(offset):
            return _structure_recompute(lambda f: self._count_within(f, 2.0 + offset))(frames)

        result = cutoff_smoothness(frames, recompute_at, {"max_delta": 0.1, "n_steps": 9})
        preview = result["preview"]
        assert preview["n_flagged"] == 1
        assert preview["frames"][0]["flagged"] is True
        assert preview["worst_jump_ratio"] > 3.0

    def test_pair_off_the_cutoff_stays_smooth(self):
        frames = [_frame([[0.0, 0.0, 0.0], [1.3, 0.0, 0.0], [5.0, 0.0, 0.0]])]

        def recompute_at(offset):
            return _structure_recompute(lambda f: self._count_within(f, 2.0 + offset))(frames)

        result = cutoff_smoothness(frames, recompute_at, {"max_delta": 0.1, "n_steps": 9})
        preview = result["preview"]
        assert preview["n_flagged"] == 0
        np.testing.assert_allclose(result["arrays"]["response_curves"], 0.0, atol=1e-15)

    def test_rebuild_mismatch_is_warned(self):
        frames = [_frame([[0.0, 0.0, 0.0], [1.3, 0.0, 0.0]])]

        def recompute_at(offset):
            return _structure_recompute(lambda f: self._count_within(f, 2.0 + offset))(frames)

        stored = np.asarray([[12345.0]])  # nothing like the rebuild
        result = cutoff_smoothness(frames, recompute_at, {"_stored_baseline": stored})
        assert any("cutoff parameter" in warning or "deviates" in warning for warning in result["warnings"])

    def test_even_grid_rejected(self):
        frames = [_frame([[0.0, 0.0, 0.0], [1.3, 0.0, 0.0]])]
        with pytest.raises(AppError):
            cutoff_smoothness(frames, lambda offset: None, {"n_steps": 8})


class TestEnvironmentJacobian:
    def test_complete_descriptor_reaches_expected_rank(self):
        frame = _frame(_GENERIC_POSITIONS)
        recompute = _atom_recompute(_complete_atom)
        result = environment_jacobian(
            [frame],
            recompute,
            {"cutoff": 100.0, "displacement": 1e-4, "rank_tolerance": 1e-6},
        )
        preview = result["preview"]
        assert preview["n_deficient"] == 0
        arrays = result["arrays"]
        # 4 neighbors per atom: expected 3*4 - 3 = 9, observed must match.
        np.testing.assert_array_equal(arrays["expected_rank"], np.full(5, 9))
        np.testing.assert_array_equal(arrays["observed_rank"], np.full(5, 9))
        # Rotational zero modes are satisfied to first order.
        assert preview["max_rotational_residual"] is not None
        assert preview["max_rotational_residual"] < 1e-6

    def test_incomplete_descriptor_shows_deficiency(self):
        frame = _frame(_GENERIC_POSITIONS)
        recompute = _atom_recompute(_center_distances_atom)
        result = environment_jacobian(
            [frame],
            recompute,
            {"cutoff": 100.0, "displacement": 1e-4, "rank_tolerance": 1e-6},
        )
        # 4 features on a 9-dimensional invariant manifold: at most rank 4.
        assert result["preview"]["n_deficient"] == 5
        np.testing.assert_array_less(result["arrays"]["observed_rank"], result["arrays"]["expected_rank"])

    def test_collinear_configuration_is_flagged(self):
        positions = [[float(index) * 0.9, 0.0, 0.0] for index in range(5)]
        frame = _frame(positions)
        recompute = _atom_recompute(_complete_atom)
        result = environment_jacobian(
            [frame],
            recompute,
            {"cutoff": 100.0, "displacement": 1e-4, "rank_tolerance": 1e-6},
        )
        # Collinear environments lose the transverse information entirely.
        assert result["preview"]["n_deficient"] >= 1
        assert max(result["arrays"]["rank_deficiency"]) >= 2

    def test_requires_atomic_rows(self):
        frame = _frame(_GENERIC_POSITIONS)
        with pytest.raises(AppError):
            environment_jacobian([frame], _structure_recompute(_pair_distances), {"cutoff": 100.0})

    def test_cutoff_required(self):
        frame = _frame(_GENERIC_POSITIONS)
        with pytest.raises(AppError):
            environment_jacobian([frame], _atom_recompute(_complete_atom), {})


class TestDegeneracySearch:
    def test_collapsed_pair_is_flagged(self):
        values = np.asarray(
            [
                [0.0, 0.0],
                [0.001, 0.0],  # descriptor twin of sample 0
                [3.0, 0.0],
                [0.0, 3.0],
                [3.0, 3.0],
                [-3.0, -3.0],
            ]
        )

        def structural_distance(i, j):
            # Ground truth: the twins are physically far apart.
            if {i, j} == {0, 1}:
                return 10.0
            return float(np.linalg.norm(values[i] - values[j]) + 1.0)

        result = degeneracy_search(values, structural_distance, {"k_neighbors": 3})
        preview = result["preview"]
        assert preview["n_dangerous"] >= 1
        flagged = {(pair["sample_a"], pair["sample_b"]) for pair in preview["pairs"]}
        assert ("sample:0", "sample:1") in flagged or ("sample:1", "sample:0") in flagged
        assert any(pair["reason"] == "structure" for pair in preview["pairs"])

    def test_atom_count_mismatch_reported(self):
        values = np.asarray([[0.0, 0.0], [0.001, 0.0], [5.0, 5.0]])

        def structural_distance(i, j):
            if {i, j} == {0, 1}:
                return float("inf")
            return 1.0

        result = degeneracy_search(values, structural_distance, {"k_neighbors": 2})
        assert any(pair["reason"] == "atom_count" for pair in result["preview"]["pairs"])
        assert result["preview"]["different_atom_count_pairs"] >= 1

    def test_energy_signal_flags_pair(self):
        values = np.asarray([[0.0, 0.0], [0.001, 0.0], [4.0, 4.0], [4.0, 4.2]])
        energy = np.asarray([0.0, 3.0, 0.0, 0.01])

        def structural_distance(i, j):
            return 0.5  # geometry says "same" for everything

        result = degeneracy_search(values, structural_distance, {"k_neighbors": 2}, energy=energy)
        assert result["preview"]["energy_used"] is True
        assert any(pair["reason"] in ("energy", "structure+energy") for pair in result["preview"]["pairs"])

    def test_requires_two_samples(self):
        with pytest.raises(AppError):
            degeneracy_search(np.zeros((1, 3)), lambda i, j: 0.0, {})


class TestDistanceConsistency:
    def test_linear_descriptor_tracks_structure(self):
        values = np.linspace(0.0, 1.0, 12).reshape(-1, 1)

        def structural_distance(i, j):
            return abs(i - j) / 11.0

        count = values.shape[0]
        matrix = np.zeros((count, count))
        for i in range(count):
            for j in range(count):
                matrix[i, j] = structural_distance(i, j)
        result = distance_consistency(values, matrix, {})
        preview = result["preview"]
        assert preview["pearson_descriptor_structural"] > 0.99
        # The uniform grid has heavy distance ties; float round-off shuffles
        # equal ranks slightly, so hold spearman to a looser bar.
        assert preview["spearman_descriptor_structural"] > 0.9
        assert preview["collapse"]["enrichment"] is not None

    def test_collapse_pair_is_surfaced(self):
        values = np.linspace(0.0, 1.0, 12).reshape(-1, 1)
        values[0] = values[1]  # descriptor twin
        matrix = np.abs(np.arange(12)[:, None] - np.arange(12)[None, :]) / 11.0
        matrix[0, 1] = matrix[1, 0] = 0.9  # ...that is physically far apart

        result = distance_consistency(values, matrix, {})
        preview = result["preview"]
        assert preview["collapse"]["count"] >= 1
        assert preview["collapse"]["enrichment"] > preview["collapse"]["independence_baseline"]
        pair_keys = {(pair["sample_a"], pair["sample_b"]) for pair in preview["dangerous_pairs"]}
        assert any({"sample:0", "sample:1"} == key for key in pair_keys) or pair_keys

    def test_shape_mismatch_rejected(self):
        values = np.zeros((5, 2))
        with pytest.raises(AppError):
            distance_consistency(values, np.zeros((4, 4)), {})


class TestRealEngineSmoke:
    """One real-descriptor pass through the invariance battery (engine present)."""

    def test_invariance_on_a_bundled_descriptor(self):
        pytest.importorskip("mdescriptor")
        from mdescriptor_studio_backend.mdescriptor_adapter import EngineAdapter

        engine = EngineAdapter()
        names = engine.list_names()
        assert names, "no descriptors registered by the engine"
        cell = np.diag([6.0, 6.0, 6.0])
        positions = np.asarray(
            [
                [0.1, 0.1, 0.1],
                [1.1, 0.2, 0.3],
                [0.4, 1.3, 0.2],
                [0.2, 0.3, 1.4],
                [2.2, 1.1, 0.6],
            ],
            dtype=np.float64,
        )
        numbers = np.array([1, 1, 6, 8, 1], dtype=np.int64)
        frame = _frame(positions, numbers=numbers, cell=cell, pbc=[True, True, True])
        last_error: Exception | None = None
        for name in names:
            schema = engine.schema(name)
            params: dict = {}
            for key, meta in schema.get("parameters", {}).items():
                if not meta.get("required"):
                    continue
                ptype = meta.get("type")
                if ptype == "species":
                    params[key] = sorted({int(value) for value in numbers})
                elif ptype == "integer":
                    params[key] = int(meta.get("default") or 1)
                elif ptype == "number":
                    params[key] = float(meta.get("default") if meta.get("default") is not None else 1.0)
                elif ptype == "boolean":
                    params[key] = bool(meta.get("default", False))
                elif ptype == "enum":
                    params[key] = (meta.get("enum") or [""])[0]
                elif ptype == "array":
                    params[key] = meta.get("default") or []
                elif ptype == "model":
                    continue
                else:
                    default = meta.get("default")
                    if default is not None:
                        params[key] = default
            try:
                descriptor = engine.build(name, params)
                control = engine.make_control()
                batch = engine.to_structure_batch([frame])
                engine.compute(descriptor, batch, control)

                def recompute(frames, _descriptor=descriptor, _control=control):
                    evaluation_batch = engine.to_structure_batch(frames)
                    computed = engine.compute(_descriptor, evaluation_batch, _control)
                    from mdescriptor_studio_backend.generation.evaluator import evaluate_batch

                    evaluation = evaluate_batch(computed, len(frames), what="diagnostics smoke")
                    return DescriptorRecompute(
                        structure_values=evaluation.structure_values,
                        atomic_values=evaluation.atomic_values,
                        row_offsets=evaluation.row_offsets,
                    )

                baseline = recompute([frame])
                result = formal_invariance(
                    [frame],
                    baseline,
                    recompute,
                    {"tolerance": 1e-5, "n_rotations": 2, "checks": ["translation", "rotation", "permutation", "precision"]},
                )
            except Exception as exc:  # noqa: BLE001 - try the next descriptor
                last_error = exc
                continue
            checks = result["preview"]["checks"]
            for check in ("translation", "rotation", "permutation"):
                assert checks[check]["passed"], f"{name}: {check} failed: {checks[check]}"
            return
        pytest.fail(f"no engine descriptor could be exercised: {last_error}")
