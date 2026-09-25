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

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np

from ..analysis.sampling import apply_scaling
from ..analysis.sampling.fps import farthest_point_sampling
from ..errors import AppError, JOB_CANCELLED
from ._distance import min_sqdist_to_set
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

    @property
    def accepted_count(self) -> int:
        return len(self.accepted)

    @property
    def evaluation_count(self) -> int:
        return int(sum(r.evaluations for r in self.rounds)) if self.rounds else 0


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
    ranked = finite[np.argsort(fitness[finite], kind="stable")[::-1][:pool_size]]
    pool = candidate_values[ranked]
    picked = farthest_point_sampling(pool, n_samples=min(int(budget), pool.shape[0]))
    return [int(ranked[i]) for i in picked.indices]


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
    ) -> None:
        if not seed_pool:
            raise ValueError("seed pool must not be empty")
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

    def _check_geometry(self, candidate: StructureCandidate) -> ConstraintResult:
        return self.constraints.validate(candidate)

    def _count_unique_novel_environments(
        self,
        selected: list[int],
        atomic_values: np.ndarray,
        row_offsets: np.ndarray,
        threshold: float,
    ) -> int:
        """Greedy union dedup of one round's novel environments (G3.5 A12).

        Candidates are counted in selection order against the frozen archive
        *plus* the environments already counted this round, so two accepted
        structures that found the same new region contribute it once. The raw
        per-candidate counts stay frozen-archive based (order-independent,
        what the objective scores on); this number is the benchmark-honest
        one — "Random vs GA" must compare unique environments, not raw.
        """
        counted: list[np.ndarray] = []
        unique = 0
        for index in selected:
            lo, hi = int(row_offsets[index]), int(row_offsets[index + 1])
            rows = np.asarray(atomic_values[lo:hi], dtype=np.float64)
            if rows.shape[0] == 0:
                continue
            novel = self.local_archive.nearest_per_row(rows) > threshold
            if counted:
                d2 = min_sqdist_to_set(rows, np.vstack(counted), workers=1)
                novel &= np.sqrt(np.clip(d2, 0.0, None)) > threshold
            unique += int(novel.sum())
            counted.append(rows)
        return unique

    def run(self, check_cancelled=None, progress=None, on_round=None, on_evaluated=None) -> GenerationRunResult:
        check_cancelled = check_cancelled or (lambda: None)
        result = GenerationRunResult()
        best_fitness = -np.inf
        stagnant = 0
        total_evaluations = 0
        generation = 0
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
            )
        )

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

            evaluations_this_round = 0
            accepted_this_round = 0
            best_round_fitness = -np.inf
            best_round_novelty = None
            mean_round_novelty = None
            novel_environments = 0
            unique_novel_environments: int | None = None

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
                selected = select_diverse_batch(
                    fitness,
                    scaled_values,
                    budget=min(self.optimizer.batch_accept, remaining),
                )
                selected_set = set(selected)
                selection_rank = {index: rank for rank, index in enumerate(selected)}
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
                        self.local_archive.add(rows, local_entries)
                    best_round_fitness = float(max(fitness[i] for i in selected))
                    if scores.novel_environment_count is not None:
                        novel_environments = int(sum(int(scores.novel_environment_count[i]) for i in selected))
                if scores.novelty is not None:
                    finite_novelty = scores.novelty[np.isfinite(scores.novelty)]
                    if finite_novelty.size:
                        best_round_novelty = float(finite_novelty.max())
                        mean_round_novelty = float(finite_novelty.mean())
                if scores.novel_environment_count is not None and self.local_archive is not None and evaluation.atomic_values is not None:
                    threshold = getattr(self.objective, "novel_environment_threshold", None)
                    if threshold is not None:
                        unique_novel_environments = self._count_unique_novel_environments(
                            selected, evaluation.atomic_values, evaluation.row_offsets, float(threshold)
                        )

            # Report every proposed candidate's outcome back to the optimizer
            # (G3.5 lifecycle): proposal order, geometry rejections included.
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
            )
            result.rounds.append(record)
            if on_round is not None:
                on_round(record)

            # Discovery-rate saturation (G3): the scientific stop. When the
            # trailing window of rounds produced fewer novel environments per
            # 100 descriptor evaluations than the threshold, expanding
            # further is not paying off — the archive has converged onto the
            # reachable frontier of the operator family. Only objectives that
            # actually produce novel_environment_count may trigger it.
            window = int(getattr(self.budget, "discovery_window", 0) or 0)
            if window and counts_environments and generation >= window:
                gained = sum(r.novel_environments for r in result.rounds[-window:])
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

            if best_round_fitness > best_fitness + 1e-9:
                best_fitness = best_round_fitness
                stagnant = 0
            else:
                stagnant += 1
            if self.budget.target_novelty is not None and best_round_novelty is not None and best_round_novelty >= self.budget.target_novelty:
                result.stopped_by = "target_novelty"
                break

        return result
