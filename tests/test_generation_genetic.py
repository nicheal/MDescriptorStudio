"""G4-1 genetic optimizer: genome inheritance, rank roulette, immigrant
slots, and the degrade path — the USPEX-derived mechanisms, pinned to the
G3.5 optimizer lifecycle contract."""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.datasets.base import DatasetFrame
from mdescriptor_studio_backend.generation.models import Budget, StructureCandidate
from mdescriptor_studio_backend.generation.operators import AnisotropicStrain, AtomicDisplacement, CellShear, IsotropicStrain
from mdescriptor_studio_backend.generation.optimization import (
    CandidateObservation,
    ObservationBatch,
    OptimizationContext,
)
from mdescriptor_studio_backend.generation.optimizers import GeneticOptimizer


def _seed_pool(n: int = 5) -> list[StructureCandidate]:
    rng = np.random.default_rng(11)
    pool = []
    for i in range(n):
        frame = DatasetFrame(
            numbers=np.array([14, 14]),
            positions=rng.uniform(0.5, 3.0, size=(2, 3)),
            cell=np.eye(3) * 8.0,
            pbc=np.ones(3, dtype=bool),
            index=i,
        )
        pool.append(StructureCandidate.from_frame(frame, candidate_id=f"seed_{i}", parent_frame=i))
    return pool


def _optimizer(**params) -> GeneticOptimizer:
    return GeneticOptimizer(
        [AtomicDisplacement(0.1), IsotropicStrain(0.03), CellShear(0.03)],
        children_per_seed=params.pop("children_per_seed", 3),
        **params,
    )

def _context(pool=None, n_seeds=4) -> OptimizationContext:
    return OptimizationContext(seed_pool=tuple(pool or _seed_pool()), n_seeds=n_seeds, budget=Budget())


def _accepted(candidate, rank, generation=1) -> CandidateObservation:
    return CandidateObservation(
        candidate_id=candidate.candidate_id,
        generation=generation,
        candidate=candidate,
        valid=True,
        accepted=True,
        selection_rank=rank,
        structure_descriptor=np.asarray(candidate.positions.mean(axis=0)),
    )


class TestLifecycleContract:
    def test_propose_before_initialize_is_an_error(self):
        optimizer = _optimizer()
        with pytest.raises(RuntimeError, match="initialize"):
            optimizer.propose(budget=100, rng=np.random.default_rng(0))

    def test_initialize_resets_internal_state(self):
        optimizer = _optimizer()
        optimizer.initialize(_context())
        optimizer._pool.append((_seed_pool(1)[0], optimizer._initial_genome()))
        optimizer.initialize(_context())
        assert optimizer._pool == []
        assert optimizer.state_dict()["rounds"] == 0

    def test_invalid_constructor_params_rejected(self):
        pool = _seed_pool()
        with pytest.raises(ValueError, match="parent_fraction"):
            GeneticOptimizer([AtomicDisplacement()], parent_fraction=0.05)
        with pytest.raises(ValueError, match="immigrant_fraction"):
            GeneticOptimizer([AtomicDisplacement()], immigrant_fraction=0.95)
        with pytest.raises(ValueError, match="at least one operator"):
            GeneticOptimizer([])

    def test_fixed_seed_proposals_are_reproducible(self):
        a = _optimizer()
        a.initialize(_context())
        b = _optimizer()
        b.initialize(_context())
        pa = a.propose(budget=100, rng=np.random.default_rng(42))
        pb = b.propose(budget=100, rng=np.random.default_rng(42))
        assert [c.positions.tolist() for c in pa.candidates] == [c.positions.tolist() for c in pb.candidates]


