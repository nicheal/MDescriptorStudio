"""Energy/force screening (audit R5.1): spec validation, verdict math, and
the engine's post-selection gate. The real mdescriptor predictor requires
>= 0.3.5; the verdict-math tests inject a fake predictor so they run on any
installed wheel.
"""

from __future__ import annotations

import numpy as np
import pytest

from mdescriptor_studio_backend.datasets.base import DatasetFrame
from mdescriptor_studio_backend.generation.constraints import build_constraints
from mdescriptor_studio_backend.generation.models import Budget, StructureCandidate
from mdescriptor_studio_backend.generation.engine import GenerationEngine
from mdescriptor_studio_backend.generation.evaluator import DescriptorEvaluation
from mdescriptor_studio_backend.generation.objectives import NoveltyObjective
from mdescriptor_studio_backend.generation.operators import AtomicDisplacement
from mdescriptor_studio_backend.generation.optimizers import RandomSearchOptimizer
from mdescriptor_studio_backend.generation.screening import (
    EnergyForceScreen,
    ScreeningSpec,
    ScreeningVerdict,
    mdescriptor_predictors_available,
)


def _candidate(cid: str = "c", pbc=(True, True, True), numbers=(14, 14)) -> StructureCandidate:
    frame = DatasetFrame(
        numbers=np.asarray(numbers, dtype=np.int64),
        positions=np.array([[0.0, 0.0, 0.0], [2.0, 2.0, 2.0]][: len(numbers)], dtype=np.float64),
        cell=np.eye(3) * 8.0,
        pbc=np.asarray(pbc, dtype=bool),
        index=0,
    )
    return StructureCandidate.from_frame(frame, candidate_id=cid, parent_frame=0)


class _FakePredictor:
    """Deterministic fake: energy = epa × atoms; |F| = fixed eV/Å."""

    def __init__(self, energy_per_atom: float = 1.0, max_force: float = 2.0):
        self._epa = energy_per_atom
        self._force = max_force

    def predict(self, batch):
        class _R:
            def __init__(self, energy, forces, offsets):
                self.energy = energy
                self.forces = forces
                self.offsets = offsets

        offsets = np.asarray(batch.offsets, dtype=np.int64)
        energies, forces = [], []
        for i in range(len(batch.ids)):
            atoms = int(offsets[i + 1] - offsets[i])
            energies.append(atoms * self._epa)
            forces.extend([[self._force, 0.0, 0.0]] * atoms)
        return _R(np.asarray(energies), np.asarray(forces), offsets)


def _screen_with_fake(spec: ScreeningSpec) -> EnergyForceScreen:
    screen = EnergyForceScreen.__new__(EnergyForceScreen)
    screen.spec = spec
    screen._predictor = _FakePredictor(energy_per_atom=1.0, max_force=2.0)
    return screen


class TestScreeningSpec:
    def test_defaults_and_disabled(self):
        assert ScreeningSpec.from_constraints({}) is None
        assert ScreeningSpec.from_constraints({"energy_screening": {"enabled": False}}) is None
        spec = ScreeningSpec.from_constraints({"energy_screening": {"enabled": True}})
        assert spec.model == "NEP" and spec.device == "cpu"
        assert spec.max_energy_per_atom is None and spec.max_force is None

    def test_dpa4c_requires_checkpoint(self):
        with pytest.raises(ValueError, match="checkpoint"):
            ScreeningSpec(model="DPA4C").validate()
        ScreeningSpec(model="DPA4C", checkpoint="model.pt").validate()

    def test_bounds_must_be_finite_numbers(self):
        # Energy bounds are typically NEGATIVE eV/atom; the contract is
        # finiteness, not positivity.
        ScreeningSpec(max_energy_per_atom=-1.0).validate()
        with pytest.raises(ValueError, match="max_energy_per_atom"):
            ScreeningSpec(max_energy_per_atom=float("nan")).validate()
        with pytest.raises(ValueError, match="max_force"):
            ScreeningSpec(max_force="strict").validate()

    def test_unknown_model_rejected(self):
        with pytest.raises(ValueError, match="must be one of"):
            ScreeningSpec(model="MACE").validate()

    def test_version_gate_is_truthful(self):
        assert isinstance(mdescriptor_predictors_available(), bool)


class TestVerdictMath:
    def test_bounds_reject_and_measure_only_passes(self):
        loose = _screen_with_fake(ScreeningSpec(max_energy_per_atom=5.0, max_force=5.0))
        strict_energy = _screen_with_fake(ScreeningSpec(max_energy_per_atom=0.5))
        strict_force = _screen_with_fake(ScreeningSpec(max_force=1.0))
        measure_only = _screen_with_fake(ScreeningSpec())
        candidates = [_candidate("a"), _candidate("b")]
        for screen, expected in (
            (loose, "pass"),
            (strict_energy, "fail"),
            (strict_force, "fail"),
            (measure_only, "pass"),
        ):
            verdicts = screen.screen(candidates)
            assert [v.status for v in verdicts] == [expected, expected]
        verdict = loose.screen(candidates)[0]
        assert verdict.energy_per_atom == pytest.approx(1.0)
        assert verdict.max_force == pytest.approx(2.0)
        assert verdict.energy == pytest.approx(2.0)
        assert measure_only.screen(candidates)[0].reasons == ()

    def test_partial_periodicity_is_unscreenable_not_rejected(self):
        screen = _screen_with_fake(ScreeningSpec(max_energy_per_atom=0.1))
        verdicts = screen.screen([_candidate("a", pbc=(True, False, True))])
        assert verdicts[0].status == "unscreenable"
        assert verdicts[0].accepted is True  # never a silent rejection
        assert "partial_periodicity_not_screenable" in verdicts[0].reasons


