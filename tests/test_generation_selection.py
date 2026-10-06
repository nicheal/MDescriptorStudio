"""Batch selection strategies (audit P1-03/R3).

``structure_fps_v1`` (novelty ranking + FPS over mean-pooled structure rows)
is the baseline; ``local_incremental_maximin_v1`` accepts the fitness elite
by marginal strictly-new environment count. The acceptance gate: on fixed
synthetic benchmarks the local strategy's strict unique count is never below
the structure-FPS baseline at the same budget.
"""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis.sampling import apply_scaling, fit_scaling
from mdescriptor_studio_backend.errors import AppError, INVALID_PARAMS
from mdescriptor_studio_backend.generation.archive import LocalEnvironmentArchive
from mdescriptor_studio_backend.generation.engine import (
    GenerationEngine,
    count_strict_unique_environments,
    select_diverse_batch,
    select_local_incremental_batch,
)
from mdescriptor_studio_backend.generation.models import parse_request


def _one_dim_archive(rows: list[float]) -> LocalEnvironmentArchive:
    reference = np.asarray(rows, dtype=np.float64)[:, None]
    scaling, _ = fit_scaling(reference, "raw")
    return LocalEnvironmentArchive(reference, scaling)


def _offsets(sizes: list[int]) -> np.ndarray:
    return np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)


def _scaled_oracle(selected, atomic, offsets, threshold, archive) -> int:
    """Independent scalar-loop strict count, written straight from the
    metric definition in the archive's scaled space — not via the engine
    helpers — so it can catch a space or ordering regression in the shared
    counter (2026-09-30 audit §7.1)."""
    atomic = np.asarray(atomic, dtype=np.float64)
    scaled = apply_scaling(archive.scaling, atomic)
    memory: list[np.ndarray] = []
    for index in selected:
        for j in range(int(offsets[index]), int(offsets[index + 1])):
            if archive.nearest_per_row(atomic[j : j + 1])[0] <= threshold:
                continue
            if all(np.linalg.norm(scaled[j] - kept) > threshold for kept in memory):
                memory.append(scaled[j])
    return len(memory)


class TestSameMeanDifferentEnvironments:
    def test_local_strategy_resolves_what_mean_pooling_hides(self):
        # Structure descriptors (mean pooling) hide environment redundancy:
        # c0/c1 sit far apart in structure space yet their environments are
        # near-duplicates, while c0 and c3 share ONE structure value. The
        # structure-FPS baseline cannot see any of this and wastes a slot on
        # the duplicate; the local strategy counts environments directly.
        structure_values = np.array([[0.0], [5.0], [9.0], [0.0]])
        atomic = np.array(
            [
                [0.0], [10.0],  # c0 — environments A
                [0.5], [10.5],  # c1 — near-duplicates of A, structure 5.0 away
                [3.0], [7.0],   # c2 — genuinely different environments
                [0.2], [10.2],  # c3 — duplicates of A, same structure value as c0
            ]
        )
        offsets = _offsets([2, 2, 2, 2])
        fitness = np.ones(4)
        archive = _one_dim_archive([5.0])
        threshold = 1.0

        fps = select_diverse_batch(fitness, structure_values, budget=2)
        local = select_local_incremental_batch(
            fitness, structure_values, atomic, offsets, budget=2,
            local_archive=archive, threshold=threshold,
        )
        fps_unique = count_strict_unique_environments(fps, atomic, offsets, threshold, archive)
        local_unique = count_strict_unique_environments(local, atomic, offsets, threshold, archive)
        # The baseline spends one of two slots on an environment duplicate.
        assert fps_unique == 2
        # The local strategy claims both genuinely distinct environment groups.
        assert local_unique == 4
        # Equal fitness everywhere: the shared elite order (the FPS
        # baseline's reversed-stable, see _fitness_elite_order) picks the
        # *later* input index first, so the first pick is c3 and the second
        # is the env-distinct c2 — c1, the near-duplicate FPS wastes a slot
        # on, is never selected.
        assert set(local) == {2, 3}
        # The premise: structure values do not track environment redundancy.
        assert structure_values[0] == structure_values[3]  # same structure, same envs
        assert structure_values[0, 0] != structure_values[1, 0]  # far structure, dup envs