class TestProposeShapes:
    def test_round_one_matches_random_distribution(self):
        # Empty pool → every slot is an immigrant; the round is exactly
        # n_seeds × children_per_seed with no genome bookkeeping pending.
        optimizer = _optimizer()
        optimizer.initialize(_context(n_seeds=4))
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(0))
        assert len(proposal.candidates) == 4 * 3
        assert all(candidate.parent_candidate_id.startswith("seed_") for candidate in proposal.candidates)

    def test_propose_respects_the_budget_cap(self):
        optimizer = _optimizer()
        optimizer.initialize(_context(n_seeds=4))
        proposal = optimizer.propose(budget=5, rng=np.random.default_rng(0))
        assert len(proposal.candidates) == 5

    def test_propose_does_not_exceed_the_round_size(self):
        optimizer = _optimizer()
        optimizer.initialize(_context(n_seeds=2))
        proposal = optimizer.propose(budget=10_000, rng=np.random.default_rng(0))
        assert len(proposal.candidates) == 2 * 3

    def test_state_dict_shape(self):
        optimizer = _optimizer()
        optimizer.initialize(_context())
        state = optimizer.state_dict()
        assert state["name"] == "genetic"
        assert state["children_per_seed"] == 3
        assert state["parent_fraction"] == 0.7
        assert state["immigrant_fraction"] == 0.15
        assert state["pool"] == []
        assert state["operator_stats"] == {}


class TestGenomeAndMask:
    def test_initial_genome_seeds_amplitudes_from_operator_params(self):
        optimizer = _optimizer()
        genome = optimizer._initial_genome()
        assert genome["sigma"] == pytest.approx(0.1)
        assert genome["strain"] == pytest.approx(0.03)
        assert genome["shear"] == pytest.approx(0.03)
        assert set(genome["mask"]) == {"atomic_displacement", "isotropic_strain", "cell_shear"}

    def test_genome_amplitude_overrides_user_params(self):
        optimizer = _optimizer()
        optimizer.initialize(_context())
        proposal = optimizer.propose(budget=100, rng=np.random.default_rng(3))
        displaced = [c for c in proposal.candidates if c.operator == "atomic_displacement"]
        assert displaced, "expected displacement children in the round"
        for candidate in displaced:
            assert candidate.operator_params["max_sigma"] == pytest.approx(0.1)
            # σ is drawn at or below the genome amplitude cap.
            assert candidate.operator_params["sigma"] <= 0.1 + 1e-12

    def test_mask_weights_steers_operator_choice(self):
        optimizer = _optimizer()
        optimizer.initialize(_context())
        genome = optimizer._initial_genome()
        genome["mask"] = {"atomic_displacement": 1.0, "isotropic_strain": _MASK_MIN, "cell_shear": _MASK_MIN}
        parent = _seed_pool(1)[0]
        rng = np.random.default_rng(7)
        picks = [optimizer._choose_operator(genome, parent, rng).name for _ in range(200)]
        assert picks.count("atomic_displacement") > 150

    def test_mutated_stays_within_bounds(self):
        optimizer = _optimizer(gene_mutation_rate=10.0)
        rng = np.random.default_rng(5)
        genome = optimizer._initial_genome()
        for _ in range(200):
            genome = optimizer._mutate_genome(genome, rng)
            assert 0.01 <= genome["sigma"] <= 5.0
            assert 0.005 <= genome["strain"] <= 0.95
            assert 0.005 <= genome["shear"] <= 0.95
            assert all(0.05 <= w <= 1.0 for w in genome["mask"].values())


