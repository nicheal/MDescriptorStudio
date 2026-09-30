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
    def test_repo_config_loads_and_matches_the_frozen_scenario(self):
        # The checked-in benchmark/config.json must always validate against
        # the harness constants — drift is exactly what pre-registration
        # exists to surface.
        harness = _load_harness()
        config = harness._load_preregistration(REPO / "benchmark" / "config.json")
        assert config["kind"] == "generation-benchmark-preregistration"
        assert config["selection_strategy"] == "structure_fps_v1"
        assert config["primary_metric"] == "unique_per_100_evals"

    def test_local_selection_config_loads(self):
        # The second pre-registration (selection-strategy comparison) must
        # validate against the same frozen scenario constants.
        harness = _load_harness()
        config = harness._load_preregistration(REPO / "benchmark" / "config.local-selection.json")
        assert config["selection_strategy"] == "local_incremental_maximin_v1"
        assert len(config["groups"]) == 7

    def test_diverging_scenario_is_rejected(self, tmp_path):
        import json

        harness = _load_harness()
        config = json.loads((REPO / "benchmark" / "config.json").read_text(encoding="utf-8"))
        config["objective"]["novelty_threshold"] = 0.5  # any silent drift
        path = tmp_path / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        with pytest.raises(SystemExit, match="diverges"):
            harness._load_preregistration(path)

    def test_missing_required_key_is_rejected(self, tmp_path):
        import json

        harness = _load_harness()
        config = json.loads((REPO / "benchmark" / "config.json").read_text(encoding="utf-8"))
        del config["seed_base"]
        path = tmp_path / "config.json"
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
