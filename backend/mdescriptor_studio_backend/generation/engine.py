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
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..analysis.sampling import apply_scaling
from ..analysis.sampling.fps import farthest_point_sampling
from ..errors import AppError, JOB_CANCELLED
from ._distance import min_sqdist_to_set
from .registry import GENERATION_ALGORITHM_VERSION
from .optimization import CandidateObservation, ObservationBatch, OptimizationContext
from .evaluator import DescriptorEvaluator
from .models import (
    ArchiveEntry,
    Budget,
    CandidateEvaluation,
    ConstraintResult,
    StructureCandidate,
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

SNAPSHOT_VERSION = 1


def _rng_from_state(state: dict) -> np.random.Generator:
    """Rebuild a Generator from a persisted bit-generator state (R5.5)."""
    bit_generator = getattr(np.random, state["bit_generator"])()
    bit_generator.state = state
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


def _candidate_novel_rows(
    rows: np.ndarray,
    archive_novel: np.ndarray,
    counted: np.ndarray | None,
    threshold: float,
) -> np.ndarray:
    """The rows one candidate contributes to the strict novel set right now.

    Shared by the round metric and the local selection strategy so both use
    one definition: archive-near rows are dropped, rows within ``threshold``
    of an already-counted row are dropped, and within-candidate duplicates
    are deduped greedily in fixed row order.

    Space contract (2026-09-30 audit P0): ``rows`` must already live in the
    space every batch-internal comparison is defined in (the archive's
    scaled space) and ``archive_novel`` must be a mask over those same row
    indices — computed by querying the archive with the RAW rows, since
    ``nearest_per_row`` scales internally.
    """
    fresh = rows[archive_novel]
    if fresh.shape[0] and counted is not None:
        d2 = min_sqdist_to_set(fresh, counted, workers=1)
        fresh = fresh[np.sqrt(np.clip(d2, 0.0, None)) > threshold]
    kept: list[np.ndarray] = []
    kept_matrix: np.ndarray | None = None
    for row in fresh:
        if kept_matrix is not None:
            d2 = min_sqdist_to_set(row[None, :], kept_matrix, workers=1)[0]
            if not np.sqrt(max(float(d2), 0.0)) > threshold:
                continue
        kept.append(row)
        kept_matrix = row[None, :] if kept_matrix is None else np.vstack([kept_matrix, row[None, :]])
    if not kept:
        return np.empty((0, rows.shape[1]), dtype=np.float64)
    return np.vstack(kept) if len(kept) > 1 else kept[0][None, :]


def count_strict_unique_environments(
    selected: list[int],
    atomic_values: np.ndarray,
    row_offsets: np.ndarray,
    threshold: float,
    local_archive,
) -> int:
    """Strict greedy union dedup of novel environments over ``selected``.

    Rows are visited in fixed candidate order, then fixed atomic row order.
    A row is counted only when its distance to the frozen archive AND to
    every already-counted novel row is strictly greater than ``threshold``;
    only counted rows join the novel set. Two accepted structures that found
    the same new region therefore contribute it once, within-candidate
    duplicates count once, and archive-near rows can never repel a later
    novel row. Environments accepted in earlier rounds reach this comparison
    only through the formal local_archive update. This one function is the
    single definition behind the round metric, the discovery-rate stop and
    the local selection strategy (P0-02/P1-01/P1-03).

    Every batch-internal comparison (already-counted dedup, within-candidate
    dedup) runs in the archive's scaled space — the space the threshold is
    defined in and the local strategy optimizes in (2026-09-30 audit P0: a
    raw-space counter over-counted when the archive scale > 1 and
    under-counted when < 1). The frozen-archive mask still queries raw rows
    because ``nearest_per_row`` applies the scaling itself — never pass it
    scaled rows, that would scale twice.

    The count is defined for the given visit order (selection order, then
    stored row order): threshold nearness is not transitive, so permuting
    candidates or atomic rows may legitimately change the greedy count. A
    permutation-invariant metric needs stable candidate identities and a
    versioned redefinition, not a silent change here.
    """
    atomic_values = np.asarray(atomic_values, dtype=np.float64)
    threshold = float(threshold)
    scaled = apply_scaling(local_archive.scaling, atomic_values)
    counted: np.ndarray | None = None
    unique = 0
    for index in selected:
        lo, hi = int(row_offsets[index]), int(row_offsets[index + 1])
        rows = atomic_values[lo:hi]
        if rows.shape[0] == 0:
            continue
        archive_novel = local_archive.nearest_per_row(rows) > threshold
        block = _candidate_novel_rows(scaled[lo:hi], archive_novel, counted, threshold)
        if block.shape[0]:
            unique += int(block.shape[0])
            counted = block if counted is None else np.vstack([counted, block])
    return unique


@dataclass
class RoundRecord:
    generation: int
    evaluations: int
    proposed: int
    rejected_geometry: int
    rejected_duplicate: int
    accepted: int
    best_fitness: float
    best_novelty: float | None
    mean_novelty: float | None
    # Covering radius of the *accepted* set over the reference domain
    # (None until the first accept). The reference rows themselves always
    # cover the domain exactly, so the old archive-level number was a
    # structural 0.0 — not a coverage metric.
    coverage_radius: float | None
    novel_environments: int
    # Novel environments accepted this round, deduplicated greedily against
    # the frozen archive *plus* everything already counted this round (in
    # selection order). Two accepted structures that found the same new
    # region count it once — the benchmark-honest number (G3.5 A12). None
    # when the objective does not produce environment counts.
    unique_novel_environments: int | None = None
    # The strict-dedup counterpart over the post-screening kept set (R5.1):
    # what actually entered the local archive this round. Equals
    # unique_novel_environments unless screening rejected a selected
    # candidate; None when the objective produces no counts.
    archived_unique_novel_environments: int | None = None
    # Candidates removed after selection by energy/force screening (R5.1):
    # still counted as discovered, never archived or fed back.
    rejected_screening: int = 0
    rejected_geometry_by_reason: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "generation": self.generation,
            "evaluations": self.evaluations,
            "proposed": self.proposed,
            "rejected_geometry": self.rejected_geometry,
            "rejected_geometry_by_reason": dict(self.rejected_geometry_by_reason),
            "rejected_duplicate": self.rejected_duplicate,
            "accepted": self.accepted,
            "best_fitness": self.best_fitness,
            "best_novelty": self.best_novelty,
            "mean_novelty": self.mean_novelty,
            "coverage_radius": self.coverage_radius,
            "novel_environments": self.novel_environments,
            "unique_novel_environments": self.unique_novel_environments,
            "archived_unique_novel_environments": self.archived_unique_novel_environments,
            "rejected_screening": self.rejected_screening,
        }


