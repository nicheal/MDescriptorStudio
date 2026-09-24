"""Scientific behavior tests for the generic FPS sampling core.

Covers the canonical orderings (center initialization, endpoint-first
expansion), order independence, warm start, the ``min_distance`` stop, coverage
statistics, and every degenerate input the sampling module promises to reject
or handle deterministically.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis import StructureDescriptorMatrix
from mdescriptor_studio_backend.analysis.sampling import (
    FPSResult,
    FeatureBlock,
    GroupedFPSResult,
    apply_scaling,
    combine_feature_blocks,
    coverage_statistics,
    farthest_point_sampling,
    fit_scaling,
    grouped_farthest_point_sampling,
    sqrt_quota,
)
from mdescriptor_studio_backend.analysis.sampling.engine import sampling
from mdescriptor_studio_backend.generation._distance import sqdist_to_point as _sqdist_to_point
from mdescriptor_studio_backend.errors import ANALYSIS_INPUT_INVALID, AppError


def test_center_initialization_starts_in_the_middle_and_reaches_endpoints() -> None:
    x = np.array([[0.0], [1.0], [2.0], [3.0], [4.0]])
    result = farthest_point_sampling(x, n_samples=5)
    assert isinstance(result, FPSResult)
    # The structure closest to the centroid (2.0) seeds the set, then the two
    # endpoints are the farthest remaining candidates.
    assert result.indices[0] == 2
    assert set(result.indices[1:3].tolist()) == {0, 4}
    assert result.selection_distances[0] == 0.0
    assert result.selection_distances[1] == pytest.approx(2.0)
    # Selection distances are non-increasing after the seed.
    assert np.all(np.diff(result.selection_distances[1:]) <= 1e-12)
    assert result.n_selected == 5
    assert result.stopped_by == "target"


def test_two_dimensional_corners_and_radius_curve() -> None:
    x = np.array([[0.0, 0.0], [0.0, 1.0], [1.0, 0.0], [1.0, 1.0]])
    result = farthest_point_sampling(x, n_samples=4)
    assert result.indices[0] == 0  # all corners tie as "center"; first wins
    assert set(result.indices.tolist()) == {0, 1, 2, 3}
    # The covering radius shrinks (weakly) with every selection.
    assert np.all(np.diff(result.coverage_radius_curve) <= 1e-12)
    assert result.coverage_radius_curve[-1] == 0.0
    assert result.coverage_radius == 0.0
    assert result.nearest_distances.shape == (4,)
    assert result.coverage_radius == pytest.approx(result.nearest_distances.max())


def test_order_independence_of_centered_fps() -> None:
    rng = np.random.default_rng(11)
    # Well-separated clusters: selection is tie-free, so the same physical
    # structures must be chosen regardless of input order.
    clusters = np.repeat(np.arange(6, dtype=np.float64).reshape(-1, 1) * 100.0, 8, axis=0)
    x = clusters + rng.normal(size=clusters.shape) * 0.1

    base = farthest_point_sampling(x, n_samples=10)
    order = rng.permutation(x.shape[0])
    shuffled = farthest_point_sampling(x[order], n_samples=10)
    assert set(base.indices.tolist()) == set(order[shuffled.indices].tolist())


def test_permutation_keeps_selection_mostly_stable_on_noisy_data() -> None:
    rng = np.random.default_rng(5)
    x = rng.uniform(-1.0, 1.0, size=(300, 4))
    base = set(farthest_point_sampling(x, n_samples=40).indices.tolist())
    for perm in (rng.permutation(300), rng.permutation(300)):
        picked = farthest_point_sampling(x[perm], n_samples=40).indices
        overlap = len(base & set(perm[picked].tolist()))
        assert overlap >= 0.9 * 40


def test_warm_start_targets_the_existing_coverage_gap() -> None:
    candidates = np.array([[0.0], [0.1], [0.9], [5.0], [5.1]])
    existing = np.array([[0.05], [0.95]])
    result = farthest_point_sampling(candidates, n_samples=2, selected_features=existing)
    # The region around 5.0 is the one the existing set knows nothing about.
    assert result.indices[0] == 4
    assert result.selection_distances[0] == pytest.approx(4.15)
    assert result.stopped_by == "target"


def test_warm_start_dimension_mismatch_raises() -> None:
    x = np.zeros((4, 3))
    with pytest.raises(ValueError, match="dimension"):
        farthest_point_sampling(x, n_samples=1, selected_features=np.zeros((3, 2)))


def test_min_distance_stops_before_adding_no_new_information() -> None:
    x = np.linspace(0.0, 1.0, 101).reshape(-1, 1)
    result = farthest_point_sampling(x, n_samples=100, min_distance=0.3)
    # Center 0.5, then both endpoints at distance 0.5; the next candidate
    # (0.25/0.75) sits at 0.25 < 0.3, so the run stops with three.
    assert result.n_selected == 3
    assert result.stopped_by == "min_distance"
    assert set(result.indices.tolist()) == {0, 50, 100}


def test_requested_more_samples_than_candidates_is_exhausted() -> None:
    x = np.arange(4, dtype=np.float64).reshape(-1, 1)
    result = farthest_point_sampling(x, n_samples=100)
    assert result.n_selected == 4
    assert result.stopped_by == "exhausted"


def test_duplicate_points_remain_selectable_under_strict_count() -> None:
    x = np.array([[0.0], [0.0], [1.0], [1.0]])
    result = farthest_point_sampling(x, n_samples=4)
    assert result.n_selected == 4
    assert result.stopped_by == "target"
    # The first two picks are the two distinct locations.
    assert set(result.indices[:2].tolist()) == {0, 2}


def test_constant_descriptor_selects_deterministically() -> None:
    x = np.zeros((5, 3))
    result = farthest_point_sampling(x, n_samples=2)
    assert result.n_selected == 2
    assert result.coverage_radius == 0.0
    assert np.all(result.selection_distances == 0.0)


@pytest.mark.parametrize("bad", [np.empty((0, 2)), np.empty((3, 0))])
def test_empty_matrix_rejected(bad: np.ndarray) -> None:
    with pytest.raises(ValueError, match="2D matrix"):
        farthest_point_sampling(bad, n_samples=1)


def test_invalid_sample_counts_rejected() -> None:
    x = np.ones((3, 1))
    with pytest.raises(ValueError, match="n_samples"):
        farthest_point_sampling(x, n_samples=0)
    with pytest.raises(ValueError, match="n_samples"):
        farthest_point_sampling(x, n_samples=1.5)


@pytest.mark.parametrize("bad", [[[np.nan], [1.0]], [[1.0], [np.inf]]])
def test_nonfinite_values_rejected(bad: list[list[float]]) -> None:
    with pytest.raises(ValueError, match="NaN or Inf"):
        farthest_point_sampling(np.array(bad), n_samples=1)


def test_invalid_initial_rejected() -> None:
    x = np.ones((3, 1))
    with pytest.raises(ValueError, match="initial"):
        farthest_point_sampling(x, n_samples=1, initial="chebyshev")
    with pytest.raises(ValueError, match="range"):
        farthest_point_sampling(x, n_samples=1, initial=3)
    assert farthest_point_sampling(x, n_samples=1, initial=1).indices[0] == 1


def test_negative_min_distance_rejected() -> None:
    with pytest.raises(ValueError, match="min_distance"):
        farthest_point_sampling(np.ones((3, 1)), n_samples=1, min_distance=-1.0)


def test_float32_input_matches_float64_selections() -> None:
    rng = np.random.default_rng(3)
    x64 = rng.normal(size=(200, 6))
    a = farthest_point_sampling(x64, n_samples=25)
    b = farthest_point_sampling(x64.astype(np.float32), n_samples=25)
    assert np.array_equal(a.indices, b.indices)
    assert np.allclose(a.selection_distances, b.selection_distances, rtol=1e-5)


def test_matches_brute_force_reference() -> None:
    rng = np.random.default_rng(9)
    x = rng.normal(size=(60, 3))
    result = farthest_point_sampling(x, n_samples=12, initial="first")

    # Naive O(N²K) reference with explicit true distances.
    d = np.linalg.norm(x - x[0], axis=1)
    chosen = [0]
    for _ in range(11):
        d = np.minimum(d, np.linalg.norm(x - x[chosen[-1]], axis=1))
        d[chosen] = -1.0
        chosen.append(int(np.argmax(d)))
    assert result.indices.tolist() == chosen
    residual = np.min(np.stack([np.linalg.norm(x - x[c], axis=1) for c in chosen]), axis=0)
    assert np.allclose(result.nearest_distances, residual)
    assert result.coverage_radius == pytest.approx(float(residual.max()))


def test_scaling_robust_uses_median_and_iqr() -> None:
    x = np.arange(5, dtype=np.float64).reshape(-1, 1)
    scaling, warnings = fit_scaling(x, "robust")
    assert not warnings
    assert scaling.center[0] == pytest.approx(2.0)
    assert scaling.scale[0] == pytest.approx(2.0 / 1.349)
    scaled = apply_scaling(scaling, x)
    assert scaled.ravel()[2] == pytest.approx(0.0)

    standardized, _ = fit_scaling(x, "standardized")
    assert standardized.center[0] == pytest.approx(2.0)
    assert standardized.scale[0] == pytest.approx(np.std(x))

    constant, warnings = fit_scaling(np.zeros((4, 2)), "robust")
    assert len(warnings) == 1
    assert np.all(constant.scale == 1.0)


def test_coverage_statistics_quantiles() -> None:
    stats = coverage_statistics(np.array([0.0, 1.0, 2.0, 3.0, 4.0]))
    assert stats["mean"] == pytest.approx(2.0)
    assert stats["p50"] == pytest.approx(2.0)
    assert stats["p95"] == pytest.approx(3.8)  # linear interpolation at q=0.95
    assert stats["max"] == pytest.approx(4.0)


def test_engine_fps_reports_center_init_and_min_distance() -> None:
    x = np.arange(5, dtype=np.float64).reshape(-1, 1)
    samples = StructureDescriptorMatrix(x, np.arange(5))
    result = sampling(samples, {"n_samples": 5, "scaling": "raw"}, "fps")
    preview = result["preview"]
    assert preview["algorithm"] == "fps"
    assert preview["initialization"] == "center"
    # Selection order is 2 → 0 → 4 → 1 → 3, entering at 0, 2, 2, 1, 1. Both
    # arrays are stored sorted by sample and the distance travels with its own
    # sample, so the pair reads: sample 0 joined at 2.0, 1 at 1.0, 2 at 0.0
    # (it was the seed), 3 at 1.0, 4 at 2.0 (deep review pass 5, 5-C8 - before
    # that only `selected_indices` was sorted and the two .npy columns did not
    # line up for anyone joining them by row).
    assert result["arrays"]["selected_indices"].tolist() == [0, 1, 2, 3, 4]
    assert result["arrays"]["selection_distances"].tolist() == pytest.approx([2.0, 1.0, 0.0, 1.0, 2.0])
    assert result["arrays"]["coverage_radius_curve"].tolist() == pytest.approx([2.0, 2.0, 1.0, 1.0, 0.0])

    stopped = sampling(samples, {"n_samples": 5, "min_distance": 1.5, "scaling": "raw"}, "fps")
    assert stopped["preview"]["selected_count"] == 3
    assert stopped["preview"]["stop_reason"] == "min_distance"

    with pytest.raises(AppError) as exc:
        sampling(samples, {"scaling": "bogus"}, "fps")
    assert exc.value.code == ANALYSIS_INPUT_INVALID


def test_engine_fps_warm_start_passes_existing_set() -> None:
    candidates = StructureDescriptorMatrix(np.array([[0.0], [1.0], [2.0], [9.0]]), np.arange(4))
    existing = StructureDescriptorMatrix(np.array([[0.0], [2.0]]), np.array([0, 2]))
    result = sampling(
        candidates,
        {"n_samples": 2, "scaling": "raw", "initialization": "center"},
        "fps",
        existing=existing,
    )
    assert result["preview"]["warm_start"] is True
    # The candidate farthest from {0, 2} is 9.0, selected first at that distance -
    # and the distance still belongs to sample 3 now that both arrays are stored
    # in the same order.
    assert result["preview"]["warm_start"]
    assert 3 in result["arrays"]["selected_indices"].tolist()
    entered_at = dict(
        zip(result["arrays"]["selected_indices"].tolist(), result["arrays"]["selection_distances"].tolist())
    )
    assert entered_at[3] == pytest.approx(7.0)


def test_regression_fixed_matrix_golden_selection() -> None:
    """Golden-order regression: any change in selection order or algorithm
    semantics changes this hash.  Regenerate deliberately, never silently."""
    rng = np.random.default_rng(7)
    x = rng.normal(size=(256, 8)) * np.linspace(0.5, 2.0, 8)
    result = farthest_point_sampling(x, n_samples=32)
    digest = hashlib.sha256(np.sort(result.indices).tobytes()).hexdigest()
    assert digest == "62dd922bc0d7db60e0e7c108ac98684c8bb1307efafb256c822c19989890cf92"
    # And the coverage curve stays a non-increasing, non-negative witness.
    assert np.all(np.diff(result.coverage_radius_curve) <= 1e-12)


# ---------------------------------------------------------------------------
# V1.1: group labels, √N quota, and grouped FPS
# ---------------------------------------------------------------------------


def test_sqrt_quota_sums_exactly_and_covers_every_group() -> None:
    quota = sqrt_quota(np.array([850, 420, 170, 60]), 77)
    assert quota.sum() == 77
    assert np.all(quota >= 1)  # every non-empty group gets a representative
    # √N share, not a linear N share: the smallest group keeps a usable budget.
    assert quota[3] >= 8
    assert quota[0] / quota[3] < 850 / 60


def test_sqrt_quota_respects_capacity_and_small_targets() -> None:
    # A target below the group count cannot give everyone a sample.
    assert sqrt_quota(np.array([5, 3, 2]), 2).sum() == 2
    # Counts never exceed a group's size, and empty groups stay at zero.
    capped = sqrt_quota(np.array([100, 1]), 101)
    assert capped.tolist() == [100, 1]
    assert sqrt_quota(np.array([0, 7]), 4).tolist() == [0, 4]


def test_grouped_fps_allocates_by_sqrt_and_reports_groups() -> None:
    rng = np.random.default_rng(1)
    x = np.vstack([rng.normal(0, 0.1, size=(90, 2)), rng.normal(10, 0.1, size=(10, 2))])
    labels = np.array(["A"] * 90 + ["B"] * 10, dtype=object)
    result = grouped_farthest_point_sampling(x, labels, n_samples=10)
    assert isinstance(result, GroupedFPSResult)
    assert result.group_names == ["A", "B"]
    assert result.group_sizes.tolist() == [90, 10]
    assert result.group_quota.tolist() == [8, 2]
    # Both compositions are represented, with the minority not drowned out.
    picked = labels[result.indices]
    assert (picked == "B").sum() == 2
    # Coverage is global: every selected structure has residual ~0.
    assert result.nearest_distances[result.indices].max() == pytest.approx(0.0)
    assert np.all(np.diff(result.coverage_radius_curve) <= 1e-12)
    assert result.n_selected == 10
    assert result.stopped_by == "target"


def test_grouped_fps_warm_start_covers_every_group_against_the_existing_set() -> None:
    x = np.array([[0.0], [1.0], [10.0], [11.0]])
    labels = np.array(["A", "A", "B", "B"], dtype=object)
    existing = np.array([[0.0]])
    result = grouped_farthest_point_sampling(x, labels, n_samples=2, selected_features=existing)
    # Group A's farthest-from-existing member is 1.0; group B's is 11.0.
    assert result.selection_distances.tolist() == pytest.approx([1.0, 11.0])


def test_grouped_fps_truncates_a_quota_when_global_coverage_is_reached() -> None:
    values = np.linspace(0.0, 1.0, 100, dtype=np.float64).reshape(-1, 1)
    x = np.vstack([values, values])
    labels = np.array(["A"] * 100 + ["B"] * 100, dtype=object)
    result = grouped_farthest_point_sampling(x, labels, n_samples=20, target_coverage=0.95)

    # The first group's geometry already covers the duplicated global space;
    # once the target is reached, the remaining group quota must not be run.
    assert result.group_quota.tolist() == [10, 10]
    assert result.n_selected < int(result.group_quota.sum())
    assert result.stopped_by == "coverage"
    assert result.coverage_r2 >= 0.95


def test_grouped_fps_rejects_integer_initial_and_misaligned_labels() -> None:
    x = np.ones((4, 2))
    labels = np.array(["A", "A", "B", "B"], dtype=object)
    with pytest.raises(ValueError, match="grouped sampling"):
        grouped_farthest_point_sampling(x, labels, n_samples=2, initial=1)
    with pytest.raises(ValueError, match="group labels cover"):
        grouped_farthest_point_sampling(x, labels[:3], n_samples=2)


def test_engine_grouped_fps_reports_allocation_and_requires_group_labels() -> None:
    x = np.arange(12, dtype=np.float64).reshape(6, 2)
    samples = StructureDescriptorMatrix(x, np.arange(6))
    labels = np.array(["C", "C", "C", "C-Si", "C-Si", "O"], dtype=object)

    with pytest.raises(AppError) as exc:
        sampling(samples, {"n_samples": 3, "strategy": "grouped", "scaling": "raw"}, "fps")
    assert exc.value.code == ANALYSIS_INPUT_INVALID

    result = sampling(
        samples, {"n_samples": 6, "strategy": "grouped", "scaling": "raw"}, "fps", group_labels=labels
    )
    preview = result["preview"]
    assert preview["strategy"] == "grouped"
    assert [row["group"] for row in preview["allocation"]] == ["C", "C-Si", "O"]
    assert sum(row["quota"] for row in preview["allocation"]) == 6
    assert preview["selected_count"] == 6
    # A global run of the same data reports no allocation.
    global_preview = sampling(samples, {"n_samples": 3, "scaling": "raw"}, "fps")["preview"]
    assert "allocation" not in global_preview
    assert global_preview["strategy"] == "global"


def test_engine_rejects_unknown_strategy() -> None:
    samples = StructureDescriptorMatrix(np.ones((4, 2)), np.arange(4))
    with pytest.raises(AppError) as exc:
        sampling(samples, {"strategy": "clustered"}, "fps")
    assert exc.value.code == ANALYSIS_INPUT_INVALID


# ---------------------------------------------------------------------------
# V2: composite feature blocks and coverage-based automatic budget
# ---------------------------------------------------------------------------


def test_combine_blocks_normalizes_each_block_and_weights_by_inverse_sqrt_dimension() -> None:
    rng = np.random.default_rng(2)
    descriptor = rng.normal(0.0, 50.0, size=(60, 4))  # wide scale
    lattice = rng.normal(5.0, 0.001, size=(60, 2))  # narrow scale
    space, reference = combine_feature_blocks([FeatureBlock("descriptor", descriptor), FeatureBlock("lattice", lattice)])
    assert reference is None
    assert space.values.shape == (60, 6)
    assert [item["name"] for item in space.layout] == ["descriptor", "lattice"]
    assert [item["offset"] for item in space.layout] == [0, 4]
    # Robust scaling makes each block's spread O(1); 1/√D then equalizes the
    # per-block contribution to the squared distance.
    assert np.all(np.abs(space.values[:, :4].std(axis=0) - 1.0 / np.sqrt(4)) < 0.35)
    assert np.all(np.abs(space.values[:, 4:].std(axis=0) - 1.0 / np.sqrt(2)) < 0.35)


def test_combine_blocks_weight_scales_one_block_relative_to_another() -> None:
    x = np.arange(40, dtype=np.float64).reshape(20, 2)
    y = np.arange(20, dtype=np.float64).reshape(-1, 1)
    base, _ = combine_feature_blocks([FeatureBlock("a", x), FeatureBlock("b", y)])
    weighted, _ = combine_feature_blocks([FeatureBlock("a", x, weight=3.0), FeatureBlock("b", y)])
    # The weight is a pure multiplier on the block after normalisation.
    assert np.allclose(weighted.values[:, :2], base.values[:, :2] * 3.0)
    assert np.allclose(weighted.values[:, 2:], base.values[:, 2:])


def test_combine_blocks_fits_scaling_on_the_reference_set() -> None:
    candidate = np.array([[10.0], [20.0], [30.0]])
    existing = np.array([[0.0], [1.0], [2.0]])
    blocks = [FeatureBlock("a", candidate)]
    reference = [FeatureBlock("a", existing)]
    space, reference_space = combine_feature_blocks(blocks, reference=reference)
    assert reference_space is not None
    # Robust scaling fitted on the reference maps the reference median to 0, so
    # the candidate values land well away from it — comparable units, no rescale.
    assert reference_space.values.reshape(-1)[1] == pytest.approx(0.0)
    assert space.values.reshape(-1)[0] > 10.0


def test_combine_blocks_rejects_inconsistent_shapes_and_names() -> None:
    good = FeatureBlock("a", np.ones((4, 2)))
    with pytest.raises(ValueError, match="at least one feature block"):
        combine_feature_blocks([])
    with pytest.raises(ValueError, match="rows"):
        combine_feature_blocks([good, FeatureBlock("b", np.ones((3, 2)))])
    with pytest.raises(ValueError, match="non-empty name"):
        combine_feature_blocks([FeatureBlock("  ", np.ones((4, 2)))])
    with pytest.raises(ValueError, match="weight"):
        combine_feature_blocks([FeatureBlock("a", np.ones((4, 2)), weight=0.0)])
    with pytest.raises(ValueError, match="NaN or Inf"):
        combine_feature_blocks([FeatureBlock("a", np.array([[1.0], [np.nan]]))])
    with pytest.raises(ValueError, match="scaling"):
        combine_feature_blocks([FeatureBlock("a", np.ones((4, 2)), scaling="zscore")])
    with pytest.raises(ValueError, match="no feature block named"):
        combine_feature_blocks([good], reference=[FeatureBlock("b", np.ones((4, 2)))])
    with pytest.raises(ValueError, match="dimension"):
        combine_feature_blocks([good], reference=[FeatureBlock("a", np.ones((4, 3)))])


def test_target_coverage_stops_early_and_r2_rises_monotonically() -> None:
    x = np.linspace(0.0, 1.0, 201).reshape(-1, 1)
    result = farthest_point_sampling(x, n_samples=200, target_coverage=0.95)
    assert result.stopped_by == "coverage"
    assert result.n_selected < 50  # far below the requested maximum
    assert result.coverage_r2 >= 0.95
    assert np.all(np.diff(result.coverage_r2_curve) >= -1e-12)
    assert result.coverage_r2 == pytest.approx(result.coverage_r2_curve[-1])
    # Total spread is the R² denominator's numerator: Σ‖x−x̄‖².
    assert result.total_spread == pytest.approx(float(((x - x.mean()) ** 2).sum()))


def test_target_coverage_reports_coverage_when_the_last_allowed_pick_hits_it() -> None:
    x = np.linspace(0.0, 1.0, 201).reshape(-1, 1)
    result = farthest_point_sampling(x, n_samples=3, target_coverage=1e-9)
    assert result.n_selected == 2  # the check happens before the pick
    assert result.stopped_by == "coverage"


def test_target_coverage_already_satisfied_by_the_existing_set_selects_nothing() -> None:
    x = np.linspace(0.0, 1.0, 51).reshape(-1, 1)
    result = farthest_point_sampling(x, n_samples=10, selected_features=x, target_coverage=0.99)
    assert result.n_selected == 0
    assert result.indices.size == 0
    assert result.stopped_by == "coverage"
    assert result.coverage_r2_curve.size == 0


def test_target_coverage_rejects_out_of_range_values() -> None:
    x = np.ones((4, 1))
    for bad in (0.0, 1.0, -0.5, 1.5):
        with pytest.raises(ValueError, match="target_coverage"):
            farthest_point_sampling(x, n_samples=2, target_coverage=bad)


def test_constant_space_is_fully_covered_immediately() -> None:
    x = np.zeros((6, 3))
    result = farthest_point_sampling(x, n_samples=6, target_coverage=0.95)
    assert result.n_selected == 1
    assert result.stopped_by == "coverage"
    assert result.coverage_r2 == 1.0


def test_engine_composite_space_reports_block_layout() -> None:
    x = np.arange(40, dtype=np.float64).reshape(10, 4)
    samples = StructureDescriptorMatrix(x, np.arange(10))
    lattice = np.linspace(3.0, 9.0, 10).reshape(-1, 1)
    blocks = [FeatureBlock("descriptor", x), FeatureBlock("lattice", lattice, weight=2.0)]
    result = sampling(samples, {"n_samples": 5, "scaling": "robust"}, "fps", blocks=blocks)
    preview = result["preview"]
    assert preview["sampling_space"] == "composite"
    assert preview["sampling_dimension"] == 5
    assert [item["name"] for item in preview["blocks"]] == ["descriptor", "lattice"]
    assert preview["blocks"][1]["weight"] == pytest.approx(2.0)
    assert result["arrays"]["coverage_r2_curve"].size == 5
    # Without blocks the same run reports the plain descriptor space.
    plain = sampling(samples, {"n_samples": 5, "scaling": "robust"}, "fps")["preview"]
    assert plain["sampling_space"] == "descriptor"
    assert "blocks" not in plain


def test_engine_target_coverage_and_invalid_target() -> None:
    x = np.linspace(0.0, 1.0, 101).reshape(-1, 1)
    samples = StructureDescriptorMatrix(x, np.arange(101))
    result = sampling(samples, {"n_samples": 100, "scaling": "raw", "target_coverage": 0.95}, "fps")
    assert result["preview"]["stop_reason"] == "coverage"
    assert result["preview"]["selected_count"] < 100
    assert result["preview"]["target_coverage"] == pytest.approx(0.95)
    with pytest.raises(AppError) as exc:
        sampling(samples, {"scaling": "raw", "target_coverage": 1.0}, "fps")
    assert exc.value.code == ANALYSIS_INPUT_INVALID


def test_grouped_coverage_stop_needs_a_measurable_coverage() -> None:
    # A constant feature space has no spread to explain, so _r2_coverage answers
    # 1.0 for it — and with no warm-start set every residual is still +inf at
    # that moment. The pre-check stopped the run before its first pick and the
    # grouped result carried all-inf residuals, which the sampling preview then
    # could not encode as a protocol frame.
    x = np.zeros((4, 3))
    labels = np.array(["A", "A", "B", "B"])
    result = grouped_farthest_point_sampling(x, labels, n_samples=2, target_coverage=0.95)
    assert result.n_selected >= 1
    assert np.isfinite(result.nearest_distances).all()
    assert result.coverage_radius == pytest.approx(0.0)

    # The same stop is still legitimate when a warm start already covers the
    # space: the residuals are finite, so "add nothing" is the honest answer.
    existing = np.zeros((2, 3))
    warm = grouped_farthest_point_sampling(
        x, labels, n_samples=4, selected_features=existing, target_coverage=0.95
    )
    assert warm.n_selected == 0
    assert np.isfinite(warm.nearest_distances).all()


def test_engine_grouped_sampling_preview_encodes() -> None:
    import json

    from mdescriptor_studio_backend.protocol import frames

    x = np.arange(12, dtype=np.float64).reshape(6, 2)
    samples = StructureDescriptorMatrix(x, np.arange(6))
    result = sampling(
        samples,
        {"n_samples": 2, "strategy": "grouped", "scaling": "raw", "target_coverage": 0.9},
        "fps",
        group_labels=np.array(["si", "si", "si", "ge", "ge", "ge"]),
    )
    preview = result["preview"]
    assert preview["selected_count"] >= 1
    assert np.isfinite(preview["mean_residual"])
    # The preview is what crosses the wire: encode it for real, since the
    # protocol refuses (rather than stringifies) a non-finite number.
    assert json.loads(frames.encode(frames.response_ok(1, preview)))["result"] == preview


def test_fps_pick_order_survives_a_large_common_feature_offset() -> None:
    """Deep review P1-18: the sampler rebuilt its distances from
    |a|² + |b|² - 2ab, which spends the float64 mantissa on the shared
    magnitude and gives away the low-order bits that separate near-duplicate
    structures.  Farthest-point order is a property of the geometry, so moving
    the feature origin must not move a single pick."""
    rng = np.random.default_rng(11)
    tight = rng.normal(size=(400, 96)) * 1e-3 + 5.0
    offset = tight + 1e6

    assert farthest_point_sampling(tight, n_samples=12, initial="center").indices.tolist() == \
        farthest_point_sampling(offset, n_samples=12, initial="center").indices.tolist()

    # The identity really was the problem on this input: it puts a different row
    # first, while explicit differences agree with scipy to full precision.
    point = offset[0]
    direct = _sqdist_to_point(offset, point)
    expanded = np.maximum(
        np.einsum("ij,ij->i", offset, offset) - 2.0 * (offset @ point) + float(point @ point),
        0.0,
    )
    assert int(np.argmax(expanded)) != int(np.argmax(direct))
    assert np.median(np.abs(expanded - direct) / np.maximum(direct, 1e-30)) > 0.5


def test_grouped_fps_reports_progress_inside_a_group() -> None:
    """Progress is the runner's only cancellation point, and one group can be
    the whole run: reporting once per group made a large group unstoppable."""
    x = np.random.default_rng(2).normal(size=(60, 4))
    reports: list[float] = []

    grouped_farthest_point_sampling(
        x, np.array(["a"] * 30 + ["b"] * 30), n_samples=24, progress=lambda fraction, _message: reports.append(fraction)
    )

    assert len(reports) > 2  # one per group would be two
    assert reports == sorted(reports)  # a bar that goes backwards is broken
    assert reports[-1] == pytest.approx(1.0)


def _cluster_selection(x: np.ndarray, scaling: str) -> list[int]:
    params = {"n_samples": 3, "n_clusters": 3, "seed": 5, "scaling": scaling}
    result = sampling(StructureDescriptorMatrix(x, np.arange(x.shape[0])), params, "cluster_representative")
    return result["arrays"]["selected_indices"].tolist()


def _two_blob_matrix() -> np.ndarray:
    """Three tight groups in three comparable columns, the last of which a
    mixed-unit matrix would make enormous (energy in eV next to volume in Å³)."""
    rng = np.random.default_rng(11)
    blocks = [rng.normal(centre, 0.15, (10, 3)) for centre in ((0.0, 0.0, 0.0), (3.0, 1.0, 2.0), (1.0, 4.0, 0.5))]
    return np.vstack(blocks)


def test_cluster_representatives_ignore_a_column_unit() -> None:
    """Deep review B-5: FPS measured in a scaled space and cluster in the raw one,
    so the same matrix gave two sampling cards describing two geometries, and one
    wide column decided every representative on its own."""
    x = _two_blob_matrix()
    wide = x.copy()
    wide[:, 2] *= 1000.0

    raw, raw_wide = _cluster_selection(x, "raw"), _cluster_selection(wide, "raw")
    assert raw != raw_wide  # the wide column moved every pick it touches
    for mode in ("standardized", "robust"):
        assert _cluster_selection(x, mode) == _cluster_selection(wide, mode)
    # The default is a scaled mode, so this batch changes stored selections and
    # carries the studio-analysis-7 invalidation with it.
    assert _cluster_selection(x, "robust") != raw


def test_cluster_scaling_matches_an_already_scaled_matrix() -> None:
    """The engine scales the matrix the representatives are measured in, not just
    the one KMeans is fitted on: pre-scaling by hand must give the same answer."""
    x = _two_blob_matrix()
    scaled = (x - x.mean(axis=0)) / x.std(axis=0)
    assert _cluster_selection(x, "standardized") == _cluster_selection(scaled, "raw")


def test_cluster_representatives_are_permutation_invariant_and_default_to_target_clusters() -> None:
    x = _two_blob_matrix()
    base_samples = StructureDescriptorMatrix(x, np.arange(x.shape[0]))
    base = sampling(base_samples, {"n_samples": 9, "seed": 5}, "cluster_representative")
    order = np.random.default_rng(17).permutation(x.shape[0])
    shuffled = sampling(
        StructureDescriptorMatrix(x[order], order),
        {"n_samples": 9, "seed": 5},
        "cluster_representative",
    )

    assert base["preview"]["n_clusters"] == 9
    assert set(base["arrays"]["selected_indices"].tolist()) == set(order[shuffled["arrays"]["selected_indices"]].tolist())


def test_cluster_preview_names_the_space_it_used() -> None:
    """The card has to say which geometry it picked from, because the two
    distance-based algorithms no longer share the raw one."""
    x = _two_blob_matrix()
    samples = StructureDescriptorMatrix(x, np.arange(x.shape[0]))
    default = sampling(samples, {"n_samples": 3, "n_clusters": 3}, "cluster_representative")
    assert default["preview"]["scaling"] == "robust"
    with pytest.raises(AppError) as refused:
        sampling(samples, {"n_samples": 3, "scaling": "mixed"}, "cluster_representative")
    assert refused.value.code == ANALYSIS_INPUT_INVALID
    # A choice that never measures a distance has no space to report.
    assert "scaling" not in sampling(samples, {"n_samples": 3}, "random")["preview"]