class TestObserveAndSelection:
    def test_accepted_children_enter_the_pool_in_selection_order(self):
        optimizer = _optimizer()
        optimizer.initialize(_context())
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(1))
        assert len(proposal.candidates) >= 2
        first, second = proposal.candidates[0], proposal.candidates[1]
        # Selection order (1, 0) differs from proposal order (0, 1).
        optimizer.observe(
            ObservationBatch(
                generation=1,
                observations=[_accepted(first, 1), _accepted(second, 0)],
            )
        )
        pool_ids = [entry["candidate_id"] for entry in optimizer.state_dict()["pool"]]
        assert pool_ids == [second.candidate_id, first.candidate_id]

    def test_genome_travels_with_the_accepted_child(self):
        optimizer = _optimizer()
        optimizer.initialize(_context())
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(2))
        child = proposal.candidates[0]
        optimizer.observe(ObservationBatch(generation=1, observations=[_accepted(child, 0)]))
        pool = optimizer.state_dict()["pool"]
        assert len(pool) == 1
        stored = pool[0]["genome"]
        # The stored amplitude is the gene that actually produced the child.
        if child.operator == "atomic_displacement":
            assert child.operator_params["max_sigma"] == pytest.approx(stored["sigma"])

    def test_pool_is_fps_pruned_with_genomes_carried_along(self):
        # limit = min(256, 4·n_seeds) = 4; six accepted children must be
        # pruned back to four, genomes carried along with the survivors.
        optimizer = _optimizer(children_per_seed=6)
        optimizer.initialize(_context(n_seeds=1))
        proposal = optimizer.propose(budget=100, rng=np.random.default_rng(8))
        assert len(proposal.candidates) == 6
        observations = [_accepted(candidate, rank) for rank, candidate in enumerate(proposal.candidates)]
        optimizer.observe(ObservationBatch(generation=1, observations=observations))
        state = optimizer.state_dict()
        assert len(state["pool"]) == 4
        assert all("genome" in entry for entry in state["pool"])

    def test_observe_ignores_candidates_it_never_proposed(self):
        # The genome bookkeeping is strict: only this optimizer's own
        # proposals carry genes, so foreign candidates cannot enter the pool.
        optimizer = _optimizer()
        optimizer.initialize(_context())
        optimizer.observe(
            ObservationBatch(generation=1, observations=[_accepted(candidate, 0) for candidate in _seed_pool(3)])
        )
        assert optimizer.state_dict()["pool"] == []

    def test_unaccepted_children_never_become_parents(self):
        optimizer = _optimizer()
        optimizer.initialize(_context())
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(3))
        rejected = [
            CandidateObservation(
                candidate_id=candidate.candidate_id,
                generation=1,
                candidate=candidate,
                valid=True,
                accepted=False,
                structure_descriptor=np.asarray(candidate.positions.mean(axis=0)),
            )
            for candidate in proposal.candidates
        ]
        optimizer.observe(ObservationBatch(generation=1, observations=rejected))
        assert optimizer.state_dict()["pool"] == []

    def test_operator_stats_count_proposals_and_acceptances(self):
        optimizer = _optimizer()
        optimizer.initialize(_context())
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(4))
        optimizer.observe(
            ObservationBatch(generation=1, observations=[_accepted(proposal.candidates[0], 0)])
        )
        stats = optimizer.state_dict()["operator_stats"]
        assert sum(s["proposed"] for s in stats.values()) == len(proposal.candidates)
        assert sum(s["accepted"] for s in stats.values()) == 1