class TestFixedSyntheticBenchmark:
    def test_local_strategy_never_loses_to_structure_fps(self):
        # Acceptance gate (R3): on fixed synthetic benchmarks the local
        # strategy's strict unique count is >= the structure-FPS baseline's
        # at the same budget.
        for seed in range(5):
            rng = np.random.default_rng(seed)
            n_candidates, rows_per_candidate, dim = 40, 6, 2
            atomic = rng.uniform(0.0, 20.0, size=(n_candidates * rows_per_candidate, dim))
            offsets = _offsets([rows_per_candidate] * n_candidates)
            structure_values = np.stack(
                [atomic[int(offsets[i]) : int(offsets[i + 1])].mean(axis=0) for i in range(n_candidates)]
            )
            fitness = rng.uniform(0.0, 1.0, size=n_candidates)
            reference = rng.uniform(0.0, 20.0, size=(30, dim))
            scaling, _ = fit_scaling(reference, "robust")
            archive = LocalEnvironmentArchive(reference, scaling)
            budget = 8
            threshold = 0.5

            fps = select_diverse_batch(fitness, structure_values, budget=budget)
            local = select_local_incremental_batch(
                fitness, structure_values, atomic, offsets, budget=budget,
                local_archive=archive, threshold=threshold,
            )
            assert len(local) == len(fps) == budget  # same-budget comparison
            fps_unique = count_strict_unique_environments(fps, atomic, offsets, threshold, archive)
            local_unique = count_strict_unique_environments(local, atomic, offsets, threshold, archive)
            assert local_unique >= fps_unique, f"seed {seed}: {local_unique} < {fps_unique}"

    def test_local_strategy_reports_what_it_optimizes(self):
        # The strict unique count of the selection equals an independent
        # scaled-space oracle — the strategy optimizes exactly the metric
        # the benchmark reports (2026-09-30 audit §7.1: this used to assert
        # only ``unique > 0`` while the counter lived in raw space).
        rng = np.random.default_rng(17)
        n_candidates, rows_per_candidate, dim = 30, 5, 3
        atomic = rng.uniform(0.0, 15.0, size=(n_candidates * rows_per_candidate, dim))
        offsets = _offsets([rows_per_candidate] * n_candidates)
        structure_values = atomic.reshape(n_candidates, rows_per_candidate, dim).mean(axis=1)
        fitness = rng.uniform(0.0, 1.0, size=n_candidates)
        reference = rng.uniform(0.0, 15.0, size=(20, dim))
        scaling, _ = fit_scaling(reference, "robust")
        archive = LocalEnvironmentArchive(reference, scaling)
        threshold = 0.5
        local = select_local_incremental_batch(
            fitness, structure_values, atomic, offsets, budget=6,
            local_archive=archive, threshold=threshold,
        )
        unique = count_strict_unique_environments(local, atomic, offsets, threshold, archive)
        assert unique > 0
        # Robust scaling moves every row: raw-space comparisons cannot agree
        # with this oracle — the equality locks the counter (and the greedy
        # memory, whose gains this count sums over the selection) into the
        # archive's scaled space, across the within-candidate and
        # cross-candidate dedup branches alike.
        assert unique == _scaled_oracle(local, atomic, offsets, threshold, archive)


class TestBoundedConsideration:
    def test_elite_pool_cap_keeps_selection_among_fitness_elites(self):
        # R3.1: a fixed high-fitness sub-pool is chosen first — a low-fitness
        # candidate with a huge environment gain outside the pool is never
        # considered (pool = budget * top_pool_factor = 1 here).
        structure_values = np.arange(6, dtype=np.float64)[:, None]
        atomic = np.array([[0.0], [50.0], [51.0], [52.0], [53.0], [54.0]])
        offsets = _offsets([1, 1, 1, 1, 1, 1])
        fitness = np.array([0.9, 0.1, 0.1, 0.1, 0.1, 0.1])
        archive = _one_dim_archive([100.0])
        selection = select_local_incremental_batch(
            fitness, structure_values, atomic, offsets, budget=1,
            local_archive=archive, threshold=1.0, top_pool_factor=1,
        )
        assert selection == [0]

    def test_zero_gain_candidates_still_fill_the_budget(self):
        # Filler keeps accepted counts comparable with the baseline at equal
        # budget: archive-near candidates (gain 0) are still selected.
        structure_values = np.array([[0.0], [1.0]])
        atomic = np.array([[5.05], [4.95]])
        offsets = _offsets([1, 1])
        archive = _one_dim_archive([5.0])
        selection = select_local_incremental_batch(
            np.ones(2), structure_values, atomic, offsets, budget=2,
            local_archive=archive, threshold=1.0,
        )
        assert sorted(selection) == [0, 1]
        assert count_strict_unique_environments(selection, atomic, offsets, 1.0, archive) == 0

    def test_empty_candidates_and_degenerate_inputs(self):
        archive = _one_dim_archive([5.0])
        assert select_local_incremental_batch(
            np.array([np.inf, -np.inf]), np.zeros((2, 1)),
            np.zeros((2, 1)), _offsets([1, 1]), budget=2,
            local_archive=archive, threshold=1.0,
        ) == []
        # Empty row blocks never crash the greedy; like zero-gain candidates
        # they still fill the budget, and only the real row counts.
        atomic = np.array([[3.0]])
        offsets = _offsets([0, 1, 0])
        selection = select_local_incremental_batch(
            np.ones(3), np.arange(3, dtype=np.float64)[:, None], atomic, offsets,
            budget=3, local_archive=archive, threshold=1.0,
        )
        assert sorted(selection) == [0, 1, 2]
        assert count_strict_unique_environments(selection, atomic, offsets, 1.0, archive) == 1

    def test_budget_above_the_elite_cap_still_fills_like_fps(self):
        # 2026-09-30 audit case J: 129 one-row candidates, budget 129 — the
        # old pool truncation at max_candidates=128 silently accepted one
        # fewer than the uncapped FPS baseline. The cap may only limit the
        # elite surplus beyond the budget, never the budget itself.
        n = 129
        atomic = np.arange(3.0, 3.0 + 2.0 * n, 2.0)[:, None]  # pairwise distance 2 > threshold
        offsets = _offsets([1] * n)
        fitness = np.ones(n)
        archive = _one_dim_archive([0.0])
        local = select_local_incremental_batch(
            fitness, atomic, atomic, offsets, budget=n,
            local_archive=archive, threshold=1.0,
        )
        fps = select_diverse_batch(fitness, atomic, budget=n)
        assert len(local) == len(fps) == n
        assert count_strict_unique_environments(local, atomic, offsets, 1.0, archive) == n


