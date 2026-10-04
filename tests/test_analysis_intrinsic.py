"""Statistical diagnostics V2: TwoNN intrinsic dimension and information imbalance.

Synthetic-property tests (the review §17 style): the estimators must recover
known answers on constructed data -

    uniform cloud in d dims          -> TwoNN d_hat ~ d
    exact-duplicate rows             -> excluded, not propagated
    identical spaces                 -> imbalance 0 in both directions
    independent spaces               -> imbalance ~ 0.5
    nested space (b = a + extra)     -> both directions small, asymmetric flavor
    spectral rename                  -> same participation ratio, new kind
"""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis.algorithms.intrinsic import two_nn_intrinsic_dimension
from mdescriptor_studio_backend.analysis.algorithms.pairs import information_imbalance
from mdescriptor_studio_backend.analysis.metrics import effective_dimension as effective_dimension_algo
from mdescriptor_studio_backend.analysis.models import StructureDescriptorMatrix
from mdescriptor_studio_backend.errors import AppError


def _matrix(values) -> StructureDescriptorMatrix:
    values = np.asarray(values, dtype=np.float64)
    return StructureDescriptorMatrix(
        values=values,
        frame=np.arange(values.shape[0], dtype=np.int64),
        sample_ids=[f"frame:{i}" for i in range(values.shape[0])],
    )


class TestTwoNN:
    @pytest.mark.parametrize("dim", [1, 3, 8])
    def test_recovers_uniform_cloud_dimension(self, dim):
        rng = np.random.default_rng(11)
        n = 4000
        points = rng.uniform(0.0, 1.0, size=(n, dim))
        result = two_nn_intrinsic_dimension(_matrix(points), {"n_bootstrap": 16})
        estimate = result["preview"]["intrinsic_dimension"]
        assert estimate == pytest.approx(dim, rel=0.15), (dim, estimate)
        bootstrap = result["preview"]["bootstrap"]
        assert bootstrap["draws"] == 16
        assert np.isfinite(bootstrap["ci95"]).all()
        assert bootstrap["ci95"][0] < dim * 1.3 and bootstrap["ci95"][1] > dim * 0.7

    def test_duplicates_are_excluded_not_propagated(self):
        rng = np.random.default_rng(5)
        points = rng.uniform(0.0, 1.0, size=(500, 3))
        points[:20] = points[0]  # exact copies of one row
        result = two_nn_intrinsic_dimension(_matrix(points), {"n_bootstrap": 0})
        assert result["preview"]["duplicates_excluded"] == 20
        assert result["preview"]["points_used"] == 480
        assert result["preview"]["intrinsic_dimension"] == pytest.approx(3, rel=0.25)
        assert any("duplicate" in warning for warning in result["warnings"])

    def test_degenerate_all_equal_rows_rejected(self):
        points = np.zeros((20, 4))
        with pytest.raises(AppError):
            two_nn_intrinsic_dimension(_matrix(points), {})

    def test_needs_four_rows(self):
        with pytest.raises(AppError):
            two_nn_intrinsic_dimension(_matrix(np.random.default_rng(1).normal(size=(3, 5))), {})

    def test_preprocess_is_part_of_the_space(self):
        rng = np.random.default_rng(3)
        # Strongly anisotropic cloud: raw metric sees the long axis (d~1),
        # standardized rescales each axis (d~3).
        base = rng.normal(size=(3000, 3))
        anisotropic = base * np.array([1.0, 1.0, 1.0]) * np.array([50.0, 1.0, 1.0])
        raw = two_nn_intrinsic_dimension(_matrix(anisotropic), {"preprocess": "raw", "n_bootstrap": 0})
        std = two_nn_intrinsic_dimension(_matrix(anisotropic), {"preprocess": "standardized", "n_bootstrap": 0})
        assert raw["preview"]["intrinsic_dimension"] < std["preview"]["intrinsic_dimension"]

    def test_max_samples_bounds_the_neighbor_search(self):
        rng = np.random.default_rng(7)
        result = two_nn_intrinsic_dimension(_matrix(rng.normal(size=(3000, 4))), {"max_samples": 800, "n_bootstrap": 0})
        assert result["preview"]["points_total"] == 800
        assert any("sampled 800 of 3000" in warning for warning in result["warnings"])