class TestParentSelection:
    def test_eligible_parents_respect_parent_fraction_when_warm(self):
        optimizer = _optimizer(parent_fraction=0.5, pressure_warmup_pool=0)
        optimizer.initialize(_context())
        pool = _seed_pool(6)
        optimizer._pool = [(candidate, optimizer._initial_genome()) for candidate in pool]
        eligible = optimizer._eligible_parents()
        assert [candidate.candidate_id for candidate, _ in eligible] == [f"seed_{i}" for i in range(3)]

    def test_cold_pool_is_flat_and_complete(self):
        # G4-1.5: below the warmup size the whole pool is eligible (the
        # parent_fraction truncation would aim the round at a handful of
        # lineages) and the roulette draws uniformly.
        optimizer = _optimizer(parent_fraction=0.5, pressure_warmup_pool=32)  # warmup > 6
        optimizer.initialize(_context())
        pool = _seed_pool(6)
        optimizer._pool = [(candidate, optimizer._initial_genome()) for candidate in pool]
        assert optimizer._eligible_parents()[0][0].candidate_id == "seed_0"
        assert len(optimizer._eligible_parents()) == 6
        rng = np.random.default_rng(9)
        picks = [optimizer._roulette_pick(optimizer._eligible_parents(), rng)[0].candidate_id for _ in range(2000)]
        counts = [picks.count(f"seed_{i}") for i in range(6)]
        assert max(counts) / min(counts) < 1.5  # flat, unlike the cubic profile

    def test_roulette_pressure_decays_with_rank_when_warm(self):
        optimizer = _optimizer(pressure_warmup_pool=0)
        optimizer.initialize(_context())
        pool = _seed_pool(5)
        eligible = [(candidate, optimizer._initial_genome()) for candidate in pool]
        rng = np.random.default_rng(9)
        picks = [optimizer._roulette_pick(eligible, rng)[0].candidate_id for _ in range(2000)]
        best = picks.count("seed_0")
        worst = picks.count("seed_4")
        assert best > 3 * worst  # cubic decay between adjacent extremes is steeper still

    def test_pool_children_come_from_pool_immigrants_from_seeds(self):
        optimizer = _optimizer(immigrant_fraction=0.0, pressure_warmup_pool=0)
        optimizer.initialize(_context())
        pool_candidates = _seed_pool(3)
        optimizer._pool = [(candidate, optimizer._initial_genome()) for candidate in pool_candidates]
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(5))
        pool_ids = {candidate.candidate_id for candidate in pool_candidates}
        assert proposal.candidates
        assert all(candidate.parent_candidate_id in pool_ids for candidate in proposal.candidates)

    def test_immigrant_share_is_annealed_up_while_cold(self):
        optimizer = _optimizer(immigrant_fraction=0.0, pressure_warmup_pool=32)  # anneal may only raise it
        optimizer.initialize(_context())
        pool = _seed_pool(3)
        optimizer._pool = [(candidate, optimizer._initial_genome()) for candidate in pool]
        # 3/32 cold → anneal 0.4·(1 − 3/32) ≈ 0.3625, above the user's 0.0.
        assert optimizer._effective_immigrant_fraction() > 0.3
        optimizer._pool = [(candidate, optimizer._initial_genome()) for candidate in _seed_pool(40)]
        assert optimizer._effective_immigrant_fraction() == 0.0  # warm: user value
        # The anneal never lowers a share the user configured higher.
        high = _optimizer(immigrant_fraction=0.6, pressure_warmup_pool=32)
        high.initialize(_context())
        assert high._effective_immigrant_fraction() == 0.6

    def test_every_child_records_its_provenance_for_observe(self):
        optimizer = _optimizer()
        optimizer.initialize(_context())
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(6))
        pending = optimizer._pending
        assert set(pending) == {id(candidate) for candidate in proposal.candidates}
        for candidate, name, genome in pending.values():
            assert candidate in proposal.candidates  # kept alive for identity
            assert name in {operator.name for operator in optimizer.operators}
            assert 0.05 <= genome["mask"][name] <= 1.0

    def test_colliding_candidate_ids_do_not_cross_genomes(self):
        # Two children of one parent can share a candidate_id (strain ids are
        # derived from the volume change); genome bookkeeping keys on object
        # identity, so neither acceptance inherits the other's genes. Under
        # string keying both lookups resolve to the last-inserted entry.
        optimizer = _optimizer()
        optimizer.initialize(_context())
        parent = _seed_pool(1)[0]
        genome_a = optimizer._initial_genome()  # sigma 0.1
        genome_b = dict(optimizer._initial_genome())
        genome_b["sigma"] = 0.5
        child_a = parent.child(
            candidate_id="dup", positions=parent.positions,
            operator="atomic_displacement", operator_params={"max_sigma": genome_a["sigma"]},
        )
        child_b = parent.child(
            candidate_id="dup", positions=parent.positions + 0.01,
            operator="atomic_displacement", operator_params={"max_sigma": genome_b["sigma"]},
        )
        optimizer._pending[id(child_a)] = (child_a, "atomic_displacement", genome_a)
        optimizer._pending[id(child_b)] = (child_b, "atomic_displacement", genome_b)
        optimizer.observe(
            ObservationBatch(generation=1, observations=[_accepted(child_a, 0), _accepted(child_b, 1)])
        )
        pool = optimizer.state_dict()["pool"]
        assert sorted(entry["genome"]["sigma"] for entry in pool) == [0.1, 0.5]