@dataclass
class EvaluatedRecord:
    """One valid candidate that reached descriptor evaluation (accepted or not)."""

    candidate_id: str
    generation: int
    structure_descriptor: np.ndarray
    novelty: float | None
    fitness: float
    accepted: bool


@dataclass
class GenerationRunResult:
    accepted: list = field(default_factory=list)  # list[StructureCandidate]
    evaluations: list = field(default_factory=list)  # list[CandidateEvaluation]
    rounds: list = field(default_factory=list)  # list[RoundRecord]
    evaluated: list = field(default_factory=list)  # list[EvaluatedRecord]
    stopped_by: str = "max_generations"
    # Resume mirrors (audit R5.5), updated at every round boundary so a
    # snapshot taken from on_round is fully consistent.
    best_fitness: float | None = None
    stagnant: int = 0

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def evaluation_count(self) -> int:
        return int(sum(r.evaluations for r in self.rounds)) if self.rounds else 0


def _fitness_elite_order(fitness: np.ndarray, finite: np.ndarray, pool_size: int) -> np.ndarray:
    """Descending-fitness elite order: stable ascending argsort, reversed.

    This is ``structure_fps_v1``'s historical order, kept bit-identical on
    purpose — equal-fitness ties resolve to the *later* input index first.
    Both selection strategies share this one helper so a pool cutoff or
    budget that falls inside an equal-fitness group leaves the same elite
    membership and pick order in both (2026-09-30 audit P1: the local
    strategy used to sort ``-fitness`` stably and kept *earlier* ties, so
    the two strategies could pick different identities from one tie group).
    """
    return finite[np.argsort(fitness[finite], kind="stable")[::-1][:pool_size]]