class TestInformationImbalance:
    def test_identical_spaces_have_zero_imbalance(self):
        rng = np.random.default_rng(1)
        values = rng.normal(size=(300, 6))
        result = information_imbalance(_matrix(values), _matrix(values.copy()), {})
        assert result["preview"]["delta_a_to_b"] == pytest.approx(0.0, abs=1e-12)
        assert result["preview"]["delta_b_to_a"] == pytest.approx(0.0, abs=1e-12)
        assert result["preview"]["mean_overlap"][0] == pytest.approx(1.0)

    def test_independent_spaces_sit_near_half(self):
        rng = np.random.default_rng(2)
        a = rng.normal(size=(400, 6))
        b = rng.normal(size=(400, 6))
        result = information_imbalance(_matrix(a), _matrix(b), {})
        assert result["preview"]["delta_a_to_b"] == pytest.approx(0.5, abs=0.08)
        assert result["preview"]["delta_b_to_a"] == pytest.approx(0.5, abs=0.08)

    def test_nested_space_preserves_the_coarse_side(self):
        # b = a + two extra well-scaled coordinates: b contains all of a's
        # geometry, so a's neighbor choices stay near the top of b's ranking,
        # while b's extra dimensions perturb a's view of b's choices more.
        rng = np.random.default_rng(4)
        a = rng.normal(size=(400, 4))
        extra = rng.normal(size=(400, 2)) * 3.0
        b = np.hstack([a, extra])
        result = information_imbalance(_matrix(a), _matrix(b), {})
        assert result["preview"]["delta_a_to_b"] < 0.15
        # Both directions stay far below the 0.5 independence level: b's
        # neighbor choice (driven by the extra dimensions) is still a strong
        # predictor of a's ranking because the |Delta a| term tie-breaks
        # inside the extra-dimension annulus - measured, not assumed.
        assert result["preview"]["delta_b_to_a"] < 0.15
        assert result["preview"]["delta_b_to_a"] < result["preview"]["delta_a_to_b"]
        assert result["preview"]["mean_overlap"][-1] > result["preview"]["mean_overlap"][0] * 0.5

    def test_requires_aligned_sample_ids(self):
        rng = np.random.default_rng(6)
        left = _matrix(rng.normal(size=(50, 4)))
        right = _matrix(rng.normal(size=(40, 4)))
        with pytest.raises(AppError):
            information_imbalance(left, right, {})

    def test_per_point_contributions_are_bounded(self):
        rng = np.random.default_rng(8)
        result = information_imbalance(_matrix(rng.normal(size=(60, 3))), _matrix(rng.normal(size=(60, 3))), {})
        for name in ("contribution_a_to_b", "contribution_b_to_a"):
            values = result["arrays"][name]
            assert values.min() >= 0.0 and values.max() <= 1.0


class TestSpectralRename:
    def test_new_kind_same_number(self):
        rng = np.random.default_rng(9)
        values = rng.normal(size=(200, 10))
        legacy = effective_dimension_algo(_matrix(values), {}, kind="effective_dimension")
        spectral = effective_dimension_algo(_matrix(values), {}, kind="spectral_effective_dimension")
        assert legacy["preview"]["kind"] == "effective_dimension"
        assert spectral["preview"]["kind"] == "spectral_effective_dimension"
        assert spectral["preview"]["participation_ratio"] == pytest.approx(legacy["preview"]["participation_ratio"])
        np.testing.assert_array_equal(spectral["arrays"]["explained_variance"], legacy["arrays"]["explained_variance"])