class TestAutoFrac:
    def test_multiplier_is_neutral_without_data(self):
        optimizer = _optimizer(autofrac=True)
        optimizer.initialize(_context())
        assert optimizer._autofrac_multiplier("atomic_displacement") == 1.0
        optimizer._operator_stats = {"atomic_displacement": {"proposed": 10, "accepted": 0}}
        # Zero mean acceptance everywhere → cold-start data must not steer.
        assert optimizer._autofrac_multiplier("atomic_displacement") == 1.0

    def test_successful_operator_gets_more_weight(self):
        optimizer = _optimizer(autofrac=True)
        optimizer.initialize(_context())
        optimizer._operator_stats = {
            "atomic_displacement": {"proposed": 100, "accepted": 50},
            "isotropic_strain": {"proposed": 100, "accepted": 5},
        }
        hot = optimizer._autofrac_multiplier("atomic_displacement")
        cold = optimizer._autofrac_multiplier("isotropic_strain")
        assert hot > 1.0 and cold < 1.0
        # floor + (1-floor)·rate/mean-rate, mean rate = (0.5 + 0.05)/2
        assert hot == pytest.approx(_AUTOFrac_FLOOR + (1 - _AUTOFrac_FLOOR) * (0.5 / 0.275))
        assert cold == pytest.approx(_AUTOFrac_FLOOR + (1 - _AUTOFrac_FLOOR) * (0.05 / 0.275))

    def test_multiplier_clamped_at_the_bounds(self):
        # Four operators, one takes every acceptance: relative rate 4 →
        # blend 2.35, clamped to the 2.0 ceiling; the zero-success operators
        # land on the blend floor (0.55, above the 0.5 safety clamp).
        optimizer = GeneticOptimizer(
            [AtomicDisplacement(0.1), IsotropicStrain(0.03), CellShear(0.03), AnisotropicStrain(0.03)],
            autofrac=True,
        )
        optimizer.initialize(_context())
        optimizer._operator_stats = {
            "atomic_displacement": {"proposed": 100, "accepted": 100},
            "isotropic_strain": {"proposed": 100, "accepted": 0},
            "cell_shear": {"proposed": 100, "accepted": 0},
            "anisotropic_strain": {"proposed": 100, "accepted": 0},
        }
        assert optimizer._autofrac_multiplier("atomic_displacement") == pytest.approx(_AUTOFrac_CLAMP[1])
        assert optimizer._autofrac_multiplier("isotropic_strain") == pytest.approx(_AUTOFrac_FLOOR)

    def test_autofrac_shifts_operator_choice(self):
        optimizer = _optimizer(autofrac=True)
        optimizer.initialize(_context())
        optimizer._operator_stats = {
            "atomic_displacement": {"proposed": 100, "accepted": 50},
            "isotropic_strain": {"proposed": 100, "accepted": 5},
            "cell_shear": {"proposed": 0, "accepted": 0},  # no proposals → neutral 1.0
        }
        genome = optimizer._initial_genome()
        parent = _seed_pool(1)[0]
        rng = np.random.default_rng(7)
        picks = [optimizer._choose_operator(genome, parent, rng).name for _ in range(600)]
        disp = picks.count("atomic_displacement")
        strain = picks.count("isotropic_strain")
        # Effective weights 1.37 : 1.0 : 0.63 → disp share ≈ 45%, strain ≈ 21%.
        assert disp > 240 and strain < 160


class TestDegradePath:
    def test_all_stale_mask_choices_degrade_to_displacement(self):
        # A genome whose mask favours an operator that raises on apply must
        # still produce children through the plain-displacement fallback.
        class _Broken:
            name = "broken"

            def apply(self, parent, rng, params):
                raise ValueError("always fails")

        optimizer = GeneticOptimizer([_Broken(), AtomicDisplacement(0.1)])
        optimizer.initialize(_context())
        pool = _seed_pool(2)
        genome = optimizer._initial_genome()
        genome["mask"] = {"broken": 1.0, "atomic_displacement": _MASK_MIN}
        optimizer._pool = [(candidate, genome) for candidate in pool]
        proposal = optimizer.propose(budget=1000, rng=np.random.default_rng(7))
        assert proposal.candidates
        assert all(candidate.operator in ("broken", "atomic_displacement") for candidate in proposal.candidates)
        # Nothing entered via "broken" — every recorded child used the fallback.
        assert all(name == "atomic_displacement" for _c, name, _g in optimizer._pending.values())


