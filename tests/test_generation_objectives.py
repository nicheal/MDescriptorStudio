"""Analytic objective tests (design doc §40/§41).

§40 — structure novelty with archive + batch diversity:
    existing: (0,0) (0,1) (1,0) (1,1)
    A = (0.5,0.5)  B = (3,3)            → Novelty(B) > Novelty(A)
    B1 = (3,3)  B2 = (3.01,3.01)  C = (−2,−2)
    selecting two must take exactly one of {B1,B2} plus C — never B1+B2.

§41 — local-environment novelty:
    archive A = {0,1,2,3}
    X1 = {0.1,1.1,2.1,3.1}   X2 = {0.1,1.1,8.0,9.0}
    → top-20% and mean rank X2 far above X1.
    X3 = {0.1,1.1,2.1,1000}
    → max is dominated by the single freak atom; top-Q/quantile stay stable.
"""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis.sampling import fit_scaling
from mdescriptor_studio_backend.generation.archive import DescriptorArchive, LocalEnvironmentArchive
from mdescriptor_studio_backend.generation.engine import select_diverse_batch
from mdescriptor_studio_backend.generation.models import ArchiveEntry
from mdescriptor_studio_backend.generation.objectives import (
    CoverageGainObjective,
    LocalEnvironmentNoveltyObjective,
    NoveltyObjective,
)


def _structure_archive(reference, accepted=None):
    reference = np.asarray(reference, dtype=np.float64)
    scaling, _ = fit_scaling(reference, "raw")
    archive = DescriptorArchive(reference, scaling)
    if accepted is not None:
        accepted = np.atleast_2d(np.asarray(accepted, dtype=np.float64))
        archive.add(
            accepted,
            [ArchiveEntry(candidate_id=f"acc_{i}", structure_index=i, fitness=0.0, novelty=0.0, generation=0) for i in range(accepted.shape[0])],
        )
    return archive


def _score(objective, values, structure_archive, local_archive=None):
    values = np.atleast_2d(np.asarray(values, dtype=np.float64))
    return objective.evaluate_batch(
        values, None, None, structure_archive, local_archive, np.zeros(values.shape[0])
    )


class TestStructureNovelty:
    def test_far_candidate_beats_central_candidate(self):
        archive = _structure_archive([[0.0, 0.0], [0.0, 1.0], [1.0, 0.0], [1.0, 1.0]])
        scores = _score(NoveltyObjective(), [[0.5, 0.5], [3.0, 3.0]], archive)
        novelty = scores.novelty
        assert novelty[1] > novelty[0]
        assert novelty[1] == pytest.approx(np.sqrt(8.0), rel=1e-9)  # nearest is (1,1)

    def test_batch_selection_takes_two_regions_not_one(self):
        archive = _structure_archive([[0.0, 0.0], [0.0, 1.0], [1.0, 0.0], [1.0, 1.0]])
        values = np.array([[3.0, 3.0], [3.01, 3.01], [-2.0, -2.0]])
        scores = _score(NoveltyObjective(), values, archive)
        selected = select_diverse_batch(scores.fitness, values, budget=2)
        assert len(selected) == 2
        # C = (-2,-2) must be taken; the two near-identical far points must not
        # both be taken (FPS inside the novelty-elite pool, G3 selection).
        assert 2 in selected
        assert not ({0, 1} <= set(selected))

    def test_novelty_is_zero_on_the_reference_itself(self):
        reference = np.array([[0.0, 0.0], [0.0, 1.0], [1.0, 0.0], [1.0, 1.0]])
        archive = _structure_archive(reference)
        scores = _score(NoveltyObjective(), reference, archive)
        np.testing.assert_allclose(scores.novelty, 0.0, atol=1e-12)


class TestCoverageGain:
    """Coverage completion (G3): gain = R_cov(archive) − R_cov(archive ∪ X).

    Reference = the four unit-square corners; archive already holds the
    origin, so the covering radius is √2 (the corner (1,1) is farthest).
    """

    def _archive(self):
        corners = np.array([[0.0, 0.0], [0.0, 1.0], [1.0, 0.0], [1.0, 1.0]])
        return _structure_archive(corners, accepted=[[0.0, 0.0]])

    def test_gain_is_analytic_and_sparse(self):
        archive = self._archive()
        values = np.array([[0.5, 0.5], [3.0, 3.0], [1.05, 1.05]])
        scores = _score(CoverageGainObjective(), values, archive)
        gain = scores.components["coverage_gain"]
        # (0.5,0.5) halves every corner's distance: R √2 → √0.5.
        assert gain[0] == pytest.approx(np.sqrt(2.0) - np.sqrt(0.5), rel=1e-9)
        # (3,3) touches nothing the origin does not already cover.
        assert gain[1] == pytest.approx(0.0, abs=1e-12)
        # (1.05,1.05) pins the farthest corner: R √2 → 1.
        assert gain[2] == pytest.approx(np.sqrt(2.0) - 1.0, rel=1e-9)
        assert gain[0] > gain[2] > gain[1]
        # Novelty stays available as a diagnostic component.
        assert scores.novelty[1] > scores.novelty[0]

    def test_empty_archive_ranks_by_established_radius(self):
        corners = np.array([[0.0, 0.0], [0.0, 1.0], [1.0, 0.0], [1.0, 1.0]])
        archive = _structure_archive(corners)
        values = np.array([[0.5, 0.5], [3.0, 3.0]])
        scores = _score(CoverageGainObjective(), values, archive)
        gain = scores.components["coverage_gain"]
        # No covering radius exists yet: the candidate establishing the larger
        # radius ranks higher (fitness must be finite for selection).
        assert np.isfinite(scores.fitness).all()
        assert gain[1] > gain[0]
        assert gain[1] == pytest.approx(np.sqrt(18.0), rel=1e-9)  # (3,3) covers worst corner at 18


