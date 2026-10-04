"""Benchmark harness contract tests (audit P0-03/P0-04).

The harness replicates the generation worker's assembly; these tests pin
the anchor-role split and the worker-consistent seed-pool assembly at stub
level — no descriptor engine, no app data.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

from mdescriptor_studio_backend.datasets.base import DatasetFrame

REPO = Path(__file__).resolve().parents[1]
_HARNESS_PATH = REPO / "benchmark" / "genetic_vs_random.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("benchmark_genetic_vs_random", _HARNESS_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("benchmark_genetic_vs_random", module)
    spec.loader.exec_module(module)
    return module


class _FakeAdapter:
    """Minimal frame adapter: frame i is one atom at (i, i, i)."""

    def __len__(self):
        return 8

    def get_frame(self, index):
        return DatasetFrame(
            numbers=np.array([14], dtype=np.int64),
            positions=np.array([[float(index)] * 3]),
            cell=np.eye(3) * 10.0,
            pbc=np.ones(3, dtype=bool),
            index=int(index),
        )


class TestAnchorRoles:
    def test_untargeted_labels_never_receive_search_anchors(self):
        # P0-03: a non-empty --anchor-frames value must not leak into the
        # engine of an untargeted baseline — proposals of "random" etc. may
        # never depend on measurement anchors.
        harness = _load_harness()
        metric_anchors = (np.zeros(3),)
        for label in ("random", "random-reuse", "genetic", "pso"):
            search_anchors, targeting = harness.search_anchor_role(label, metric_anchors)
            assert search_anchors == ()
            assert targeting is False

    def test_targeting_labels_search_with_the_anchors(self):
        harness = _load_harness()
        metric_anchors = (np.zeros(3), np.ones(3))
        for label in ("target_region", "genetic-target", "pso-target"):
            search_anchors, targeting = harness.search_anchor_role(label, metric_anchors)
            assert search_anchors == metric_anchors
            assert targeting is True

    def test_empty_metric_anchors_leave_everything_untargeted(self):
        harness = _load_harness()
        for label in ("random", "target_region", "genetic-target", "pso-target"):
            search_anchors, targeting = harness.search_anchor_role(label, ())
            assert search_anchors == ()
            assert targeting is (label in harness.TARGETING_LABELS)


class TestSeedPoolAssembly:
    def test_forced_anchors_match_the_worker_insertion(self):
        # services/generation_service.py: anchors (deduplicated, sorted)
        # go first, then the sampled indices minus the anchors, capped at
        # the pool size. Every benchmark group shares this identical pool.
        harness = _load_harness()
        pool = harness._seed_pool(_FakeAdapter(), 8, seed=3, force_anchors=[5, 1, 5])
        parent_frames = [candidate.parent_frame for candidate in pool]
        assert parent_frames[:2] == [1, 5]
        assert set(parent_frames) == set(range(8))
        assert len(parent_frames) == 8

    def test_without_forced_anchors_the_pool_is_the_plain_sampling(self):
        harness = _load_harness()
        pool = harness._seed_pool(_FakeAdapter(), 8, seed=3)
        parent_frames = [candidate.parent_frame for candidate in pool]
        assert sorted(parent_frames) == list(range(8))
        # Deterministic per seed, identical to the worker's rng(seed) path.
        again = [c.parent_frame for c in harness._seed_pool(_FakeAdapter(), 8, seed=3)]
        assert parent_frames == again

    def test_anchor_insertion_is_stable_across_seeds(self):
        harness = _load_harness()
        for seed in (0, 3, 11):
            pool = harness._seed_pool(_FakeAdapter(), 8, seed=seed, force_anchors=[2, 7])
            parent_frames = [candidate.parent_frame for candidate in pool]
            assert parent_frames[:2] == [2, 7]
            assert set(parent_frames) == set(range(8))


class TestPreregistration:
    def test_frozen_configs_are_rejected_for_rerun_after_a_version_bump(self):
        # The checked-in configs are historical gen-4 pre-registrations.
        # After the gen-5 bump the contract must refuse to rerun them —
        # that is exactly the drift pre-registration exists to surface
        # (improvement-plan F: a gen-5 freeze gets NEW pre-registrations,
        # the archived numbers keep their own caliber annotation).
        import json

        harness = _load_harness()
        assert harness.GENERATION_ALGORITHM_VERSION == "gen-5"
        for name in ("config.json", "config.local-selection.json", "config.pdcunip.json"):
            frozen = json.loads((REPO / "benchmark" / name).read_text(encoding="utf-8"))
            assert frozen["algorithm_version"] == "gen-4"
            with pytest.raises(SystemExit, match="algorithm_version"):
                harness._load_preregistration(REPO / "benchmark" / name)

    def test_restamped_preregistration_loads(self, tmp_path):
        harness = _load_harness()
        config = _current_preregistration("config.json", tmp_path)
        assert config["kind"] == "generation-benchmark-preregistration"
        assert config["selection_strategy"] == "structure_fps_v1"
        assert config["primary_metric"] == "unique_per_100_evals"

    def test_local_selection_config_loads(self, tmp_path):
        # The second pre-registration (selection-strategy comparison) must
        # validate against the same frozen scenario constants.
        harness = _load_harness()
        config = _current_preregistration("config.local-selection.json", tmp_path)
        assert config["selection_strategy"] == "local_incremental_maximin_v1"
        assert len(config["groups"]) == 7

    def test_diverging_scenario_is_rejected(self, tmp_path):
        import json

        harness = _load_harness()
        config = json.loads((REPO / "benchmark" / "config.json").read_text(encoding="utf-8"))
        config["objective"]["novelty_threshold"] = 0.5  # any silent drift
        config["algorithm_version"] = harness.GENERATION_ALGORITHM_VERSION
        path = tmp_path / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        with pytest.raises(SystemExit, match="diverges"):
            harness._load_preregistration(path)

    def test_missing_required_key_is_rejected(self, tmp_path):
        import json

        harness = _load_harness()
        path = _mutated_config(tmp_path, {})
        config = json.loads(path.read_text(encoding="utf-8"))
        del config["seed_base"]
        path.write_text(json.dumps(config), encoding="utf-8")
        with pytest.raises(SystemExit, match="seed_base"):
            harness._load_preregistration(path)

    def test_checksum_manifest_covers_result_files(self, tmp_path):
        import hashlib

        harness = _load_harness()
        (tmp_path / "run_results.jsonl").write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")
        (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
        harness._write_sha256sums(tmp_path, ["run_results.jsonl", "summary.json", "missing.json"])
        lines = (tmp_path / "SHA256SUMS").read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2  # missing files are skipped
        expected = hashlib.sha256((tmp_path / "run_results.jsonl").read_bytes()).hexdigest()
        assert lines[0].startswith(expected)


def _mutated_config(tmp_path: Path, mutations: dict) -> Path:
    import json

    harness = _load_harness()
    config = json.loads((REPO / "benchmark" / "config.json").read_text(encoding="utf-8"))
    # The checked-in config is a historical gen-4 pre-registration; the
    # contract clauses under test re-stamp the current version explicitly
    # (a version mutation overrides it again).
    config["algorithm_version"] = harness.GENERATION_ALGORITHM_VERSION
    config.update(mutations)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _current_preregistration(name: str, tmp_path: Path) -> dict:
    """A frozen repo config re-stamped for the CURRENT engine version.

    The checked-in configs are historical gen-4 pre-registrations; after a
    GENERATION_ALGORITHM_VERSION bump the contract rejects them for rerun.
    Tests exercising the other contract clauses re-stamp the version."""
    import json

    harness = _load_harness()
    config = json.loads((REPO / "benchmark" / name).read_text(encoding="utf-8"))
    config["algorithm_version"] = harness.GENERATION_ALGORITHM_VERSION
    path = tmp_path / name
    path.write_text(json.dumps(config), encoding="utf-8")
    return harness._load_preregistration(path)


class TestPreregistrationContract:
    """The 2026-10-01 external review showed the load-time checks accepted
    configs that only failed (or silently ran) later; the full registration
    contract is now enforced at load."""

    def test_pdcunip_config_pins_material_specific_anchors(self, tmp_path):
        harness = _load_harness()
        config = _current_preregistration("config.pdcunip.json", tmp_path)
        assert config["dataset_id"] == "ds_9ca89d14f8f0"
        # 2026-10-01 pre-run correction: the copied carbon indices are not
        # valid anchors on PdCuNiP (one fails geometry, one sits in the
        # dense core); the re-derived pair is pinned here.
        assert config["anchor_frames"] == [2256, 2133]
        assert config["metric_caliber"] == harness.METRIC_CALIBER
        assert "peak_rss_mb" in config["secondary_metrics"]
        assert config["max_accepted"] == harness.MAX_ACCEPTED
        assert config["max_generations"] == harness.MAX_GENERATIONS

    def test_wrong_metric_caliber_is_rejected(self, tmp_path):
        harness = _load_harness()
        path = _mutated_config(tmp_path, {"metric_caliber": "strict-unique-raw"})
        with pytest.raises(SystemExit, match="metric_caliber"):
            harness._load_preregistration(path)

    def test_missing_metric_caliber_is_rejected(self, tmp_path):
        import json

        harness = _load_harness()
        path = _mutated_config(tmp_path, {})
        config = json.loads(path.read_text(encoding="utf-8"))
        del config["metric_caliber"]
        path.write_text(json.dumps(config), encoding="utf-8")
        with pytest.raises(SystemExit, match="metric_caliber"):
            harness._load_preregistration(path)

    def test_unknown_primary_metric_is_rejected(self, tmp_path):
        harness = _load_harness()
        path = _mutated_config(tmp_path, {"primary_metric": "wall_seconds"})
        with pytest.raises(SystemExit, match="primary_metric"):
            harness._load_preregistration(path)

    def test_unknown_algorithm_version_is_rejected(self, tmp_path):
        harness = _load_harness()
        path = _mutated_config(tmp_path, {"algorithm_version": "unsupported"})
        with pytest.raises(SystemExit, match="algorithm_version"):
            harness._load_preregistration(path)

    def test_unknown_selection_strategy_is_rejected(self, tmp_path):
        harness = _load_harness()
        path = _mutated_config(tmp_path, {"selection_strategy": "invalid"})
        with pytest.raises(SystemExit, match="selection_strategy"):
            harness._load_preregistration(path)

    def test_zero_repeats_is_rejected(self, tmp_path):
        harness = _load_harness()
        path = _mutated_config(tmp_path, {"repeats": 0})
        with pytest.raises(SystemExit, match="repeats"):
            harness._load_preregistration(path)

    def test_unknown_group_is_rejected(self, tmp_path):
        harness = _load_harness()
        path = _mutated_config(tmp_path, {"groups": ["nonsense"]})
        with pytest.raises(SystemExit, match="groups"):
            harness._load_preregistration(path)

    def test_primary_group_outside_groups_is_rejected(self, tmp_path):
        harness = _load_harness()
        path = _mutated_config(tmp_path, {"primary_groups": ["nonsense"]})
        with pytest.raises(SystemExit, match="primary_groups"):
            harness._load_preregistration(path)

    def test_unknown_secondary_metric_is_rejected(self, tmp_path):
        harness = _load_harness()
        path = _mutated_config(tmp_path, {"secondary_metrics": ["not_a_metric"]})
        with pytest.raises(SystemExit, match="secondary_metrics"):
            harness._load_preregistration(path)

    def test_future_harness_min_version_is_rejected(self, tmp_path):
        harness = _load_harness()
        path = _mutated_config(tmp_path, {"harness_min_version": "2999-01-01"})
        with pytest.raises(SystemExit, match="harness_min_version"):
            harness._load_preregistration(path)

    def test_diverging_budget_cap_is_rejected(self, tmp_path):
        harness = _load_harness()
        path = _mutated_config(tmp_path, {"max_accepted": 999})
        with pytest.raises(SystemExit, match="max_accepted"):
            harness._load_preregistration(path)

    def test_missing_dataset_id_is_rejected(self, tmp_path):
        import json

        harness = _load_harness()
        path = _mutated_config(tmp_path, {})
        config = json.loads(path.read_text(encoding="utf-8"))
        del config["dataset_id"]
        path.write_text(json.dumps(config), encoding="utf-8")
        with pytest.raises(SystemExit, match="dataset_id"):
            harness._load_preregistration(path)

    def test_apply_preregistration_resolves_the_material(self, tmp_path):
        harness = _load_harness()
        config = _current_preregistration("config.pdcunip.json", tmp_path)
        params = harness.apply_preregistration(config)
        assert harness.DATASET_ID == "ds_9ca89d14f8f0"
        assert harness.RUN_ID == "run_644f6186340c"
        assert params["anchor_frames"] == [2256, 2133]
        assert params["budget"] == 10000


def _load_resume():
    import sys

    sys.path.insert(0, str(REPO / "benchmark"))
    spec = importlib.util.spec_from_file_location("benchmark_resume_sweep", REPO / "benchmark" / "resume_sweep.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("benchmark_resume_sweep", module)
    spec.loader.exec_module(module)
    return module


class TestResumeMaterialGuard:
    """The resume path must never extend a sweep with another material's
    rows (the 2026-10-01 review: resuming a second-material sweep appended
    default-carbon rows because the module ids were never applied)."""

    def _harness_and_resume(self):
        harness = _load_harness()
        return harness, _load_resume()

    def test_used_config_divergence_is_refused(self, tmp_path):
        import json

        harness, resume = self._harness_and_resume()
        config = _current_preregistration("config.pdcunip.json", tmp_path)
        (tmp_path / "config.used.json").write_text(
            json.dumps(config | {"dataset_id": "ds_d56748fb4391"}), encoding="utf-8"
        )
        with pytest.raises(SystemExit, match="diverges"):
            resume._verify_used_config(config, tmp_path / "config.used.json")

    def test_used_config_tolerates_later_annotations(self, tmp_path):
        # A sweep started before metric_caliber existed must still resume:
        # keys added to the pre-registration afterwards are annotations, not
        # scenario changes; the free-text note is excluded as well.
        import json

        harness, resume = self._harness_and_resume()
        config = _current_preregistration("config.json", tmp_path)
        used = {k: v for k, v in config.items() if k not in ("metric_caliber",)}
        used["note"] = used["note"] + " 2026-10-01 caliber annotation ..."
        (tmp_path / "config.used.json").write_text(json.dumps(used), encoding="utf-8")
        resume._verify_used_config(config, tmp_path / "config.used.json")

    def test_rows_of_another_material_are_refused(self, tmp_path):
        harness, resume = self._harness_and_resume()
        config = _current_preregistration("config.pdcunip.json", tmp_path)
        rows = [{"optimizer": "random", "seed": 1000, "dataset_id": "ds_d56748fb4391", "descriptor_run_id": "run_57a8b8c40286"}]
        with pytest.raises(SystemExit, match="mixed-material"):
            resume._verify_row_identities(rows, config)

    def test_matching_rows_pass(self, tmp_path):
        harness, resume = self._harness_and_resume()
        config = _current_preregistration("config.pdcunip.json", tmp_path)
        rows = [{"optimizer": "random", "seed": 1000, "dataset_id": "ds_9ca89d14f8f0", "descriptor_run_id": "run_644f6186340c"}]
        resume._verify_row_identities(rows, config)

    def test_legacy_rows_without_identity_only_fit_the_default_experiment(self, tmp_path):
        harness, resume = self._harness_and_resume()
        rows = [{"optimizer": "random", "seed": 1000}]
        carbon = _current_preregistration("config.json", tmp_path)
        resume._verify_row_identities(rows, carbon)  # default carbon: admissible
        pdcunip = _current_preregistration("config.pdcunip.json", tmp_path)
        with pytest.raises(SystemExit, match="predates material identity"):
            resume._verify_row_identities(rows, pdcunip)