class TestEngineIntegration:
    def test_genetic_run_accepts_and_breeds(self):
        from mdescriptor_studio_backend.generation.engine import GenerationEngine
        from mdescriptor_studio_backend.generation.objectives import NoveltyObjective

        pool = _seed_pool(6)
        rng = np.random.default_rng(11)
        reference = np.stack([candidate.positions.mean(axis=0) for candidate in pool])
        from mdescriptor_studio_backend.analysis.sampling import fit_scaling
        from mdescriptor_studio_backend.generation.archive import DescriptorArchive
        from mdescriptor_studio_backend.generation.constraints import build_constraints
        from mdescriptor_studio_backend.generation.evaluator import DescriptorEvaluation

        structure_scaling, _ = fit_scaling(reference, "robust")

        class _StubEvaluator:
            def evaluate(self, candidates, *, return_atomic=False, control=None):
                rows = np.concatenate([np.asarray(c.positions, dtype=np.float64) for c in candidates])
                offsets = np.concatenate([[0], np.cumsum([len(c.positions) for c in candidates])]).astype(np.int64)
                pooled = np.stack(
                    [rows[int(offsets[i]) : int(offsets[i + 1])].mean(axis=0) for i in range(len(candidates))]
                )
                return DescriptorEvaluation(structure_values=pooled, atomic_values=None, row_offsets=None)

        optimizer = _optimizer(children_per_seed=2)
        engine = GenerationEngine(
            seed_pool=pool,
            evaluator=_StubEvaluator(),
            structure_archive=DescriptorArchive(reference, structure_scaling),
            local_archive=None,
            objective=NoveltyObjective(),
            optimizer=optimizer,
            constraints=build_constraints({"min_distance_mode": "covalent", "min_distance_factor": 0.5}),
            budget=Budget(max_evaluations=200, max_accepted=10, max_generations=6),
            rng=np.random.default_rng(13),
            n_seeds=3,
        )
        result = engine.run()
        assert result.accepted_count > 0
        assert result.evaluation_count <= 200
        # Round 2+ must actually draw from the pool: the genome pool bred.
        stats = optimizer.state_dict()["operator_stats"]
        assert sum(s["accepted"] for s in stats.values()) > 0

    def test_genetic_run_is_reproducible(self):
        from mdescriptor_studio_backend.generation.engine import GenerationEngine
        from mdescriptor_studio_backend.generation.objectives import NoveltyObjective

        def _run():
            pool = _seed_pool(6)
            reference = np.stack([candidate.positions.mean(axis=0) for candidate in pool])
            from mdescriptor_studio_backend.analysis.sampling import fit_scaling
            from mdescriptor_studio_backend.generation.archive import DescriptorArchive
            from mdescriptor_studio_backend.generation.constraints import build_constraints
            from mdescriptor_studio_backend.generation.evaluator import DescriptorEvaluation

            structure_scaling, _ = fit_scaling(reference, "robust")

            class _StubEvaluator:
                def evaluate(self, candidates, *, return_atomic=False, control=None):
                    rows = np.concatenate([np.asarray(c.positions, dtype=np.float64) for c in candidates])
                    offsets = np.concatenate(
                        [[0], np.cumsum([len(c.positions) for c in candidates])]
                    ).astype(np.int64)
                    pooled = np.stack(
                        [rows[int(offsets[i]) : int(offsets[i + 1])].mean(axis=0) for i in range(len(candidates))]
                    )
                    return DescriptorEvaluation(structure_values=pooled, atomic_values=None, row_offsets=None)

            optimizer = _optimizer(children_per_seed=2)
            engine = GenerationEngine(
                seed_pool=pool,
                evaluator=_StubEvaluator(),
                structure_archive=DescriptorArchive(reference, structure_scaling),
                local_archive=None,
                objective=NoveltyObjective(),
                optimizer=optimizer,
                constraints=build_constraints({"min_distance_mode": "covalent", "min_distance_factor": 0.5}),
                budget=Budget(max_evaluations=120, max_accepted=8, max_generations=5),
                rng=np.random.default_rng(21),
                n_seeds=3,
            )
            result = engine.run()
            return [
                (c.candidate_id, np.round(c.positions, 12).tolist())
                for c in result.accepted
            ]

        assert _run() == _run()