class TestEliteTieContract:
    def test_equal_fitness_elites_match_the_fps_baseline(self):
        # 2026-09-30 audit case F: with equal fitness and a budget/pool
        # cutoff inside the tie group, the strategies used to pick different
        # identities (local kept earlier ties via ``argsort(-fitness)``;
        # FPS's reversed stable sort keeps later ones). Both now share the
        # FPS baseline's elite order, so the picks agree under any input
        # permutation of the tie group.
        rows = np.array([[3.0], [5.0]])
        offsets = _offsets([1, 1])
        fitness = np.ones(2)
        structure = np.array([[0.0], [2.0]])
        archive = _one_dim_archive([100.0])  # every row far outside → gain 1 each
        for f, s, r in (
            (fitness, structure, rows),
            (fitness, structure[::-1], rows[::-1]),
        ):
            local = select_local_incremental_batch(
                f, s, r, offsets, 1, local_archive=archive, threshold=1.0
            )
            assert local == select_diverse_batch(f, s, 1)

    def test_empty_reference_archive_rejected_empty_accepted_supported(self):
        # 2026-09-30 audit case H (contract): "no accepted structures yet"
        # is a valid archive state; a truly empty reference set is not.
        archive = _one_dim_archive([5.0])
        assert archive.accepted_matrix is None
        with pytest.raises(ValueError, match="non-empty"):
            LocalEnvironmentArchive(np.empty((0, 1)), archive.scaling)


