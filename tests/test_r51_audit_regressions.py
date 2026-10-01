"""R5.1 audit regressions (2026-10-01 external audit, main 8ed4bdb).

Each test pins one audited defect (writer state persistence, discovery
counting, PSO parent memory, snapshot measurements, capability gate,
checkpoint validation, NaN defense) or one audited-correct behavior.
Tests that
need the real mdescriptor predictor wheel (>= 0.3.5) skip on older wheels
via importorskip — the dev environment pins 0.3.4 on purpose (upgrade is
deferred until the running sweeps finish).
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from test_generation_screening import _candidate, _screen_with_fake, TestEngineGate as _EngineFixture
from test_generation_artifacts import _run_result, _commit
from test_generation_pso import _optimizer, _context, _obs
from mdescriptor_studio_backend.generation.screening import EnergyForceScreen, ScreeningSpec, ScreeningVerdict
from mdescriptor_studio_backend.generation.optimization import ObservationBatch


def test_nonfinite_prediction_must_not_pass():
    screen = _screen_with_fake(ScreeningSpec(max_energy_per_atom=0, max_force=1))
    screen._predictor._epa = np.nan
    screen._predictor._force = np.nan
    verdict = screen.screen([_candidate()])[0]
    assert verdict.status != "pass"


def test_screened_header_and_measurements_survive_commit(tmp_path):
    result = _run_result(1)
    result.evaluations[0] = replace(result.evaluations[0], energy=-2.0, energy_per_atom=-1.0, max_force=0.1)
    _, final, _ = _commit(tmp_path, result)
    text = (final / "accepted.extxyz").read_text()
    assert "energy_screened=true" in text
    assert "energy_screen_pass=true" in text
    assert "energy_per_atom=" in text


def test_pso_excludes_screening_failure_from_parent_memory():
    optimizer = _optimizer(children_per_seed=1)
    optimizer.initialize(_context(n_seeds=1))
    child = optimizer.propose(budget=1, rng=np.random.default_rng(3)).candidates[0]
    obs = replace(_obs(child, fitness=10), screening_rejection=("force_above_bound",))
    optimizer.observe(ObservationBatch(generation=1, observations=[obs]))
    assert optimizer._particles[0].pbest is not child
    assert optimizer._particles[0].position is not child


class _RejectAll:
    def screen(self, candidates):
        return [ScreeningVerdict("fail", reasons=("force_above_bound",)) for _ in candidates]


def test_discovered_but_rejected_environments_count_without_archiving():
    from mdescriptor_studio_backend.analysis.sampling import fit_scaling
    from mdescriptor_studio_backend.generation.archive import LocalEnvironmentArchive
    from mdescriptor_studio_backend.generation.objectives.local_diversity import LocalEnvironmentNoveltyObjective
    from mdescriptor_studio_backend.generation.evaluator import DescriptorEvaluation

    engine = _EngineFixture()._engine(_RejectAll(), max_generations=1)
    reference = np.zeros((1, 3))
    scaling, _ = fit_scaling(reference, "raw")
    engine.local_archive = LocalEnvironmentArchive(reference, scaling)
    engine.objective = LocalEnvironmentNoveltyObjective(novelty_threshold=0.1)

    def evaluate(candidates, **kwargs):
        n = len(candidates)
        atomic = np.arange(1, 2 * n + 1, dtype=float)[:, None] * np.ones((1, 3))
        return DescriptorEvaluation(
            np.stack([c.positions.mean(axis=0) for c in candidates]),
            atomic,
            np.arange(0, 2 * n + 1, 2),
        )

    engine.evaluator = SimpleNamespace(evaluate=evaluate)
    result = engine.run()
    assert result.rounds[0].rejected_screening > 0
    assert engine.structure_archive.size == engine.local_archive.size == 0
    assert not result.accepted
    assert result.rounds[0].unique_novel_environments > 0


def test_screening_measurements_survive_final_snapshot_resume(tmp_path):
    engine = _EngineFixture()._engine(_EngineFixture._OddFails(), max_generations=1)
    result = engine.run()
    expected = result.evaluations[0].energy
    assert expected is not None
    engine.write_snapshot(tmp_path)
    resumed = _EngineFixture()._engine(_EngineFixture._OddFails(), max_generations=1)
    resumed.restore_state(tmp_path)
    assert resumed.run().evaluations[0].energy == expected


@pytest.mark.parametrize("model", ["NEP", "DPA4C"])
def test_real_bundled_and_explicit_checkpoint_resolution(model):
    pytest.importorskip("mdescriptor.predictors")
    default = EnergyForceScreen(ScreeningSpec(model=model))
    resolved = default._predictor.resolved_model
    assert resolved.source == "package"
    explicit = EnergyForceScreen(ScreeningSpec(model=model, checkpoint=str(resolved.path)))
    assert explicit._predictor.resolved_model.digest == resolved.digest
    a, b = default.screen([_candidate()])[0], explicit.screen([_candidate()])[0]
    assert a == b


def test_mixed_partial_periodicity_preserves_verdict_alignment():
    screen = _screen_with_fake(ScreeningSpec(max_energy_per_atom=0.5))
    candidates = [_candidate("partial", pbc=(1, 0, 1)), _candidate("full"), _candidate("cluster", pbc=(0, 0, 0))]
    assert [v.status for v in screen.screen(candidates)] == ["unscreenable", "fail", "fail"]


def test_capability_gate_checks_native_predictor(monkeypatch):
    pytest.importorskip("mdescriptor.predictors")
    import mdescriptor._native as native
    from mdescriptor_studio_backend.generation.screening import mdescriptor_predictors_available

    monkeypatch.delattr(native, "Dpa4cPredictor")
    assert not mdescriptor_predictors_available()


def test_explicit_checkpoint_must_be_a_path():
    with pytest.raises(ValueError, match="checkpoint"):
        ScreeningSpec.from_constraints({"energy_screening": {"enabled": True, "checkpoint": 42}})


def test_real_engine_rejects_nan_before_adapter():
    # Adapter-only NaN failure is a defensive gap, not reachable through
    # the official 0.3.5 PredictionResult contract.
    pytest.importorskip("mdescriptor.predictors")
    from mdescriptor.core.prediction_result import PredictionResult

    with pytest.raises(ValueError, match="finite"):
        PredictionResult(np.array([np.nan]), np.zeros(2), np.zeros((2, 3)), ("c",), np.array([0, 2]))


def test_old_wheel_without_predictors_rejected(monkeypatch):
    import importlib.util
    from mdescriptor_studio_backend.generation.screening import mdescriptor_predictors_available

    original = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: None if name == "mdescriptor.predictors" else original(name)
    )
    assert not mdescriptor_predictors_available()
    with pytest.raises(ValueError, match="0.3.5"):
        EnergyForceScreen(ScreeningSpec())


def test_exact_bounds_pass_and_prediction_failure_is_not_unscreenable():
    screen = _screen_with_fake(ScreeningSpec(max_energy_per_atom=1, max_force=2))
    assert screen.screen([_candidate()])[0].status == "pass"

    def broken(batch):
        raise RuntimeError("predictor unavailable")

    screen._predictor.predict = broken
    with pytest.raises(RuntimeError, match="unavailable"):
        screen.screen([_candidate()])