class TestRegistryAndRequest:
    def test_registry_builds_genetic_optimizer(self):
        from mdescriptor_studio_backend.generation.models import OperatorSpec
        from mdescriptor_studio_backend.generation.registry import GENERATION_REGISTRY

        operators = [GENERATION_REGISTRY.build_operator(OperatorSpec("atomic_displacement", {"max_sigma": 0.2}))]
        optimizer = GENERATION_REGISTRY.build_optimizer(
            "genetic", operators, {"children_per_seed": 4, "batch_accept": 2, "parent_fraction": 0.5}
        )
        assert optimizer.name == "genetic"
        assert optimizer.children_per_seed == 4
        assert optimizer.batch_accept == 2
        assert optimizer.parent_fraction == 0.5

    def test_catalog_offers_genetic_with_defaults(self):
        from mdescriptor_studio_backend.generation.registry import GENERATION_REGISTRY

        catalog = GENERATION_REGISTRY.catalog()
        genetic = next(entry for entry in catalog["optimizers"] if entry["name"] == "genetic")
        assert genetic["params"]["parent_fraction"] == 0.7
        assert genetic["params"]["immigrant_fraction"] == 0.15


_MASK_MIN = 0.05  # keep the literal in sync with the mask floor under test
_AUTOFrac_FLOOR = 0.55  # keep in sync with the AutoFrac blend floor
_AUTOFrac_CLAMP = (0.5, 2.0)  # keep in sync with the AutoFrac multiplier clamp


class TestRequestParsing:
    @staticmethod
    def _request(**overrides) -> dict:
        payload = {
            "dataset_id": "ds",
            "descriptor_run_id": "run",
            "optimizer": "genetic",
            "optimizer_params": {"children_per_seed": 4, "batch_accept": 2, "n_seeds": 8},
            "objective": {"type": "novelty"},
            "operators": {"atomic_displacement": {"enabled": True, "max_sigma": 0.1}},
            "constraints": {"min_distance_mode": "none"},
            "budget": {"max_evaluations": 100},
            "seed": 42,
        }
        payload.update(overrides)
        return payload

    def test_genetic_params_normalize_at_submit(self):
        from mdescriptor_studio_backend.generation.models import parse_request

        request = parse_request(self._request())
        assert request.optimizer_params["n_seeds"] == 8
        assert request.optimizer_params["children_per_seed"] == 4
        assert request.optimizer_params["batch_accept"] == 2
        assert request.optimizer_params["parent_fraction"] == 0.7
        assert request.optimizer_params["immigrant_fraction"] == 0.15

    def test_genetic_fraction_bounds_enforced(self):
        from mdescriptor_studio_backend.errors import AppError
        from mdescriptor_studio_backend.generation.models import parse_request

        with pytest.raises(AppError, match="parent_fraction"):
            parse_request(self._request(optimizer_params={"parent_fraction": 1.5}))
        with pytest.raises(AppError, match="parent_fraction"):
            parse_request(self._request(optimizer_params={"parent_fraction": 0.05}))
        with pytest.raises(AppError, match="immigrant_fraction"):
            parse_request(self._request(optimizer_params={"immigrant_fraction": 0.95}))
        with pytest.raises(AppError, match="immigrant_fraction"):
            parse_request(self._request(optimizer_params={"immigrant_fraction": "many"}))
        # The valid boundary values pass.
        ok = parse_request(self._request(optimizer_params={"parent_fraction": 1.0, "immigrant_fraction": 0.0}))
        assert ok.optimizer_params["parent_fraction"] == 1.0
        assert ok.optimizer_params["immigrant_fraction"] == 0.0

    def test_genetic_unknown_param_rejected(self):
        from mdescriptor_studio_backend.errors import AppError
        from mdescriptor_studio_backend.generation.models import parse_request

        with pytest.raises(AppError, match="unknown optimizer params"):
            parse_request(self._request(optimizer_params={"reuse_accepted_seeds": True}))
        with pytest.raises(AppError, match="unknown optimizer params"):
            parse_request(self._request(optimizer_params={"tournament_size": 4}))

    def test_random_params_rejected_for_genetic_and_vice_versa(self):
        from mdescriptor_studio_backend.errors import AppError
        from mdescriptor_studio_backend.generation.models import parse_request

        with pytest.raises(AppError, match="unknown optimizer params"):
            parse_request(
                self._request(optimizer="random", optimizer_params={"children_per_seed": 4, "parent_fraction": 0.7})
            )