def select_diverse_batch(
    fitness: np.ndarray,
    candidate_values: np.ndarray,
    budget: int,
    *,
    top_pool_factor: int = 4,
) -> list[int]:
    """Novelty ranking, then farthest-point sampling inside the batch (§16).

    Without the FPS pass the top-K could be K near-identical structures that
    all sit far from the archive — the budget would be spent on one region.
    FPS runs on the fitness-elite pool only (top ``budget * top_pool_factor``),
    so selection stays a coverage decision among the candidates that already
    scored well, not a re-ranking by geometry alone.
    """
    fitness = np.asarray(fitness, dtype=np.float64)
    candidate_values = np.asarray(candidate_values, dtype=np.float64)
    finite = np.flatnonzero(np.isfinite(fitness))
    if finite.size == 0 or budget <= 0:
        return []
    pool_size = min(max(int(budget) * int(top_pool_factor), int(budget)), int(finite.size))
    ranked = _fitness_elite_order(fitness, finite, pool_size)
    pool = candidate_values[ranked]
    picked = farthest_point_sampling(pool, n_samples=min(int(budget), pool.shape[0]))
    return [int(ranked[i]) for i in picked.indices]


SELECTION_STRATEGIES = ("structure_fps_v1", "local_incremental_maximin_v1")


