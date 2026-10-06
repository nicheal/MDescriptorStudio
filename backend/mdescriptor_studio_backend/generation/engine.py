"""The generation iteration loop.

One round, in order (the order is a performance contract):

    optimizer.propose → geometry filter (cheap)
    → duplicate filter (frozen archive) → ONE batch descriptor evaluation
    → objective scoring (frozen archive) → greedy max-min batch selection
    → archive update exactly once → optimizer.observe → stop check

Never: per-candidate descriptor computes, per-candidate archive updates, or
re-fitting the scaling inside the loop.

The optimizer lifecycle (G3.5): initialize → propose → observe → state. The
engine reports every proposed candidate's outcome back through ``observe``
(fitness, novelty, acceptance, geometry rejection) so feedback-driven
optimizers (GA/PSO) can steer the next round; Random ignores the feedback.

Module layout (2026-10-06 architecture split, zero behavior): the strict
unique-environment counters live in ``counting.py``, the batch selection
strategies in ``selection.py``, the round/result records in ``records.py``;
this module re-exports them so existing import sites are unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from pathlib import Path

import numpy as np

from ..analysis.sampling import apply_scaling
from ..errors import AppError, JOB_CANCELLED
from .registry import GENERATION_ALGORITHM_VERSION
from .snapshot import durable_write, fsync_directory, require_keys
from .optimization import CandidateObservation, ObservationBatch, OptimizationContext
from .evaluator import DescriptorEvaluator
from .models import (
    ArchiveEntry,
    Budget,
    CandidateEvaluation,
    ConstraintResult,
    StructureCandidate,
)

from .counting import (
    count_strict_unique_environments,
    count_strict_unique_environments_v2,
)
from .records import EvaluatedRecord, GenerationRunResult, RoundRecord
from .selection import (
    SELECTION_STRATEGIES,
    select_diverse_batch,
    select_local_incremental_batch,
)

_GEOMETRY_REJECTION_CODES = {
    "positions contain NaN or Inf": "non_finite_positions",
    "cell contains NaN or Inf": "non_finite_cell",
    "periodic cell is singular": "singular_periodic_cell",
    "periodic cell is too skewed to bound the contact search": "periodic_cell_too_skewed",
    "structure contains no atoms": "empty_structure",
    "interatomic contact below the minimum distance": "minimum_distance",
    "composition differs from the parent structure": "composition_lock",
    "atom count differs from the parent structure": "atom_count_lock",
}

# v2 (audit R5.5 hardening): the manifest references uniquely named, hashed
# data files instead of overwriting candidates.json/evaluations.json in
# place, records counts + a run-configuration fingerprint, and optimizer
# states carry strict required-field validation. v1 snapshots are rejected
# on load.
# v3 (resume productization, 2026-10-02): the evaluated-candidate records
# (the descriptor-space map) join the snapshot as a third data file — the
# artifact writer streams them to a spool file that a crash destroys, so a
# resumed run would otherwise republish a partial map. v2 snapshots are
# rejected on load.
SNAPSHOT_VERSION = 3

# Manifest-referenced data file names: a strict single-segment pattern so a
# hand-edited manifest cannot point outside the snapshot directory.
_DATA_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.json")

_ROUND_RECORD_KEYS = (
    "generation",
    "evaluations",
    "proposed",
    "rejected_geometry",
    "rejected_geometry_by_reason",
    "rejected_duplicate",
    "accepted",
    "best_fitness",
    "best_novelty",
    "mean_novelty",
    "coverage_radius",
    "novel_environments",
    "unique_novel_environments",
    "archived_unique_novel_environments",
    "rejected_screening",
)

_EVALUATION_KEYS = (
    "candidate_id",
    "structure_descriptor",
    "atomic_descriptors",
    "novelty",
    "local_diversity",
    "fitness",
    "novel_environment_count",
    "atom_count",
    "energy",
    "energy_per_atom",
    "max_force",
    "screening_status",
    "screening_reasons",
)


def _encode_rng_state(state):
    """JSON-safe encoding of a bit-generator state (audit R5.5).

    PCG64's state is plain Python integers, but MT19937, Philox and SFC64
    carry ndarray leaves that json.dumps cannot serialize; every ndarray
    becomes a tagged {dtype, data} record restored bit-exactly on load.
    """
    if isinstance(state, np.ndarray):
        return {"__ndarray__": {"dtype": state.dtype.str, "data": state.tolist()}}
    if isinstance(state, dict):
        return {key: _encode_rng_state(value) for key, value in state.items()}
    if isinstance(state, (list, tuple)):
        return [_encode_rng_state(value) for value in state]
    return state


def _decode_rng_state(state):
    if isinstance(state, dict) and set(state) == {"__ndarray__"}:
        payload = state["__ndarray__"]
        return np.asarray(payload["data"], dtype=np.dtype(payload["dtype"]))
    if isinstance(state, dict):
        return {key: _decode_rng_state(value) for key, value in state.items()}
    if isinstance(state, list):
        return [_decode_rng_state(value) for value in state]
    return state


def _rng_from_state(state) -> np.random.Generator:
    """Rebuild a Generator from a persisted bit-generator state (R5.5).

    The bit-generator name is validated against numpy's BitGenerator types —
    an arbitrary np.random attribute must never be instantiated from file
    content — and ndarray leaves are decoded before the state assignment.
    """
    name = state.get("bit_generator") if isinstance(state, dict) else None
    bit_generator_cls = getattr(np.random, name, None) if isinstance(name, str) else None
    if not (isinstance(bit_generator_cls, type) and issubclass(bit_generator_cls, np.random.BitGenerator)):
        raise ValueError(f"snapshot references unknown bit generator: {name!r}")
    bit_generator = bit_generator_cls()
    try:
        bit_generator.state = _decode_rng_state(state)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"snapshot bit-generator state is not loadable: {exc}") from exc
    return np.random.Generator(bit_generator)


def _candidate_record(candidate: StructureCandidate) -> dict:
    return {
        "candidate_id": candidate.candidate_id,
        "atomic_numbers": np.asarray(candidate.atomic_numbers).tolist(),
        "positions": np.asarray(candidate.positions, dtype=np.float64).tolist(),
        "cell": np.asarray(candidate.cell, dtype=np.float64).tolist(),
        "pbc": np.asarray(candidate.pbc, dtype=bool).tolist(),
        "parent_frame": candidate.parent_frame,
        "parent_candidate_id": candidate.parent_candidate_id,
        "generation": candidate.generation,
        "operator": candidate.operator,
        "operator_params": dict(candidate.operator_params or {}),
        "metadata": {k: (v.tolist() if isinstance(v, np.ndarray) else v) for k, v in (candidate.metadata or {}).items()},
    }


def _candidate_from_record(record: dict) -> StructureCandidate:
    return StructureCandidate(
        candidate_id=record["candidate_id"],
        atomic_numbers=np.asarray(record["atomic_numbers"], dtype=np.int64),
        positions=np.asarray(record["positions"], dtype=np.float64),
        cell=np.asarray(record["cell"], dtype=np.float64),
        pbc=np.asarray(record["pbc"], dtype=bool),
        parent_frame=record["parent_frame"],
        parent_candidate_id=record["parent_candidate_id"],
        generation=int(record["generation"]),
        operator=record["operator"],
        operator_params=dict(record.get("operator_params") or {}),
        metadata=dict(record.get("metadata") or {}),
    )


def _objective_fingerprint(objective) -> dict:
    """The objective's scoring-relevant configuration, for the snapshot
    fingerprint. CompositeObjective's inner local objective carries the
    aggregation knobs, so it is folded in via the private attribute."""
    payload = {"class": type(objective).__name__}
    for attr in (
        "structure_weight",
        "local_weight",
        "aggregation",
        "top_fraction",
        "quantile",
        "novel_environment_threshold",
        "novelty_threshold",
    ):
        value = getattr(objective, attr, None)
        if value is not None:
            payload[attr] = value
    inner = getattr(objective, "_local", None)
    if inner is not None and inner is not objective:
        payload["local"] = _objective_fingerprint(inner)
    return payload


def _archive_fingerprint(archive) -> dict:
    """Frozen scaling plus a content hash of the scaled reference — any
    change to the reference set or to the scaling transform invalidates a
    snapshot taken against the old archive."""
    scaling = archive.scaling
    reference = np.ascontiguousarray(archive.reference, dtype=np.float64)
    return {
        "mode": scaling.mode,
        "center": np.asarray(scaling.center, dtype=np.float64).tolist(),
        "scale": np.asarray(scaling.scale, dtype=np.float64).tolist(),
        "reference_shape": list(reference.shape),
        "reference_sha256": hashlib.sha256(reference.tobytes()).hexdigest(),
    }


def _screening_fingerprint(screening) -> dict | None:
    if screening is None:
        return None
    payload = {"class": type(screening).__name__}
    spec = getattr(screening, "spec", None)
    if spec is not None and is_dataclass(spec):
        payload["spec"] = asdict(spec)
    identity = getattr(screening, "model_identity", None)
    # Content identity of the loaded model (2026-10-02 audit P1): the spec's
    # checkpoint PATH cannot tell an in-place file replacement apart.
    if callable(identity):
        payload["model_identity"] = identity()
    return payload


def _constraints_fingerprint(constraints) -> dict:
    """Normalized geometry-constraints fingerprint (2026-10-02 audit P0).

    The minimum-distance mode/value/factor, pair cutoffs, displacement and
    volume bounds and the composition/atom-count locks decide which proposals
    survive the geometry filter — resuming under different physics must be
    rejected like any other run-definition change. The pair cutoff matrix is
    a (max_Z+1)² ndarray, so it enters as shape + content hash."""
    payload = asdict(constraints) if is_dataclass(constraints) else dict(constraints)
    matrix = payload.get("pair_cutoff_matrix")
    if matrix is not None:
        matrix = np.ascontiguousarray(matrix, dtype=np.float64)
        payload["pair_cutoff_matrix"] = {
            "shape": list(matrix.shape),
            "sha256": hashlib.sha256(matrix.tobytes()).hexdigest(),
        }
    return payload


def _geometry_rejection_code(reason: str | None) -> str:
    if reason is None:
        return "unknown"
    if reason.startswith("displacement "):
        return "displacement_limit"
    if reason.startswith("volume change "):
        return "volume_change_limit"
    if reason.startswith("volume per atom "):
        return "volume_per_atom_range"
    return _GEOMETRY_REJECTION_CODES.get(reason, "other")


def _finite_or_none(values: np.ndarray | None, index: int) -> float | None:
    if values is None:
        return None
    value = float(values[index])
    return value if np.isfinite(value) else None


class GenerationEngine:
    def __init__(
        self,
        *,
        seed_pool: list,
        evaluator: DescriptorEvaluator,
        structure_archive,
        local_archive,
        objective,
        optimizer,
        constraints,
        budget: Budget,
        rng: np.random.Generator,
        n_seeds: int = 64,
        duplicate_threshold: float | None = None,
        workers: int = 1,
        seed_descriptors: tuple = (),
        anchor_descriptors: tuple = (),
        region_radius: float | None = None,
        selection_strategy: str = "structure_fps_v1",
        local_anchor_descriptors: tuple = (),
        seed_local_distances: tuple = (),
        energy_screening=None,
    ) -> None:
        if not seed_pool:
            raise ValueError("seed pool must not be empty")
        if selection_strategy not in SELECTION_STRATEGIES:
            raise ValueError(f"selection_strategy must be one of {', '.join(SELECTION_STRATEGIES)}")
        if selection_strategy == "local_incremental_maximin_v1" and (
            local_archive is None or getattr(objective, "novel_environment_threshold", None) is None
        ):
            # Fail fast: the strategy scores candidates in environment space,
            # so it is meaningless without atom rows and a novelty threshold.
            raise ValueError(
                "local_incremental_maximin_v1 requires a local-environment archive and a novelty threshold"
            )
        self.selection_strategy = selection_strategy
        # Resume support (audit R5.5): populated by restore_state(), consumed
        # (and cleared) at the top of run(). restore_state() validates the
        # whole snapshot and prepares this payload transactionally; run()
        # only applies it.
        self._resume: dict | None = None
        self._resume_candidates: dict | None = None
        self._active_result: GenerationRunResult | None = None
        # The run definition is fixed at construction, so the fingerprint is
        # computed once and reused by every snapshot write / restore check.
        self._config_fingerprint_cache: str | None = None
        self.seed_pool = list(seed_pool)
        # Candidate-identity invariant (2026-10-02 audit P0): proposal ids
        # must be unique across the whole run — a collision silently collapses
        # two distinct structures in the snapshot's candidate table and
        # misdirects lineage, accepted_order and optimizer memory. Operators
        # mint ids from parent ids plus operator-specific suffixes; the engine
        # is the one place that sees every id ever issued, so it renames a
        # colliding proposal in place (object identity preserved — GA/PSO
        # keep their id()-keyed parent maps) before it reaches any state a
        # snapshot persists.
        seed_ids = [candidate.candidate_id for candidate in self.seed_pool]
        if len(set(seed_ids)) != len(seed_ids):
            raise ValueError("seed pool contains duplicate candidate ids")
        self._issued_candidate_ids: set = set(seed_ids)
        self.evaluator = evaluator
        self.structure_archive = structure_archive
        self.local_archive = local_archive
        self.objective = objective
        self.optimizer = optimizer
        self.constraints = constraints
        self.budget = budget
        self.rng = rng
        self.n_seeds = int(n_seeds)
        self.duplicate_threshold = duplicate_threshold
        self.workers = max(1, int(workers))
        # Scaled structure descriptors for the seed pool / user anchors —
        # forwarded into the context for descriptor-aware optimizers
        # (the random optimizer's search-target mode); the engine itself never uses them.
        self.seed_descriptors = tuple(seed_descriptors)
        self.anchor_descriptors = tuple(anchor_descriptors)
        self.region_radius = None if region_radius is None else float(region_radius)
        # Local-environment targeting (audit R3.4): the anchor frames' scaled
        # atom rows plus per-seed distances to them. Only populated for
        # target_mode="local_environment" on atom-level runs.
        self.local_anchor_descriptors = tuple(local_anchor_descriptors)
        self.seed_local_distances = tuple(seed_local_distances)
        # Second-stage energy/force screening (audit R5.1): any object with
        # screen(candidates) -> list[ScreeningVerdict]; None disables it.
        if energy_screening is not None and not hasattr(energy_screening, "screen"):
            raise ValueError("energy_screening must provide screen(candidates)")
        self.energy_screening = energy_screening

    def _check_geometry(self, candidate: StructureCandidate) -> ConstraintResult:
        return self.constraints.validate(candidate)

    def _register_proposal_ids(self, children: list[StructureCandidate]) -> list[StructureCandidate]:
        """Enforce the candidate-id uniqueness invariant on a fresh batch.

        A colliding proposal is renamed in place (``<id>_x<k>``, object
        identity preserved) instead of dropped: dropping would silently
        shrink the optimizer's batch and consume a different number of
        descriptor evaluations than the operator intended. The rename keeps
        the operator-minted lineage readable (the original id is a prefix)."""
        issued = self._issued_candidate_ids
        for candidate in children:
            candidate_id = candidate.candidate_id
            if candidate_id not in issued:
                issued.add(candidate_id)
                continue
            suffix = 1
            while f"{candidate_id}_x{suffix}" in issued:
                suffix += 1
            renamed = f"{candidate_id}_x{suffix}"
            candidate.candidate_id = renamed
            issued.add(renamed)
        return children

    def _count_unique_novel_environments(
        self,
        selected: list[int],
        atomic_values: np.ndarray,
        row_offsets: np.ndarray,
        threshold: float,
    ) -> int:
        return count_strict_unique_environments(selected, atomic_values, row_offsets, threshold, self.local_archive)

    def _count_unique_novel_environments_v2(
        self,
        selected: list[int],
        candidate_ids: list[str],
        atomic_values: np.ndarray,
        row_offsets: np.ndarray,
        threshold: float,
    ) -> int:
        return count_strict_unique_environments_v2(
            selected, candidate_ids, atomic_values, row_offsets, threshold, self.local_archive
        )

    def run(self, check_cancelled=None, progress=None, on_round=None, on_evaluated=None) -> GenerationRunResult:
        check_cancelled = check_cancelled or (lambda: None)
        result = GenerationRunResult()
        best_fitness = -np.inf
        stagnant = 0
        total_evaluations = 0
        generation = 0
        self._active_result = result
        needs_atomic = bool(getattr(self.objective, "needs_atomic", False))
        # The discovery-rate stop measures novel *environments* per 100
        # descriptor evaluations. Objectives that never produce that metric
        # (structure novelty, coverage) must not be terminated by it — a
        # zero default would otherwise fake saturation from round `window`
        # on and stop productive runs prematurely.
        counts_environments = bool(getattr(self.objective, "produces_novel_environment_count", False))
        self.optimizer.initialize(
            OptimizationContext(
                seed_pool=tuple(self.seed_pool),
                n_seeds=int(self.n_seeds),
                budget=self.budget,
                seed_descriptors=self.seed_descriptors,
                anchor_descriptors=self.anchor_descriptors,
                region_radius=self.region_radius,
                local_anchor_descriptors=self.local_anchor_descriptors,
                seed_local_distances=self.seed_local_distances,
            )
        )
        if self._resume is not None:
            # Continue an interrupted run (audit R5.5). restore_state() has
            # already validated and prepared everything — the optimizer's
            # pools/genomes/memory (its load_state runs after the fresh
            # initialize() above, which rebuilt the context and reset the
            # optimizer), the decoded RNG, the replayed result history
            # (rounds, accepted candidates, evaluations with their screening
            # verdicts) and the round-boundary mirrors.
            resume, candidates_by_id = self._resume, self._resume_candidates
            self._resume = None
            self._resume_candidates = None
            self.optimizer.load_state(resume["optimizer"], candidates_by_id)
            self.rng = resume["rng"]
            generation = resume["generation"]
            total_evaluations = resume["total_evaluations"]
            stagnant = resume["stagnant"]
            best_fitness = -np.inf if resume["best_fitness"] is None else float(resume["best_fitness"])
            # Mirror the restored round-boundary bookkeeping into the fresh
            # result even when no new round runs (fixpoint): a budget-
            # exhausted resume must still report — and re-snapshot — the
            # restored values, not the fresh defaults.
            result.best_fitness = resume["best_fitness"]
            result.stagnant = stagnant
            result.rounds = resume["rounds"]
            result.accepted = resume["accepted"]
            result.evaluations = resume["evaluations"]
            result.evaluated = resume["evaluated"]
            # The scientific stops are decided AFTER on_round — i.e. after
            # the snapshot was taken — so a restored run must re-derive them
            # from the replayed history instead of silently running a round
            # the original never ran (R5.5: target/discovery stops gained a
            # round on resume). Raising a stop threshold re-enables the run
            # deliberately; extending a budget alone does not.
            if self._discovery_saturated(result.rounds, generation, counts_environments):
                result.stopped_by = "discovery_saturated"
                return result
            if self._screening_bottleneck(result.rounds, generation, counts_environments):
                result.stopped_by = "screening_bottleneck"
                return result
            last_round = result.rounds[-1] if result.rounds else None
            if (
                self.budget.target_novelty is not None
                and last_round is not None
                and last_round.best_novelty is not None
                and last_round.best_novelty >= self.budget.target_novelty
            ):
                result.stopped_by = "target_novelty"
                return result

        while True:
            try:
                check_cancelled()
            except AppError as exc:
                if exc.code != JOB_CANCELLED:
                    raise
                result.stopped_by = "cancelled"
                break
            if generation >= self.budget.max_generations:
                result.stopped_by = "max_generations"
                break
            if total_evaluations >= self.budget.max_evaluations:
                result.stopped_by = "max_evaluations"
                break
            if len(result.accepted) >= self.budget.max_accepted:
                result.stopped_by = "max_accepted"
                break
            if self.budget.no_improvement_rounds is not None and stagnant >= self.budget.no_improvement_rounds:
                result.stopped_by = "no_improvement"
                break

            generation += 1
            proposal = self.optimizer.propose(
                budget=max(1, int(self.budget.max_evaluations - total_evaluations)),
                rng=self.rng,
            )
            children = self._register_proposal_ids(list(proposal.candidates))

            verdicts: list[ConstraintResult] = []
            if self.workers > 1 and len(children) > 1:
                with ThreadPoolExecutor(max_workers=min(self.workers, len(children))) as pool:
                    verdicts = list(pool.map(self._check_geometry, children))
            else:
                verdicts = [self._check_geometry(child) for child in children]

            valid: list[StructureCandidate] = []
            rejected_geometry_by_reason: dict[str, int] = {}
            for child, verdict in zip(children, verdicts):
                if verdict.valid:
                    valid.append(child)
                    continue
                # A candidate may violate multiple checks; count its first
                # reported reason so the buckets sum to rejected_geometry.
                reason = verdict.reasons[0] if verdict.reasons else None
                code = _geometry_rejection_code(reason)
                rejected_geometry_by_reason[code] = rejected_geometry_by_reason.get(code, 0) + 1
            rejected_geometry = sum(rejected_geometry_by_reason.values())
            # The evaluation budget counts descriptor calls, not proposals.
            # A full final batch could otherwise exceed the requested cap.
            del valid[max(0, self.budget.max_evaluations - total_evaluations) :]

            # Duplicate filtering needs descriptors, so it runs after scoring
            # (below), against the frozen archive.
            rejected_duplicate = 0
            # Energy/force screening (R5.1) state for this round; the gate
            # below only runs when candidates reached evaluation.
            rejected_screening = 0
            screening_verdicts: dict[int, object] = {}
            screening_reasons: dict[int, tuple[str, ...]] = {}

            evaluations_this_round = 0
            accepted_this_round = 0
            best_round_fitness = -np.inf
            best_round_novelty = None
            mean_round_novelty = None
            novel_environments = 0
            unique_novel_environments: int | None = None
            archived_unique_novel_environments: int | None = None
            strict_unique_v2: int | None = None
            archived_strict_unique_v2: int | None = None
            # Screening verdict breakdown (2026-10-02 audit D); the gate below
            # only runs when candidates reached evaluation, so a round without
            # screening — or with none reaching it — keeps all three at zero.
            screening_passed = 0
            screening_unscreenable = 0
            screening_train_ready = 0

            if valid:
                try:
                    evaluation = self.evaluator.evaluate(valid, return_atomic=needs_atomic)
                except AppError as exc:
                    if exc.code != JOB_CANCELLED:
                        raise
                    result.stopped_by = "cancelled"
                    return result
                evaluations_this_round = len(valid)
                total_evaluations += evaluations_this_round
                penalties = np.zeros(len(valid), dtype=np.float64)
                scores = self.objective.evaluate_batch(
                    evaluation.structure_values,
                    evaluation.atomic_values,
                    evaluation.row_offsets,
                    self.structure_archive,
                    self.local_archive,
                    penalties,
                )

                fitness = np.asarray(scores.fitness, dtype=np.float64)
                # Post-hoc duplicate rejection against the frozen archive.
                if self.duplicate_threshold is not None:
                    # Novelty, composite, and coverage objectives already
                    # measured these exact distances against this unchanged
                    # archive. Reuse them instead of scanning it a second time.
                    nearest = scores.novelty
                    if nearest is None:
                        nearest = self.structure_archive.nearest(evaluation.structure_values)
                    keep = nearest >= self.duplicate_threshold
                    rejected_duplicate = int((~keep).sum())
                    fitness = np.where(keep, fitness, -np.inf)

                scaled_values = apply_scaling(self.structure_archive.scaling, evaluation.structure_values)
                coverage_gain = scores.components.get("coverage_gain")
                remaining = self.budget.max_accepted - len(result.accepted)
                batch_budget = min(self.optimizer.batch_accept, remaining)
                if (
                    self.selection_strategy == "local_incremental_maximin_v1"
                    and evaluation.atomic_values is not None
                    and evaluation.row_offsets is not None
                ):
                    selected = select_local_incremental_batch(
                        fitness,
                        scaled_values,
                        evaluation.atomic_values,
                        evaluation.row_offsets,
                        budget=batch_budget,
                        local_archive=self.local_archive,
                        threshold=float(getattr(self.objective, "novel_environment_threshold")),
                    )
                else:
                    # structure_fps_v1: the G3 baseline — novelty ranking then
                    # farthest-point sampling over mean-pooled structure rows.
                    selected = select_diverse_batch(fitness, scaled_values, budget=batch_budget)
                # Second-stage energy/force screening (R5.1): runs on the
                # selected batch, before anything downstream. Screened-out
                # candidates stay valid and novel (their environments still
                # count as discovered) but are never archived or fed back as
                # parents. The discovery metrics below therefore run on the
                # PRE-screening selection — screening removes implausible
                # structures from the archive, it does not un-discover the
                # descriptor-space regions they explored.
                discovered = list(selected)
                if self.energy_screening is not None and discovered:
                    screen_verdicts = self.energy_screening.screen([valid[i] for i in discovered])
                    spec = getattr(self.energy_screening, "spec", None)
                    # Policy for "unscreenable" verdicts (the screener could
                    # not judge the frame): "keep" (default) leaves them in
                    # the archive/feedback stream with their provenance --
                    # "we could not judge" is not "we judged badly";
                    # "reject" drops them from the archive exactly like a
                    # fail. Either way they stay discovered (the discovery
                    # metrics count the pre-screening selection) and a "fail"
                    # verdict is never keepable.
                    unscreenable_policy = (
                        getattr(spec, "unscreenable_policy", "keep") if spec is not None else "keep"
                    )
                    kept: list[int] = []
                    for index, verdict in zip(discovered, screen_verdicts):
                        if verdict.accepted:
                            if verdict.status == "pass":
                                screening_passed += 1
                            else:
                                screening_unscreenable += 1
                            if verdict.status == "unscreenable" and unscreenable_policy == "reject":
                                screening_reasons[index] = tuple(verdict.reasons) or ("unscreenable",)
                            else:
                                kept.append(index)
                                screening_verdicts[index] = verdict
                        else:
                            screening_reasons[index] = tuple(verdict.reasons) or ("energy_screening",)
                    rejected_screening = len(discovered) - len(kept)
                    # Train-ready = a screened pass under at least one
                    # configured bound (the writer's train_set_ready
                    # semantics); a measure-only pass is a measurement, not a
                    # plausibility verdict.
                    if spec is not None and (
                        getattr(spec, "max_energy_per_atom", None) is not None
                        or getattr(spec, "max_force", None) is not None
                    ):
                        screening_train_ready = screening_passed
                    selected = kept
                selected_set = set(selected)
                selection_rank = {index: rank for rank, index in enumerate(selected)}
                # Unique novel environments must be counted against the
                # *pre-round* archive (A12: "frozen archive + already counted
                # this round"). Every selected candidate's rows are zero
                # distance to its own just-added block, so counting after the
                # archive update would make this metric structurally zero.
                # Both metrics count the pre-screening selection (see the
                # gate above); the archived variant tracks what actually
                # entered the local archive. The discovery-rate stop reads
                # the discovered metric — a strict screen must never be able
                # to fake saturation.
                if scores.novel_environment_count is not None and self.local_archive is not None and evaluation.atomic_values is not None:
                    threshold = getattr(self.objective, "novel_environment_threshold", None)
                    if threshold is not None:
                        candidate_ids = [candidate.candidate_id for candidate in valid]
                        unique_novel_environments = self._count_unique_novel_environments(
                            discovered, evaluation.atomic_values, evaluation.row_offsets, float(threshold)
                        )
                        strict_unique_v2 = self._count_unique_novel_environments_v2(
                            discovered, candidate_ids, evaluation.atomic_values, evaluation.row_offsets, float(threshold)
                        )
                        if rejected_screening:
                            archived_unique_novel_environments = (
                                self._count_unique_novel_environments(
                                    selected, evaluation.atomic_values, evaluation.row_offsets, float(threshold)
                                )
                                if selected
                                else 0
                            )
                            archived_strict_unique_v2 = (
                                self._count_unique_novel_environments_v2(
                                    selected, candidate_ids, evaluation.atomic_values, evaluation.row_offsets, float(threshold)
                                )
                                if selected
                                else 0
                            )
                        else:
                            archived_unique_novel_environments = unique_novel_environments
                            archived_strict_unique_v2 = strict_unique_v2
                # Every evaluated candidate (accepted or not) feeds the
                # descriptor-space map the results view animates.
                for index in range(len(valid)):
                    result.evaluated.append(
                        EvaluatedRecord(
                            candidate_id=valid[index].candidate_id,
                            generation=generation,
                            structure_descriptor=np.asarray(evaluation.structure_values[index], dtype=np.float32),
                            novelty=float(scores.novelty[index]) if scores.novelty is not None else None,
                            fitness=float(fitness[index]) if np.isfinite(fitness[index]) else float("nan"),
                            accepted=index in selected_set,
                        )
                    )
                if on_evaluated is not None:
                    on_evaluated(valid)

                accepted_this_round = len(selected)
                if selected:
                    entries = []
                    for order, index in enumerate(selected):
                        candidate = valid[index]
                        verdict = screening_verdicts.get(index)
                        entries.append(
                            ArchiveEntry(
                                candidate_id=candidate.candidate_id,
                                structure_index=len(result.accepted) + order,
                                fitness=float(fitness[index]),
                                novelty=float(scores.novelty[index]) if scores.novelty is not None else float("nan"),
                                generation=generation,
                            )
                        )
                        result.evaluations.append(
                            CandidateEvaluation(
                                candidate_id=candidate.candidate_id,
                                valid=True,
                                rejection_reason=None,
                                structure_descriptor=evaluation.structure_values[index],
                                atomic_descriptors=(
                                    evaluation.atomic_values[
                                        int(evaluation.row_offsets[index]) : int(evaluation.row_offsets[index + 1])
                                    ]
                                    if evaluation.atomic_values is not None
                                    else None
                                ),
                                novelty=float(scores.novelty[index]) if scores.novelty is not None else None,
                                local_diversity=(
                                    float(scores.local_diversity[index]) if scores.local_diversity is not None else None
                                ),
                                penalty=0.0,
                                fitness=float(fitness[index]),
                                novel_environment_count=(
                                    int(scores.novel_environment_count[index])
                                    if scores.novel_environment_count is not None
                                    else None
                                ),
                                atom_count=int(candidate.atomic_numbers.size),
                                # Verdict provenance (2026-10-01 audit): kept
                                # candidates carry their full verdict, so an
                                # accepted frame without measurements is
                                # explainable (unscreenable), not mysterious.
                                energy=(verdict.energy if verdict is not None else None),
                                energy_per_atom=(verdict.energy_per_atom if verdict is not None else None),
                                max_force=(verdict.max_force if verdict is not None else None),
                                screening_status=(verdict.status if verdict is not None else None),
                                screening_reasons=(
                                    tuple(verdict.reasons) if verdict is not None else ()
                                ),
                            )
                        )
                        result.accepted.append(candidate)
                    self.structure_archive.add(
                        np.stack([evaluation.structure_values[i] for i in selected]), entries
                    )
                    if self.local_archive is not None and evaluation.atomic_values is not None:
                        rows = np.concatenate(
                            [evaluation.atomic_values[int(evaluation.row_offsets[i]) : int(evaluation.row_offsets[i + 1])] for i in selected]
                        )
                        local_entries = [
                            ArchiveEntry(
                                candidate_id=entries[k].candidate_id,
                                structure_index=entries[k].structure_index,
                                fitness=entries[k].fitness,
                                novelty=entries[k].novelty,
                                generation=generation,
                            )
                            for k in range(len(entries))
                        ]
                        # A degenerate evaluator may return zero-length atom
                        # row segments for every accepted structure (the
                        # objective scores them 0 and the strategies fill the
                        # batch). Such a batch contributes nothing to the
                        # environment archive — skip the add instead of
                        # raising after the structure archive was updated
                        # (2026-09-30 audit: all-empty local batch).
                        if rows.shape[0]:
                            self.local_archive.add(rows, local_entries)
                    best_round_fitness = float(max(fitness[i] for i in selected))
                # Raw discovery counts read the pre-screening selection, same
                # as the strict metric above — an all-rejected round still
                # discovered environments (R5.1: "counted as discovered").
                if scores.novel_environment_count is not None:
                    novel_environments = int(sum(int(scores.novel_environment_count[i]) for i in discovered))
                if scores.novelty is not None:
                    finite_novelty = scores.novelty[np.isfinite(scores.novelty)]
                    if finite_novelty.size:
                        best_round_novelty = float(finite_novelty.max())
                        mean_round_novelty = float(finite_novelty.mean())

            # Report every proposed candidate's outcome back to the optimizer
            # (G3.5 lifecycle): proposal order, geometry rejections included.
            # Local-environment targeting additionally carries each scored
            # candidate's scaled atom rows so the targeted branch can weigh
            # proposals in atomic space (audit R3.4).
            local_targeting = (
                bool(self.local_anchor_descriptors)
                and self.local_archive is not None
                and evaluation.atomic_values is not None
                and evaluation.row_offsets is not None
            )
            round_observations: list[CandidateObservation] = []
            valid_cursor = 0
            for child, verdict in zip(children, verdicts):
                if not verdict.valid:
                    round_observations.append(
                        CandidateObservation(
                            candidate_id=child.candidate_id,
                            generation=generation,
                            candidate=child,
                            valid=False,
                            geometry_rejection=_geometry_rejection_code(
                                verdict.reasons[0] if verdict.reasons else None
                            ),
                        )
                    )
                    continue
                index = valid_cursor
                valid_cursor += 1
                if index >= len(valid):
                    # Dropped by the evaluation-budget truncation before the
                    # descriptor call: the pipeline never scored it.
                    continue
                round_observations.append(
                    CandidateObservation(
                        candidate_id=child.candidate_id,
                        generation=generation,
                        candidate=child,
                        valid=True,
                        accepted=index in selected_set,
                        selection_rank=selection_rank.get(index),
                        fitness=_finite_or_none(fitness, index),
                        novelty=_finite_or_none(scores.novelty, index),
                        local_diversity=_finite_or_none(scores.local_diversity, index),
                        coverage_gain=_finite_or_none(coverage_gain, index),
                        novel_environment_count=(
                            int(scores.novel_environment_count[index])
                            if scores.novel_environment_count is not None
                            else None
                        ),
                        structure_descriptor=np.asarray(scaled_values[index], dtype=np.float64),
                        screening_rejection=screening_reasons.get(index, ()),
                        local_descriptor=(
                            apply_scaling(
                                self.local_archive.scaling,
                                evaluation.atomic_values[
                                    int(evaluation.row_offsets[index]) : int(evaluation.row_offsets[index + 1])
                                ],
                            )
                            if local_targeting
                            else None
                        ),
                    )
                )
            self.optimizer.observe(ObservationBatch(generation=generation, observations=round_observations))

            record = RoundRecord(
                generation=generation,
                evaluations=evaluations_this_round,
                proposed=len(children),
                rejected_geometry=rejected_geometry,
                rejected_geometry_by_reason=rejected_geometry_by_reason,
                rejected_duplicate=rejected_duplicate,
                accepted=accepted_this_round,
                best_fitness=best_round_fitness,
                best_novelty=best_round_novelty,
                mean_novelty=mean_round_novelty,
                coverage_radius=self.structure_archive.accepted_coverage_radius(),
                novel_environments=novel_environments,
                unique_novel_environments=unique_novel_environments,
                archived_unique_novel_environments=archived_unique_novel_environments,
                strict_unique_v2=strict_unique_v2,
                archived_strict_unique_v2=archived_strict_unique_v2,
                rejected_screening=rejected_screening,
                screening_passed=screening_passed,
                screening_unscreenable=screening_unscreenable,
                screening_train_ready=screening_train_ready,
            )
            result.rounds.append(record)
            # The stagnation bookkeeping for the round just finished is
            # applied BEFORE on_round so a snapshot taken there sees a fully
            # consistent round boundary (audit R5.5).
            if best_round_fitness > best_fitness + 1e-9:
                best_fitness = best_round_fitness
                stagnant = 0
            else:
                stagnant += 1
            result.best_fitness = None if best_fitness == -np.inf else float(best_fitness)
            result.stagnant = stagnant
            if on_round is not None:
                on_round(record)

            # Discovery-rate saturation (G3): the scientific stop, shared
            # verbatim with the resume re-check so a restored run re-derives
            # the original stop decision exactly (one definition).
            if self._discovery_saturated(result.rounds, generation, counts_environments):
                result.stopped_by = "discovery_saturated"
                break
            if self._screening_bottleneck(result.rounds, generation, counts_environments):
                result.stopped_by = "screening_bottleneck"
                break
            if progress is not None:
                fraction_cap = self.budget.max_evaluations
                progress(
                    min(total_evaluations, fraction_cap),
                    fraction_cap,
                    f"generation {generation}: accepted {len(result.accepted)}",
                )

            if self.budget.target_novelty is not None and best_round_novelty is not None and best_round_novelty >= self.budget.target_novelty:
                result.stopped_by = "target_novelty"
                break

        return result

    def _discovery_saturated(self, rounds: list, generation: int, counts_environments: bool) -> bool:
        """Discovery-rate saturation (G3): the trailing ``discovery_window``
        rounds produced fewer novel environments per 100 descriptor
        evaluations than the threshold — expanding further is not paying
        off, the archive has converged onto the reachable frontier of the
        operator family. Only objectives that actually produce
        novel_environment_count may trigger it. The rate reads the same
        strictly deduplicated metric the benchmark and the results view
        report (raw counts stay on the record as a diagnostic): raw sums
        let repeated environments hold the rate above the floor and
        postpone a saturation stop that already fired. Shared by the round
        loop and the resume re-check so both decide identically.
        """
        window = int(getattr(self.budget, "discovery_window", 0) or 0)
        if not window or not counts_environments or generation < window:
            return False
        gained = sum(
            record.unique_novel_environments if record.unique_novel_environments is not None else record.novel_environments
            for record in rounds[-window:]
        )
        spent = sum(record.evaluations for record in rounds[-window:])
        min_rate = float(getattr(self.budget, "min_novel_per_100_evals", 0.0) or 0.0)
        return spent > 0 and gained < min_rate * spent / 100.0

    def _screening_bottleneck(self, rounds: list, generation: int, counts_environments: bool) -> bool:
        """Screening-bottleneck saturation (gen-5): the ARCHIVED discovery
        rate — what survived the energy/force screen and actually entered the
        local archive — fell below the floor while the pre-screen discovery
        rate did not. The descriptor frontier is not exhausted; the screen is
        rejecting nearly everything the selector finds, so expanding further
        burns evaluations without growing the training set. Only runs with
        energy screening can hit it, and it is consulted only AFTER
        :meth:`_discovery_saturated` returned False, so descriptor
        exhaustion keeps the stronger, pre-existing stop reason. Reads the
        same ``discovery_window`` / ``min_novel_per_100_evals`` knobs (the
        zero default keeps it off) and is shared verbatim by the round loop
        and the resume re-check. Archived rounds without the gen-5 v2 fields
        (old records) fall back to the gen-4 archived metric."""
        if self.energy_screening is None:
            return False
        window = int(getattr(self.budget, "discovery_window", 0) or 0)
        if not window or not counts_environments or generation < window:
            return False
        gained = 0
        for record in rounds[-window:]:
            if record.archived_unique_novel_environments is not None:
                gained += record.archived_unique_novel_environments
            elif record.unique_novel_environments is not None:
                gained += record.unique_novel_environments
            else:
                gained += record.novel_environments
        spent = sum(record.evaluations for record in rounds[-window:])
        min_rate = float(getattr(self.budget, "min_novel_per_100_evals", 0.0) or 0.0)
        return spent > 0 and gained < min_rate * spent / 100.0

    # -- resume (audit R5.5) -------------------------------------------------

    def _config_fingerprint(self) -> str:
        """SHA-256 over the run-definition inputs a resume must reproduce:
        the frozen scaling and reference content of both archives, the
        objective and operator configuration, the geometry constraints, the
        seed-pool geometry, the targeting context and the screening stage.
        Budgets and stop thresholds are deliberately excluded — extending
        them between the interruption and the resume is the point of the
        feature; everything in here changes the trajectory itself and must
        be rejected."""
        if self._config_fingerprint_cache is None:
            evaluator_signature = getattr(self.evaluator, "signature", None)
            payload = {
                "selection_strategy": self.selection_strategy,
                "n_seeds": int(self.n_seeds),
                "duplicate_threshold": self.duplicate_threshold,
                "region_radius": self.region_radius,
                "seed_descriptors": [np.asarray(value, dtype=np.float64).tolist() for value in self.seed_descriptors],
                "anchor_descriptors": [np.asarray(value, dtype=np.float64).tolist() for value in self.anchor_descriptors],
                "local_anchor_descriptors": [
                    np.asarray(value, dtype=np.float64).tolist() for value in self.local_anchor_descriptors
                ],
                "seed_local_distances": [None if value is None else float(value) for value in self.seed_local_distances],
                "objective": _objective_fingerprint(self.objective),
                # Descriptor implementation identity (2026-10-02 audit P1) —
                # the bare class name stayed constant across descriptor,
                # parameter, and wheel changes. Stub evaluators without a
                # signature (tests) degrade to the class name.
                "evaluator": (
                    evaluator_signature() if callable(evaluator_signature) else {"class": type(self.evaluator).__name__}
                ),
                "constraints": _constraints_fingerprint(self.constraints),
                "energy_screening": _screening_fingerprint(self.energy_screening),
                "operators": [
                    {
                        "name": getattr(operator, "name", type(operator).__name__),
                        "params": dict(getattr(operator, "operator_params", {}) or {}),
                    }
                    for operator in self.optimizer.operators
                ],
                "seed_pool": [_candidate_record(candidate) for candidate in self.seed_pool],
                "structure_archive": _archive_fingerprint(self.structure_archive),
                "local_archive": None if self.local_archive is None else _archive_fingerprint(self.local_archive),
            }
            blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self._config_fingerprint_cache = hashlib.sha256(blob).hexdigest()
        return self._config_fingerprint_cache

    def snapshot_state(self) -> dict:
        """Full continuation state at a round boundary (audit R5.5).

        Call from ``on_round``: every bit of search-carrying state is
        recorded — RNG bit-generator state, optimizer pools/genomes/memory,
        accepted archives as a per-round replay (raw rows, so replaying
        ``add`` re-applies the scaling identically), the accepted candidates
        themselves, counters and round history, the screening verdicts of
        every accepted evaluation (R5.1), the evaluated-candidate records
        the descriptor-space map is rebuilt from (v3), plus manifest counts
        and a run-configuration fingerprint restore_state() verifies.
        Intra-round optimizer bookkeeping is deliberately dropped (propose()
        clears it).
        """
        result = self._active_result
        if result is None:
            raise RuntimeError("snapshot_state requires a started run (call from on_round)")
        # Candidate persistence: everything accepted, plus anything the
        # optimizer references beyond that (PSO particle memory can hold
        # evaluated-but-rejected candidates).
        registered: dict = {}
        for candidate in result.accepted:
            registered[candidate.candidate_id] = candidate

        def register_candidate(candidate) -> None:
            if candidate is not None and candidate.candidate_id not in registered:
                registered[candidate.candidate_id] = candidate

        accepted_ids = [candidate.candidate_id for candidate in result.accepted]
        if len(set(accepted_ids)) != len(accepted_ids):
            # Unreachable through run() (the proposal-id guard renames
            # collisions before evaluation); a hard stop here keeps a future
            # code path from persisting an ambiguous candidate table.
            raise RuntimeError("duplicate candidate ids in the accepted set — candidate-identity invariant violated")

        evaluations = [
            {
                "candidate_id": evaluation.candidate_id,
                "structure_descriptor": np.asarray(evaluation.structure_descriptor, dtype=np.float64).tolist(),
                "atomic_descriptors": (
                    None
                    if evaluation.atomic_descriptors is None
                    else np.asarray(evaluation.atomic_descriptors, dtype=np.float64).tolist()
                ),
                "novelty": evaluation.novelty,
                "local_diversity": evaluation.local_diversity,
                "fitness": evaluation.fitness,
                "novel_environment_count": evaluation.novel_environment_count,
                "atom_count": evaluation.atom_count,
                "energy": evaluation.energy,
                "energy_per_atom": evaluation.energy_per_atom,
                "max_force": evaluation.max_force,
                "screening_status": evaluation.screening_status,
                "screening_reasons": list(evaluation.screening_reasons),
            }
            for evaluation in result.evaluations
        ]
        # Per-round archive replay blocks, derived from the acceptance order.
        # The replayed ArchiveEntry rows are the ARCHIVE's own entries — not
        # recomputed values — so a restored archive is identical to the
        # original one, metadata included (R5.5 byte-exact fixpoint).
        archive_entries = self.structure_archive.entries
        replay = []
        cursor = 0
        for record in result.rounds:
            count = record.accepted
            block = evaluations[cursor : cursor + count]
            live_entries = archive_entries[cursor : cursor + count]
            cursor += count
            entries = [
                {
                    "candidate_id": entry.candidate_id,
                    "structure_index": entry.structure_index,
                    "fitness": entry.fitness,
                    "novelty": entry.novelty,
                    "generation": entry.generation,
                }
                for entry in live_entries
            ]
            replay.append(
                {
                    "structure_rows": [entry["structure_descriptor"] for entry in block],
                    "local_rows": (
                        None
                        if any(entry["atomic_descriptors"] is None for entry in block) or self.local_archive is None
                        else [row for entry in block for row in entry["atomic_descriptors"]]
                    ),
                    "entries": entries,
                }
            )
        # The optimizer registers its extra referenced candidates (PSO
        # particle memory can hold evaluated-but-rejected children) as a side
        # effect — it must run BEFORE the candidate table is built.
        optimizer_state = self.optimizer.snapshot_state(register_candidate)
        candidates = [_candidate_record(candidate) for candidate in registered.values()]
        # Evaluated records are self-contained (scalars + one descriptor row
        # each); their candidate_id may reference a candidate that is NOT in
        # the candidate table (evaluated-but-rejected), so restore validates
        # shape only.
        evaluated = [
            {
                "candidate_id": record.candidate_id,
                "generation": int(record.generation),
                "structure_descriptor": np.asarray(record.structure_descriptor, dtype=np.float32).tolist(),
                "novelty": record.novelty,
                "fitness": float(record.fitness),
                "accepted": bool(record.accepted),
            }
            for record in result.evaluated
        ]
        return {
            "version": SNAPSHOT_VERSION,
            "algorithm_version": GENERATION_ALGORITHM_VERSION,
            "generation": int(result.rounds[-1].generation) if result.rounds else 0,
            "total_evaluations": int(sum(record.evaluations for record in result.rounds)),
            "best_fitness": result.best_fitness,
            "stagnant": int(result.stagnant),
            "budget": asdict(self.budget),
            "rng": _encode_rng_state(self.rng.bit_generator.state),
            "optimizer": optimizer_state,
            "accepted_order": [candidate.candidate_id for candidate in result.accepted],
            "rounds": [record.to_json() for record in result.rounds],
            "candidates": candidates,
            "evaluations": evaluations,
            "archive_replay": replay,
            "counts": {
                "candidates": len(candidates),
                "evaluations": len(evaluations),
                "accepted": len(result.accepted),
                "rounds": len(result.rounds),
                "evaluated": len(evaluated),
            },
            "evaluated": evaluated,
            "config_fingerprint": self._config_fingerprint(),
        }

    def write_snapshot(self, directory) -> None:
        """Durably persist snapshot_state() into ``directory``.

        Commit protocol (audit R5.5): both data files are written under
        unique, manifest-referenced names and fsynced BEFORE the manifest
        replaces state.json. v1 overwrote the data files in place, so a
        crash mid-write destroyed the previous snapshot while its manifest
        still pointed at it. Here the manifest is the commit point: before
        it lands the old snapshot is fully intact, after it lands the new
        one is (its data files are already durable), and stale data files
        are unlinked only once the new manifest is committed.
        """
        state = self.snapshot_state()
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex[:16]
        files: dict = {}
        for key in ("candidates", "evaluations", "evaluated"):
            data = json.dumps(state.pop(key), ensure_ascii=False).encode("utf-8")
            name = f"{key}-{token}.json"
            durable_write(directory / name, data)
            files[key] = {"name": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        state["files"] = files
        durable_write(directory / "state.json", json.dumps(state, ensure_ascii=False).encode("utf-8"))
        fsync_directory(directory)
        keep = {entry["name"] for entry in files.values()}
        stale_files = (
            list(directory.glob("candidates-*.json"))
            + list(directory.glob("evaluations-*.json"))
            + list(directory.glob("evaluated-*.json"))
            + list(directory.glob("*.json.tmp"))
        )
        for stale in stale_files:
            if stale.name not in keep:
                try:
                    stale.unlink()
                except OSError:
                    pass  # a locked stale file must not fail the committed snapshot

    def restore_state(self, directory) -> None:
        """Load a snapshot written by write_snapshot into this engine.

        Transactional (audit R5.5): every field, reference, count, dimension
        and hash is validated — and the optimizer state checked against this
        engine's type and configuration — BEFORE anything is mutated, so a
        rejected snapshot leaves the engine untouched instead of half-
        restored. The engine must be constructed with the same run
        definition as the interrupted run (the configuration fingerprint is
        verified) and fresh, empty archives, which are replayed here; the
        RNG and optimizer state are applied when run() starts.

        Resume semantics: the continuation is faithful to the recorded
        history. Extending ``max_generations``/``max_evaluations`` between
        the interruption and the resume is supported, but an expanded
        evaluation budget only stops truncating the remaining rounds' batch
        sizes — it does not retroactively turn the already-truncated rounds
        into ones proposed under the larger budget. The scientific stops
        (target novelty, discovery saturation) re-derive from the replayed
        history: a run that already reached them stays stopped.
        """
        directory = Path(directory)
        manifest_path = directory / "state.json"
        try:
            state = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"snapshot manifest {manifest_path} is not valid JSON: {exc}") from exc
        if not isinstance(state, dict):
            raise ValueError("snapshot manifest must be a JSON object")
        if state.get("version") != SNAPSHOT_VERSION:
            raise ValueError(f"unsupported snapshot version: {state.get('version')}")
        if state.get("algorithm_version") != GENERATION_ALGORITHM_VERSION:
            raise ValueError(
                f"snapshot algorithm version {state.get('algorithm_version')} does not match "
                f"the running {GENERATION_ALGORITHM_VERSION} — persisted metric semantics differ"
            )
        require_keys(
            state,
            (
                "generation",
                "total_evaluations",
                "best_fitness",
                "stagnant",
                "rng",
                "optimizer",
                "accepted_order",
                "rounds",
                "archive_replay",
                "files",
                "counts",
                "config_fingerprint",
            ),
            "manifest",
        )
        for key in ("generation", "total_evaluations", "stagnant"):
            value = state[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"snapshot {key} must be a non-negative integer")
        best_fitness = state["best_fitness"]
        if best_fitness is not None and (isinstance(best_fitness, bool) or not isinstance(best_fitness, (int, float))):
            raise ValueError("snapshot best_fitness must be a number or null")

        files = state["files"]
        if not isinstance(files, dict) or set(files) != {"candidates", "evaluations", "evaluated"}:
            raise ValueError(
                "snapshot manifest must reference exactly the candidates, evaluations, and evaluated data files"
            )
        payloads: dict = {}
        for key, entry in files.items():
            if not isinstance(entry, dict):
                raise ValueError(f"snapshot manifest file entry for {key} must be an object")
            name = entry.get("name")
            if not isinstance(name, str) or not _DATA_FILE_NAME.fullmatch(name):
                raise ValueError(f"snapshot manifest references an unsafe data file name: {name!r}")
            data = (directory / name).read_bytes()
            if len(data) != entry.get("bytes"):
                raise ValueError(
                    f"snapshot data file {name} holds {len(data)} bytes but the manifest recorded {entry.get('bytes')}"
                )
            digest = hashlib.sha256(data).hexdigest()
            if digest != entry.get("sha256"):
                raise ValueError(f"snapshot data file {name} fails its manifest SHA-256 check — truncated or corrupted")
            try:
                payloads[key] = json.loads(data.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"snapshot data file {name} is not valid JSON: {exc}") from exc

        if self.structure_archive.size != 0 or (self.local_archive is not None and self.local_archive.size != 0):
            raise ValueError("refusing to restore into a non-empty archive — restore into a freshly constructed engine")
        if state["config_fingerprint"] != self._config_fingerprint():
            raise ValueError(
                "snapshot configuration fingerprint does not match this engine — the run definition "
                "(archives/scaling, objective, operators, geometry constraints, seed pool, targeting "
                "or screening) changed since the snapshot was taken"
            )

        candidates_payload = payloads["candidates"]
        evaluations_payload = payloads["evaluations"]
        evaluated_payload = payloads["evaluated"]
        if not isinstance(candidates_payload, list) or not isinstance(evaluations_payload, list) or not isinstance(
            evaluated_payload, list
        ):
            raise ValueError("snapshot candidates, evaluations, and evaluated files must hold JSON arrays")

        candidates = []
        for record in candidates_payload:
            try:
                candidates.append(_candidate_from_record(record))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"snapshot candidate record is malformed: {exc}") from exc
        candidates_by_id: dict = {}
        for candidate in candidates:
            if candidate.candidate_id in candidates_by_id:
                raise ValueError(f"snapshot candidate table lists {candidate.candidate_id!r} twice")
            candidates_by_id[candidate.candidate_id] = candidate

        evaluations = []
        for entry in evaluations_payload:
            require_keys(entry, _EVALUATION_KEYS, "evaluation record")
            if entry["candidate_id"] not in candidates_by_id:
                raise ValueError(f"snapshot evaluations reference unknown candidate: {entry['candidate_id']}")
            try:
                structure_rows = np.asarray(entry["structure_descriptor"], dtype=np.float64)
                atomic_rows = (
                    None
                    if entry["atomic_descriptors"] is None
                    else np.asarray(entry["atomic_descriptors"], dtype=np.float64)
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"snapshot evaluation descriptor is malformed: {exc}") from exc
            if structure_rows.ndim != 1:
                raise ValueError("snapshot evaluation structure_descriptor must be a flat row")
            if atomic_rows is not None and atomic_rows.ndim != 2:
                raise ValueError("snapshot evaluation atomic_descriptors must be a 2-D row matrix")
            evaluations.append(
                CandidateEvaluation(
                    candidate_id=entry["candidate_id"],
                    valid=True,
                    rejection_reason=None,
                    structure_descriptor=structure_rows,
                    atomic_descriptors=atomic_rows,
                    novelty=entry["novelty"],
                    local_diversity=entry["local_diversity"],
                    penalty=0.0,
                    fitness=entry["fitness"],
                    novel_environment_count=entry["novel_environment_count"],
                    atom_count=entry["atom_count"],
                    # Screening verdicts survived the snapshot (R5.1): a
                    # restored accepted candidate keeps its measurements and
                    # its "unscreenable" provenance.
                    energy=entry["energy"],
                    energy_per_atom=entry["energy_per_atom"],
                    max_force=entry["max_force"],
                    screening_status=entry["screening_status"],
                    screening_reasons=tuple(entry["screening_reasons"] or ()),
                )
            )

        accepted_order = state["accepted_order"]
        if not isinstance(accepted_order, list):
            raise ValueError("snapshot accepted_order must be a JSON array")
        missing = sorted({cid for cid in accepted_order if cid not in candidates_by_id})
        if missing:
            raise ValueError(f"snapshot accepted order references unknown candidates: {', '.join(missing[:3])}")
        if len(accepted_order) != len(evaluations):
            raise ValueError("snapshot evaluations do not align with the accepted order")

        # Evaluated records (v3): the descriptor-space map's rows. These are
        # NOT referentially validated — an evaluated-but-rejected candidate
        # has no row in the candidate table by design.
        evaluated_records = []
        for entry in evaluated_payload:
            require_keys(entry, ("candidate_id", "generation", "structure_descriptor", "fitness", "accepted"), "evaluated record")
            try:
                descriptor_row = np.asarray(entry["structure_descriptor"], dtype=np.float32)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"snapshot evaluated descriptor is malformed: {exc}") from exc
            if descriptor_row.ndim != 1 or not np.isfinite(descriptor_row.astype(np.float64)).all():
                raise ValueError("snapshot evaluated structure_descriptor must be a finite flat row")
            novelty = entry["novelty"]
            if novelty is not None and (isinstance(novelty, bool) or not isinstance(novelty, (int, float))):
                raise ValueError("snapshot evaluated novelty must be a number or null")
            fitness = entry["fitness"]
            if isinstance(fitness, bool) or not isinstance(fitness, (int, float)):
                raise ValueError("snapshot evaluated fitness must be a number")
            evaluated_records.append(
                EvaluatedRecord(
                    candidate_id=str(entry["candidate_id"]),
                    generation=int(entry["generation"]),
                    structure_descriptor=descriptor_row,
                    novelty=None if novelty is None else float(novelty),
                    fitness=float(fitness),
                    accepted=bool(entry["accepted"]),
                )
            )

        if not isinstance(state["rounds"], list):
            raise ValueError("snapshot rounds must be a JSON array")
        rounds = []
        for record in state["rounds"]:
            require_keys(record, _ROUND_RECORD_KEYS, "round record")
            try:
                rounds.append(RoundRecord(**record))
            except TypeError as exc:
                raise ValueError(f"snapshot round record is malformed: {exc}") from exc

        counts = state["counts"]
        expected_counts = {
            "candidates": len(candidates),
            "evaluations": len(evaluations),
            "accepted": len(accepted_order),
            "rounds": len(rounds),
            "evaluated": len(evaluated_records),
        }
        if counts != expected_counts:
            raise ValueError(f"snapshot manifest counts {counts} do not match the recorded data {expected_counts}")

        replay = state["archive_replay"]
        if not isinstance(replay, list) or len(replay) != len(rounds):
            raise ValueError("snapshot archive replay must carry one block per recorded round")
        replay_blocks: list = []
        cursor = 0
        for round_replay in replay:
            require_keys(round_replay, ("entries", "local_rows"), "archive replay block")
            entries_payload = round_replay["entries"]
            if not isinstance(entries_payload, list):
                raise ValueError("snapshot archive replay entries must be a JSON array")
            entries = []
            for entry in entries_payload:
                require_keys(entry, ("candidate_id", "structure_index", "fitness", "novelty", "generation"), "archive entry")
                if entry["candidate_id"] not in candidates_by_id:
                    raise ValueError(f"snapshot archive replay references unknown candidate: {entry['candidate_id']}")
                try:
                    entries.append(ArchiveEntry(**entry))
                except TypeError as exc:
                    raise ValueError(f"snapshot archive entry is malformed: {exc}") from exc
            count = len(entries)
            structure_rows = None
            local_rows = None
            if count:
                try:
                    structure_rows = np.asarray(
                        [evaluations_payload[cursor + offset]["structure_descriptor"] for offset in range(count)],
                        dtype=np.float64,
                    )
                except (IndexError, TypeError, ValueError) as exc:
                    raise ValueError(f"snapshot archive replay structure rows are malformed: {exc}") from exc
                if structure_rows.ndim != 2:
                    raise ValueError("snapshot archive replay structure rows must form a 2-D matrix")
                if self.local_archive is not None:
                    local_block = round_replay["local_rows"]
                    if local_block is None:
                        raise ValueError("snapshot lacks local archive rows for a run with a local archive")
                    try:
                        local_rows = np.asarray(local_block, dtype=np.float64)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(f"snapshot local archive rows are malformed: {exc}") from exc
                    # A batch whose candidates all had zero atom rows never
                    # reached the local archive (the engine skips the add),
                    # so an all-empty block replays as nothing.
                    local_rows = None if local_rows.size == 0 else local_rows
                    if local_rows is not None and local_rows.ndim != 2:
                        raise ValueError("snapshot local archive rows must form a 2-D matrix")
            replay_blocks.append((structure_rows, local_rows, entries))
            cursor += count
        if cursor != len(evaluations):
            raise ValueError("snapshot archive replay does not cover every recorded evaluation")

        rng = _rng_from_state(state["rng"])

        optimizer_state = state["optimizer"]
        require_keys(optimizer_state, ("type", "config"), "optimizer state")
        if optimizer_state["type"] != self.optimizer.name:
            raise ValueError(
                f"snapshot optimizer type {optimizer_state['type']!r} does not match this engine's {self.optimizer.name!r}"
            )
        self.optimizer.validate_snapshot(optimizer_state, candidates_by_id)

        # Everything validated — the only mutations below are the archive
        # replay and the prepared-resume stash, and neither can fail.
        for structure_rows, local_rows, entries in replay_blocks:
            if not entries:
                continue
            self.structure_archive.add(structure_rows, entries)
            if local_rows is not None:
                self.local_archive.add(local_rows, entries)
        # A post-resume proposal can re-mint an id the pre-crash run already
        # issued (same parent, same deformation) — it must be renamed like
        # any other collision, not silently merge with the persisted one.
        self._issued_candidate_ids.update(candidates_by_id)
        self._resume = {
            "generation": state["generation"],
            "total_evaluations": state["total_evaluations"],
            "stagnant": state["stagnant"],
            "best_fitness": None if best_fitness is None else float(best_fitness),
            "rng": rng,
            "optimizer": optimizer_state,
            "accepted": [candidates_by_id[cid] for cid in accepted_order],
            "rounds": rounds,
            "evaluations": evaluations,
            "evaluated": evaluated_records,
        }
        self._resume_candidates = candidates_by_id