class TestLocalEnvironmentNovelty:
    def _archive(self):
        reference = np.array([[0.0], [1.0], [2.0], [3.0]])
        scaling, _ = fit_scaling(reference, "raw")
        return LocalEnvironmentArchive(reference, scaling)

    def _score_1d(self, candidate_groups, objective):
        """Score whole candidates; each group is one candidate's atom distances."""
        archive = self._archive()
        arrays = [np.asarray(g, dtype=np.float64).reshape(-1, 1) for g in candidate_groups]
        atomic = np.concatenate(arrays)
        offsets = np.concatenate([[0], np.cumsum([a.shape[0] for a in arrays])]).astype(np.int64)
        result = objective.evaluate_batch(
            np.zeros((len(candidate_groups), 1)), atomic, offsets, None, archive, np.zeros(len(candidate_groups))
        )
        return result.local_diversity

    def test_top_fraction_mean_prefers_many_new_environments(self):
        objective = LocalEnvironmentNoveltyObjective(aggregation="top_fraction_mean", top_fraction=0.2)
        x1, x2 = [0.1, 1.1, 2.1, 3.1], [0.1, 1.1, 8.0, 9.0]
        d1, d2 = self._score_1d([x1, x2], objective)
        assert d2 > 10 * d1  # 6.0 vs 0.1

    def test_mean_and_quantile_aggregations(self):
        mean_obj = LocalEnvironmentNoveltyObjective(aggregation="mean")
        quantile_obj = LocalEnvironmentNoveltyObjective(aggregation="quantile", quantile=0.5)
        x1, x2 = [0.1, 1.1, 2.1, 3.1], [0.1, 1.1, 8.0, 9.0]
        d1m, d2m = self._score_1d([x1, x2], mean_obj)
        assert d1m == pytest.approx(0.1)
        assert d2m == pytest.approx((0.1 + 0.1 + 5.0 + 6.0) / 4)
        d1q, d2q = self._score_1d([x1, x2], quantile_obj)
        assert d1q == pytest.approx(0.1)
        assert d2q == pytest.approx((0.1 + 5.0) / 2)

    def test_single_freak_atom_dominates_max_but_not_robust_aggregates(self):
        max_obj = LocalEnvironmentNoveltyObjective(aggregation="max")
        mean_obj = LocalEnvironmentNoveltyObjective(aggregation="mean")
        quantile_obj = LocalEnvironmentNoveltyObjective(aggregation="quantile", quantile=0.5)
        count_obj = LocalEnvironmentNoveltyObjective(
            aggregation="quantile", quantile=0.5, novelty_threshold=0.25
        )
        x2, x3 = [0.1, 1.1, 8.0, 9.0], [0.1, 1.1, 2.1, 1000.0]
        (d2_max, d3_max) = self._score_1d([x2, x3], max_obj)
        (d2_mean, d3_mean) = self._score_1d([x2, x3], mean_obj)
        (d2_q, d3_q) = self._score_1d([x2, x3], quantile_obj)
        # max: X3's freak atom (997) crushes X2 (6) — the failure mode that
        # makes max diagnostic-only. Even the mean is dragged up (249 > 2.8):
        # no distance aggregate survives a single extreme outlier, which is
        # exactly why the pipeline rejects such structures geometrically
        # before scoring and offers the robust alternatives below.
        assert d3_max > 100 * d2_max
        assert d3_mean > 10 * d2_mean
        # Median keeps X2 (two genuinely new environments) on top.
        assert d2_q > 10 * d3_q
        # Count-based signal: X2 brings 2 novel environments, X3 only 1.
        archive = self._archive()
        x2_rows = np.asarray(x2, dtype=np.float64).reshape(-1, 1)
        result = count_obj.evaluate_batch(
            np.zeros((1, 1)), x2_rows, np.array([0, 4]), None, archive, np.zeros(1)
        )
        assert result.novel_environment_count[0] == 2
        assert result.components["novel_fraction"][0] == pytest.approx(0.5)
        x3_rows = np.asarray(x3, dtype=np.float64).reshape(-1, 1)
        result = count_obj.evaluate_batch(
            np.zeros((1, 1)), x3_rows, np.array([0, 4]), None, archive, np.zeros(1)
        )
        assert result.novel_environment_count[0] == 1
        assert result.components["novel_fraction"][0] == pytest.approx(0.25)

    def test_novel_environment_count_and_fraction(self):
        objective = LocalEnvironmentNoveltyObjective(
            aggregation="top_fraction_mean", novelty_threshold=0.25
        )
        archive = self._archive()
        x2 = np.array([0.1, 1.1, 8.0, 9.0]).reshape(-1, 1)
        result = objective.evaluate_batch(
            np.zeros((1, 1)), x2, np.array([0, 4]), None, archive, np.zeros(1)
        )
        assert result.novel_environment_count[0] == 2
        assert result.components["novel_fraction"][0] == pytest.approx(0.5)

    def test_requires_atom_rows(self):
        objective = LocalEnvironmentNoveltyObjective()
        with pytest.raises(ValueError):
            objective.evaluate_batch(np.zeros((1, 2)), None, None, None, self._archive(), np.zeros(1))