def select_local_incremental_batch(
    fitness: np.ndarray,
    candidate_values: np.ndarray,
    atomic_values: np.ndarray,
    row_offsets: np.ndarray,
    budget: int,
    *,
    local_archive,
    threshold: float,
    top_pool_factor: int = 4,
    max_candidates: int = 128,
) -> list[int]:
    """``local_incremental_maximin_v1``: batch selection in environment space.

    A fixed fitness-elite sub-pool (same shape as the structure-FPS baseline)
    is accepted iteratively: each step picks the candidate with the largest
    marginal count of strictly new environments versus the frozen archive
    plus the environments already claimed by this batch (exactly the
    ``count_strict_unique_environments`` semantics, so the strategy optimizes
    the metric the benchmark reports). Ties break on the maximin structure
    distance to the already-selected candidates — before the first selection
    the ranked order decides, which is the shared descending-fitness elite
    order of :func:`_fitness_elite_order` (ties in reverse input order,
    identical to the FPS baseline so equal-fitness cutoffs compare fairly) —
    and zero-gain candidates still fill the batch so accepted counts stay
    comparable with the baseline at equal budget.

    Compute is bounded by design (audit R3.3): the elite pool caps the
    per-step candidate sweep at ``max_candidates`` (on top of the
    ``budget * top_pool_factor`` bound) — but never below the requested
    budget, so acceptance counts stay equal to the uncapped FPS baseline
    whenever the budget exceeds the cap (2026-09-30 audit case J) —, all
    distance passes run through the blocked ``min_sqdist_to_set`` kernel (no
    materialized N×M matrix), and the batch memory holds only the counted
    novel rows. Atomic rows are scaled once with the archive's own scaling
    so every comparison lives in the space the threshold is defined in.
    """
    fitness = np.asarray(fitness, dtype=np.float64)
    finite = np.flatnonzero(np.isfinite(fitness))
    if finite.size == 0 or budget <= 0:
        return []
    pool_size = min(
        max(int(budget) * int(top_pool_factor), int(budget)),
        int(finite.size),
        # The elite cap bounds the sweep, but truncating the pool below the
        # budget would silently accept fewer candidates than the FPS
        # baseline at the same budget — the cap may only limit the elite
        # surplus beyond it.
        max(int(budget), max(1, int(max_candidates))),
    )
    ranked = _fitness_elite_order(fitness, finite, pool_size)
    candidate_values = np.asarray(candidate_values, dtype=np.float64)
    atomic_values = np.asarray(atomic_values, dtype=np.float64)
    threshold = float(threshold)

    # One scaling pass: batch-internal distances must live in the same space
    # the frozen archive's threshold is defined in (nearest_per_row scales
    # internally, so the frozen-archive mask below stays consistent).
    scaled_rows = apply_scaling(local_archive.scaling, atomic_values)
    archive_novel = local_archive.nearest_per_row(atomic_values) > threshold

    memory: np.ndarray | None = None
    selected: list[int] = []
    selected_structures: list[np.ndarray] = []
    remaining = [int(index) for index in ranked]

    def _marginal_gain(index: int) -> tuple[int, np.ndarray]:
        lo, hi = int(row_offsets[index]), int(row_offsets[index + 1])
        rows = scaled_rows[lo:hi]
        if rows.shape[0] == 0:
            return 0, np.empty((0, rows.shape[1] if rows.ndim == 2 else 0), dtype=np.float64)
        block = _candidate_novel_rows(rows, archive_novel[lo:hi], memory, threshold)
        return int(block.shape[0]), block

    while len(selected) < int(budget) and remaining:
        best_index: int | None = None
        best_gain = -1
        best_struct_dist = -np.inf
        best_block: np.ndarray | None = None
        for index in remaining:
            gain, block = _marginal_gain(index)
            structure = candidate_values[index]
            if selected_structures:
                d2 = min_sqdist_to_set(structure[None, :], np.asarray(selected_structures), workers=1)[0]
                struct_dist = float(np.sqrt(max(float(d2), 0.0)))
            else:
                struct_dist = 0.0  # first pick: ranked order decides ties
            if gain > best_gain or (gain == best_gain and struct_dist > best_struct_dist):
                best_index, best_gain, best_struct_dist, best_block = index, gain, struct_dist, block
        assert best_index is not None
        selected.append(best_index)
        remaining.remove(best_index)
        selected_structures.append(candidate_values[best_index])
        if best_block is not None and best_block.shape[0]:
            memory = best_block if memory is None else np.vstack([memory, best_block])
    return selected


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
        # (and cleared) at the top of run().
        self._resume: dict | None = None
        self._resume_candidates: dict | None = None
        self._active_result: GenerationRunResult | None = None
        self.seed_pool = list(seed_pool)
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

    def _count_unique_novel_environments(
        self,
        selected: list[int],
        atomic_values: np.ndarray,
        row_offsets: np.ndarray,
        threshold: float,
    ) -> int:
        return count_strict_unique_environments(selected, atomic_values, row_offsets, threshold, self.local_archive)

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
            # Continue an interrupted run (audit R5.5): replay the recorded
            # history, then hand the optimizer its pools/genomes/memory back.
            # The fresh initialize() above rebuilt the context; the restored
            # pools reference the snapshot's accepted candidates.
            resume, candidates_by_id = self._resume, self._resume_candidates
            self._resume = None
            self._resume_candidates = None
            result.rounds = [RoundRecord(**record) for record in resume["rounds"]]
            result.accepted = [candidates_by_id[cid] for cid in resume["accepted_order"]]
            result.evaluations = [
                CandidateEvaluation(
                    candidate_id=entry["candidate_id"],
                    valid=True,
                    rejection_reason=None,
                    structure_descriptor=np.asarray(entry["structure_descriptor"], dtype=np.float64),
                    atomic_descriptors=(
                        None
                        if entry["atomic_descriptors"] is None
                        else np.asarray(entry["atomic_descriptors"], dtype=np.float64)
                    ),
                    novelty=entry["novelty"],
                    local_diversity=entry["local_diversity"],
                    penalty=0.0,
                    fitness=entry["fitness"],
                    novel_environment_count=entry["novel_environment_count"],
                    atom_count=entry["atom_count"],
                    # Screening measurements must survive a resume (2026-10-01
                    # audit P1): without them a restored accepted candidate
                    # would silently revert to "not screened". .get() keeps
                    # pre-R5.1 snapshots (no recorded measurements) loadable.
                    energy=entry.get("energy"),
                    energy_per_atom=entry.get("energy_per_atom"),
                    max_force=entry.get("max_force"),
                    screening_status=entry.get("screening_status"),
                    screening_reasons=tuple(entry.get("screening_reasons") or ()),
                )
                for entry in resume["evaluations"]
            ]
            generation = int(resume["generation"])
            total_evaluations = int(resume["total_evaluations"])
            stagnant = int(resume["stagnant"])
            best_fitness = -np.inf if resume["best_fitness"] is None else float(resume["best_fitness"])
            self.rng = _rng_from_state(resume["rng"])
            self.optimizer.load_state(resume["optimizer"], candidates_by_id)

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
            children = list(proposal.candidates)

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
                    kept: list[int] = []
                    for index, verdict in zip(discovered, screen_verdicts):
                        if verdict.accepted:
                            kept.append(index)
                            screening_verdicts[index] = verdict
                        else:
                            screening_reasons[index] = tuple(verdict.reasons) or ("energy_screening",)
                    rejected_screening = len(discovered) - len(kept)
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
                        unique_novel_environments = self._count_unique_novel_environments(
                            discovered, evaluation.atomic_values, evaluation.row_offsets, float(threshold)
                        )
                        if rejected_screening:
                            archived_unique_novel_environments = (
                                self._count_unique_novel_environments(
                                    selected, evaluation.atomic_values, evaluation.row_offsets, float(threshold)
                                )
                                if selected
                                else 0
                            )
                        else:
                            archived_unique_novel_environments = unique_novel_environments
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
                rejected_screening=rejected_screening,
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

            # Discovery-rate saturation (G3): the scientific stop. When the
            # trailing window of rounds produced fewer novel environments per
            # 100 descriptor evaluations than the threshold, expanding
            # further is not paying off — the archive has converged onto the
            # reachable frontier of the operator family. Only objectives that
            # actually produce novel_environment_count may trigger it.
            # The rate reads the same strictly deduplicated metric the
            # benchmark and the results view report (raw counts stay on the
            # record as a diagnostic): raw sums let repeated environments
            # hold the rate above the floor and postpone a saturation stop
            # that already fired.
            window = int(getattr(self.budget, "discovery_window", 0) or 0)
            if window and counts_environments and generation >= window:
                gained = sum(
                    r.unique_novel_environments if r.unique_novel_environments is not None else r.novel_environments
                    for r in result.rounds[-window:]
                )
                spent = sum(r.evaluations for r in result.rounds[-window:])
                min_rate = float(getattr(self.budget, "min_novel_per_100_evals", 0.0) or 0.0)
                if spent > 0 and gained < min_rate * spent / 100.0:
                    result.stopped_by = "discovery_saturated"
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

    # -- resume (audit R5.5) -------------------------------------------------

    def snapshot_state(self) -> dict:
        """Full continuation state at a round boundary (audit R5.5).

        Call from ``on_round``: every bit of search-carrying state is
        recorded — RNG bit-generator state, optimizer pools/genomes/memory,
        accepted archives as a per-round replay (raw rows, so replaying
        ``add`` re-applies the scaling identically), the accepted candidates
        themselves, counters and round history. Intra-round optimizer
        bookkeeping is deliberately dropped (propose() clears it), and the
        descriptor-space map rows are not part of the snapshot — the
        artifact writer streams those per round.
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
        replay = []
        cursor = 0
        for record in result.rounds:
            count = record.accepted
            block = evaluations[cursor : cursor + count]
            cursor += count
            entries = [
                {
                    "candidate_id": entry["candidate_id"],
                    "structure_index": cursor - count + offset,
                    "fitness": entry["fitness"],
                    "novelty": entry["novelty"],
                    "generation": record.generation,
                }
                for offset, entry in enumerate(block)
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
        return {
            "version": SNAPSHOT_VERSION,
            "algorithm_version": GENERATION_ALGORITHM_VERSION,
            "generation": int(result.rounds[-1].generation) if result.rounds else 0,
            "total_evaluations": int(sum(record.evaluations for record in result.rounds)),
            "best_fitness": result.best_fitness,
            "stagnant": int(result.stagnant),
            "rng": self.rng.bit_generator.state,
            "optimizer": self.optimizer.snapshot_state(register_candidate),
            "accepted_order": [candidate.candidate_id for candidate in result.accepted],
            "rounds": [record.to_json() for record in result.rounds],
            "candidates": [_candidate_record(candidate) for candidate in registered.values()],
            "evaluations": evaluations,
            "archive_replay": replay,
        }

    def write_snapshot(self, directory) -> None:
        """Atomically persist snapshot_state() into ``directory``.

        Data files are written first and state.json replaces last, so a
        crash mid-write leaves the previous snapshot readable.
        """
        state = self.snapshot_state()
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        candidates, evaluations = state["candidates"], state["evaluations"]
        (directory / "candidates.json").write_text(
            json.dumps(candidates, ensure_ascii=False), encoding="utf-8"
        )
        (directory / "evaluations.json").write_text(
            json.dumps(evaluations, ensure_ascii=False), encoding="utf-8"
        )
        state["candidates"] = None
        state["evaluations"] = None
        tmp = directory / "state.json.tmp"
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, directory / "state.json")

    def restore_state(self, directory) -> None:
        """Load a snapshot written by write_snapshot into this engine.

        The engine must be constructed with the same seed pool / objective /
        operators as the interrupted run (the context is rebuilt
        identically); archives start empty and are replayed here, the RNG
        and the optimizer state are applied when run() starts.
        """
        directory = Path(directory)
        state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        if state.get("version") != SNAPSHOT_VERSION:
            raise ValueError(f"unsupported snapshot version: {state.get('version')}")
        if state.get("algorithm_version") != GENERATION_ALGORITHM_VERSION:
            raise ValueError(
                f"snapshot algorithm version {state.get('algorithm_version')} does not match "
                f"the running {GENERATION_ALGORITHM_VERSION} — persisted metric semantics differ"
            )
        candidates = [_candidate_from_record(record) for record in json.loads((directory / "candidates.json").read_text(encoding="utf-8"))]
        candidates_by_id = {candidate.candidate_id: candidate for candidate in candidates}
        evaluations = json.loads((directory / "evaluations.json").read_text(encoding="utf-8"))
        cursor = 0
        for round_replay in state["archive_replay"]:
            entries = [ArchiveEntry(**entry) for entry in round_replay["entries"]]
            count = len(entries)
            structure_rows = np.asarray(
                [evaluations[cursor + offset]["structure_descriptor"] for offset in range(count)],
                dtype=np.float64,
            )
            self.structure_archive.add(structure_rows, entries)
            if self.local_archive is not None:
                local_block = round_replay.get("local_rows")
                if local_block is None:
                    raise ValueError("snapshot lacks local archive rows for a run with a local archive")
                self.local_archive.add(np.asarray(local_block, dtype=np.float64), entries)
            cursor += count
        self.rng = _rng_from_state(state["rng"])
        self._resume = state | {"evaluations": evaluations}
        self._resume_candidates = candidates_by_id