class TestEngineIntegration:
    def _pool_and_archives(self, seed: int):
        from mdescriptor_studio_backend.datasets.base import DatasetFrame
        from mdescriptor_studio_backend.generation.archive import DescriptorArchive
        from mdescriptor_studio_backend.generation.models import StructureCandidate

        rng = np.random.default_rng(seed)
        pool = []
        for i in range(4):
            frame = DatasetFrame(
                numbers=np.array([14, 14]),
                positions=rng.uniform(0.5, 3.0, size=(2, 3)),
                cell=np.eye(3) * 8.0,
                pbc=np.ones(3, dtype=bool),
                index=i,
            )
            pool.append(StructureCandidate.from_frame(frame, candidate_id=f"seed_{i}", parent_frame=i))
        reference = np.stack([c.positions.mean(axis=0) for c in pool])
        structure_scaling, _ = fit_scaling(reference, "robust")
        atom_reference = np.concatenate([c.positions for c in pool])
        local_scaling, _ = fit_scaling(atom_reference, "robust")
        return pool, DescriptorArchive(reference, structure_scaling), LocalEnvironmentArchive(atom_reference, local_scaling)

    @staticmethod
    def _evaluator():
        from mdescriptor_studio_backend.generation.evaluator import DescriptorEvaluation

        class _Stub:
            def evaluate(self, candidates, *, return_atomic=False, control=None):
                rows = np.concatenate([np.asarray(c.positions, dtype=np.float64) for c in candidates])
                offsets = np.concatenate([[0], np.cumsum([len(c.positions) for c in candidates])]).astype(np.int64)
                pooled = np.stack(
                    [rows[int(offsets[i]) : int(offsets[i + 1])].mean(axis=0) for i in range(len(candidates))]
                )
                return DescriptorEvaluation(structure_values=pooled, atomic_values=rows, row_offsets=offsets)

        return _Stub()

    def _run(self, seed: int, selection_strategy: str):
        from mdescriptor_studio_backend.generation.constraints import build_constraints
        from mdescriptor_studio_backend.generation.models import Budget
        from mdescriptor_studio_backend.generation.objectives import CompositeObjective
        from mdescriptor_studio_backend.generation.optimizers import RandomSearchOptimizer
        from mdescriptor_studio_backend.generation.operators import AtomicDisplacement

        pool, structure_archive, local_archive = self._pool_and_archives(seed)
        optimizer = RandomSearchOptimizer([AtomicDisplacement(0.4)], children_per_seed=4, batch_accept=2)
        engine = GenerationEngine(
            seed_pool=pool,
            evaluator=self._evaluator(),
            structure_archive=structure_archive,
            local_archive=local_archive,
            objective=CompositeObjective(structure_weight=0.0, local_weight=1.0, novelty_threshold=0.25),
            optimizer=optimizer,
            constraints=build_constraints({"min_distance_mode": "none"}),
            budget=Budget(max_evaluations=60, max_accepted=8, max_generations=4),
            rng=np.random.default_rng(seed),
            n_seeds=2,
            selection_strategy=selection_strategy,
        )
        return engine.run()

    def test_local_strategy_run_achieves_the_baseline_unique_count(self):
        fps_result = self._run(3, "structure_fps_v1")
        local_result = self._run(3, "local_incremental_maximin_v1")
        assert local_result.accepted_count > 0
        fps_unique = sum(r.unique_novel_environments or 0 for r in fps_result.rounds)
        local_unique = sum(r.unique_novel_environments or 0 for r in local_result.rounds)
        assert local_unique >= fps_unique

    def test_local_strategy_requires_local_archive_and_threshold(self):
        from mdescriptor_studio_backend.generation.constraints import build_constraints
        from mdescriptor_studio_backend.generation.models import Budget
        from mdescriptor_studio_backend.generation.objectives import NoveltyObjective
        from mdescriptor_studio_backend.generation.optimizers import RandomSearchOptimizer
        from mdescriptor_studio_backend.generation.operators import AtomicDisplacement

        pool, structure_archive, _ = self._pool_and_archives(1)
        with pytest.raises(ValueError, match="requires a local-environment archive"):
            GenerationEngine(
                seed_pool=pool,
                evaluator=self._evaluator(),
                structure_archive=structure_archive,
                local_archive=None,
                objective=NoveltyObjective(),
                optimizer=RandomSearchOptimizer([AtomicDisplacement(0.1)]),
                constraints=build_constraints({"min_distance_mode": "none"}),
                budget=Budget(max_evaluations=16, max_accepted=2, max_generations=2),
                rng=np.random.default_rng(1),
                selection_strategy="local_incremental_maximin_v1",
            )
        with pytest.raises(ValueError, match="selection_strategy must be one of"):
            GenerationEngine(
                seed_pool=pool,
                evaluator=self._evaluator(),
                structure_archive=structure_archive,
                local_archive=None,
                objective=NoveltyObjective(),
                optimizer=RandomSearchOptimizer([AtomicDisplacement(0.1)]),
                constraints=build_constraints({"min_distance_mode": "none"}),
                budget=Budget(max_evaluations=16, max_accepted=2, max_generations=2),
                rng=np.random.default_rng(1),
                selection_strategy="bogus",
            )


class TestRequestValidation:
    @staticmethod
    def _payload(**overrides) -> dict:
        payload = {
            "dataset_id": "ds",
            "descriptor_run_id": "run",
            "optimizer": "random",
            "optimizer_params": {"children_per_seed": 2, "batch_accept": 2, "n_seeds": 2},
            "objective": {"type": "novelty"},
            "operators": {"atomic_displacement": {"enabled": True, "max_sigma": 0.1}},
            "constraints": {"min_distance_mode": "none"},
            "budget": {"max_evaluations": 16},
            "seed": 42,
        }
        payload.update(overrides)
        return payload

    def test_default_is_the_fps_baseline(self):
        assert parse_request(self._payload()).selection_strategy == "structure_fps_v1"

    def test_local_strategy_is_accepted(self):
        request = parse_request({**self._payload(), "selection_strategy": "local_incremental_maximin_v1"})
        assert request.selection_strategy == "local_incremental_maximin_v1"

    def test_unknown_strategy_is_rejected(self):
        with pytest.raises(AppError) as excinfo:
            parse_request({**self._payload(), "selection_strategy": "bogus"})
        assert excinfo.value.code == INVALID_PARAMS
