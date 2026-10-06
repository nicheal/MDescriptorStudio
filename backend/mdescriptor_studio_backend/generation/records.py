"""Round, evaluated-candidate and run-result records.

One definition shared by the engine, the service layer, the artifact
writer and the benchmark harness. ``RoundRecord`` is the per-round public
face (its ``to_json`` shape is also the snapshot manifest's round schema);
``EvaluatedRecord`` is the descriptor-space map entry; ``GenerationRunResult``
is what :meth:`GenerationEngine.run` returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


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
    # Permutation-invariant counterpart of unique_novel_environments (gen-5
    # strict_unique_v2): the same greedy strict dedup run in CANONICAL order
    # (candidates by candidate_id, atomic rows lexicographic) so the count is
    # a pure function of the selected row set. Coexists with the gen-4
    # visit-order metric — published R4 numbers stay gen-4-caliber. None when
    # the objective produces no counts.
    strict_unique_v2: int | None = None
    # strict_unique_v2 over the post-screening kept set; equals
    # strict_unique_v2 unless screening rejected a selected candidate.
    archived_strict_unique_v2: int | None = None
    # Candidates removed after selection by energy/force screening (R5.1):
    # still counted as discovered, never archived or fed back. Equals the
    # round's "fail" verdict count, plus "unscreenable" verdicts when the
    # run's unscreenable_policy is "reject" — the screening breakdown below carries
    # the other states explicitly.
    rejected_screening: int = 0
    # Screening verdict breakdown over the selected batch (2026-10-02 audit
    # D): "pass" = judged physically plausible; "unscreenable" = the screener
    # could not judge the frame (partial periodicity, non-finite prediction) —
    # kept but never equivalent to a screened pass; "train_ready" = pass AND
    # at least one energy/force bound configured (the writer's
    # train_set_ready semantics, one definition at the source).
    screening_passed: int = 0
    screening_unscreenable: int = 0
    screening_train_ready: int = 0
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
            "strict_unique_v2": self.strict_unique_v2,
            "archived_strict_unique_v2": self.archived_strict_unique_v2,
            "rejected_screening": self.rejected_screening,
            "screening_passed": self.screening_passed,
            "screening_unscreenable": self.screening_unscreenable,
            "screening_train_ready": self.screening_train_ready,
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