class TestEngineGate:
    class _OddFails:
        """Rejects every second selected candidate (by selection order)."""

        def screen(self, candidates):
            verdicts = []
            for index in range(len(candidates)):
                if index % 2 == 1:
                    verdicts.append(ScreeningVerdict(status="fail", reasons=("energy_above_bound",)))
                else:
                    verdicts.append(
                        ScreeningVerdict(status="pass", energy=-1.0, energy_per_atom=-0.5, max_force=0.1)
                    )
            return verdicts

    def _engine(self, screening, max_generations: int = 2) -> GenerationEngine:
        rng = np.random.default_rng(3)
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
        from mdescriptor_studio_backend.analysis.sampling import fit_scaling
        from mdescriptor_studio_backend.generation.archive import DescriptorArchive

        reference = np.stack([c.positions.mean(axis=0) for c in pool])
        scaling, _ = fit_scaling(reference, "robust")
        return GenerationEngine(
            seed_pool=pool,
            evaluator=type(
                "E",
                (),
                {
                    "evaluate": staticmethod(
                        lambda candidates, *, return_atomic=False, control=None: DescriptorEvaluation(
                            structure_values=np.stack([c.positions.mean(axis=0) for c in candidates]),
                            atomic_values=None,
                            row_offsets=None,
                        )
                    )
                },
            )(),
            structure_archive=DescriptorArchive(reference, scaling),
            local_archive=None,
            objective=NoveltyObjective(),
            optimizer=RandomSearchOptimizer([AtomicDisplacement(0.3)], children_per_seed=2, batch_accept=4),
            constraints=build_constraints({"min_distance_mode": "none"}),
            budget=Budget(max_evaluations=100, max_accepted=10**6, max_generations=max_generations),
            rng=np.random.default_rng(5),
            n_seeds=4,
            energy_screening=screening,
        )

    def test_screened_out_candidates_are_not_accepted_but_counted(self):
        engine = self._engine(self._OddFails())
        result = engine.run()
        screened_total = sum(r.rejected_screening for r in result.rounds)
        assert screened_total > 0
        assert all("rejected_screening" in r.to_json() for r in result.rounds)

    def test_without_screening_the_field_stays_zero(self):
        engine = self._engine(None)
        result = engine.run()
        assert sum(r.rejected_screening for r in result.rounds) == 0


class TestParseValidation:
    @staticmethod
    def _payload(**constraint_overrides) -> dict:
        constraints = {"min_distance_mode": "none"}
        constraints.update(constraint_overrides)
        return {
            "dataset_id": "ds",
            "descriptor_run_id": "run",
            "optimizer": "random",
            "optimizer_params": {"children_per_seed": 2, "batch_accept": 2, "n_seeds": 2},
            "objective": {"type": "novelty"},
            "operators": {"atomic_displacement": {"enabled": True, "max_sigma": 0.1}},
            "constraints": constraints,
            "budget": {"max_evaluations": 16},
            "seed": 42,
        }

    def test_disabled_screening_passes_through(self):
        from mdescriptor_studio_backend.generation.models import parse_request

        request = parse_request(self._payload(energy_screening={"enabled": False, "model": "MACE"}))
        assert request.constraints["energy_screening"]["enabled"] is False

    def test_unknown_key_rejected(self):
        from mdescriptor_studio_backend.errors import AppError, INVALID_PARAMS
        from mdescriptor_studio_backend.generation.models import parse_request

        with pytest.raises(AppError, match="unknown energy_screening keys"):
            parse_request(self._payload(energy_screening={"enabled": True, "mode": "fast"}))

    def test_dpa4c_checkpoint_required_at_parse(self):
        from mdescriptor_studio_backend.errors import AppError
        from mdescriptor_studio_backend.generation.models import parse_request

        with pytest.raises(AppError, match="checkpoint"):
            parse_request(self._payload(energy_screening={"enabled": True, "model": "DPA4C"}))

    def test_valid_config_parses(self):
        from mdescriptor_studio_backend.generation.models import parse_request

        request = parse_request(
            self._payload(
                energy_screening={
                    "enabled": True,
                    "model": "NEP",
                    "max_energy_per_atom": -0.5,
                    "max_force": 5.0,
                    "num_threads": 8,
                }
            )
        )
        assert request.constraints["energy_screening"]["model"] == "NEP"
