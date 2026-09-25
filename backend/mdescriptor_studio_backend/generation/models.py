"""Strongly typed data contracts for descriptor-guided generation.

These dataclasses are the module's internal data contract: candidates flow
from operators through constraints and descriptor evaluation into the
archive as typed objects, not dicts. Only the RPC boundary (service.py)
serialises them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..datasets.base import DatasetFrame
from ..datasets.deepmd_symbols import _SYMBOL_TO_Z
from ..errors import AppError, INVALID_PARAMS


@dataclass
class StructureCandidate:
    """One candidate structure, with the lineage needed to reproduce it."""

    candidate_id: str
    atomic_numbers: np.ndarray  # (natoms,) int
    positions: np.ndarray  # (natoms, 3) float64
    cell: np.ndarray  # (3, 3) float64, zero for isolated
    pbc: np.ndarray  # (3,) bool
    parent_frame: int | None
    parent_candidate_id: str | None
    generation: int
    operator: str
    operator_params: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def to_frame(self) -> DatasetFrame:
        return DatasetFrame(
            numbers=np.asarray(self.atomic_numbers, dtype=np.int64),
            positions=np.asarray(self.positions, dtype=np.float64),
            cell=np.asarray(self.cell, dtype=np.float64),
            pbc=np.asarray(self.pbc, dtype=bool),
            id=self.candidate_id,
        )

    @classmethod
    def from_frame(
        cls,
        frame: DatasetFrame,
        *,
        candidate_id: str,
        parent_frame: int | None,
        generation: int = 0,
        operator: str = "seed",
        operator_params: dict | None = None,
        metadata: dict | None = None,
    ) -> "StructureCandidate":
        return cls(
            candidate_id=candidate_id,
            atomic_numbers=np.asarray(frame.numbers, dtype=np.int64),
            positions=np.asarray(frame.positions, dtype=np.float64),
            cell=np.asarray(frame.cell, dtype=np.float64),
            pbc=np.asarray(frame.pbc, dtype=bool),
            parent_frame=parent_frame,
            parent_candidate_id=None,
            generation=generation,
            operator=operator,
            operator_params=dict(operator_params or {}),
            metadata=dict(metadata or {}),
        )

    def child(
        self,
        *,
        candidate_id: str,
        positions: np.ndarray,
        atomic_numbers: np.ndarray | None = None,
        cell: np.ndarray | None = None,
        operator: str,
        operator_params: dict | None = None,
        metadata: dict | None = None,
    ) -> "StructureCandidate":
        """A derived candidate; operators may change composition or atom count."""
        inherited = {
            "parent_composition": np.sort(self.atomic_numbers).tolist(),
            "parent_atom_count": int(self.atomic_numbers.size),
        }
        inherited.update(metadata or {})
        return StructureCandidate(
            candidate_id=candidate_id,
            atomic_numbers=np.asarray(
                self.atomic_numbers if atomic_numbers is None else atomic_numbers,
                dtype=np.int64,
            ),
            positions=np.asarray(positions, dtype=np.float64),
            cell=np.asarray(self.cell if cell is None else cell, dtype=np.float64),
            pbc=self.pbc,
            parent_frame=self.parent_frame,
            parent_candidate_id=self.candidate_id,
            generation=self.generation + 1,
            operator=operator,
            operator_params=dict(operator_params or {}),
            metadata=inherited,
        )


@dataclass
class CandidateEvaluation:
    """A candidate after descriptor scoring against the frozen archive."""

    candidate_id: str
    valid: bool
    rejection_reason: str | None
    structure_descriptor: np.ndarray | None
    atomic_descriptors: np.ndarray | None
    novelty: float | None
    local_diversity: float | None
    penalty: float
    fitness: float
    novel_environment_count: int | None = None
    atom_count: int | None = None


@dataclass(frozen=True)
class ArchiveEntry:
    candidate_id: str
    structure_index: int
    fitness: float
    novelty: float
    generation: int


@dataclass(frozen=True)
class ConstraintResult:
    valid: bool
    penalty: float
    reasons: list


@dataclass(frozen=True)
class ObjectiveResult:
    score: float
    components: dict


@dataclass(frozen=True)
class OperatorSpec:
    name: str
    params: dict


@dataclass(frozen=True)
class Budget:
    max_evaluations: int = 10_000
    max_accepted: int = 500
    max_generations: int = 200
    target_novelty: float | None = None
    no_improvement_rounds: int | None = None
    # Discovery-rate saturation (G3): stop when the trailing window rounds
    # produced fewer than ``min_novel_per_100_evals`` novel environments per
    # 100 descriptor evaluations — "new-environment discovery has dried up".
    discovery_window: int = 10
    min_novel_per_100_evals: float = 1.0


@dataclass(frozen=True)
class GenerationRequest:
    """Validated, normalised form of the generation.submit RPC payload."""

    dataset_id: str
    descriptor_run_id: str
    optimizer: str
    objective: dict
    operators: list
    constraints: dict
    budget: Budget
    seed: int | None
    seed_view_id: str | None = None
    optimizer_params: dict = field(default_factory=dict)


def _int(params: dict, key: str, default: int, *, lo: int, hi: int) -> int:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AppError(INVALID_PARAMS, f"{key} must be a number")
    value = int(value)
    if value < lo or value > hi:
        raise AppError(INVALID_PARAMS, f"{key} must be between {lo} and {hi}")
    return value


def _optional_float(params: dict, key: str) -> float | None:
    value = params.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(float(value)):
        raise AppError(INVALID_PARAMS, f"{key} must be a finite number")
    return float(value)


def _optional_count(params: dict, key: str, default: int, *, lo: int, hi: int) -> int:
    """An integer knob: absent → default; fractional floats and bools rejected."""
    value = params.get(key, default)
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AppError(INVALID_PARAMS, f"{key} must be an integer")
    if float(value) != int(value):
        raise AppError(INVALID_PARAMS, f"{key} must be an integer")
    value = int(value)
    if value < lo or value > hi:
        raise AppError(INVALID_PARAMS, f"{key} must be between {lo} and {hi}")
    return value


def parse_budget(params: dict) -> Budget:
    params = params or {}
    min_novel = _optional_float(params, "min_novel_per_100_evals")
    budget = Budget(
        max_evaluations=_int(params, "max_evaluations", 10_000, lo=1, hi=10_000_000),
        max_accepted=_int(params, "max_accepted", 500, lo=1, hi=1_000_000),
        max_generations=_int(params, "max_generations", 200, lo=1, hi=100_000),
        target_novelty=_optional_float(params, "target_novelty"),
        no_improvement_rounds=(
            _int(params, "no_improvement_rounds", 10, lo=1, hi=10_000)
            if params.get("no_improvement_rounds") is not None
            else None
        ),
        discovery_window=_int(params, "discovery_window", 10, lo=1, hi=10_000),
        # 0.0 is a legal value ("never stop on the discovery rate") and must
        # not be collapsed into the 1.0 default — only absence means default.
        min_novel_per_100_evals=1.0 if min_novel is None else min_novel,
    )
    if budget.target_novelty is not None and budget.target_novelty <= 0:
        raise AppError(INVALID_PARAMS, "target_novelty must be positive")
    if budget.min_novel_per_100_evals < 0:
        raise AppError(INVALID_PARAMS, "min_novel_per_100_evals must be >= 0")
    return budget


def parse_seed(value) -> int | None:
    """None means a random, non-reproducible run (excluded from caching)."""
    if value is None or value == "random":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise AppError(INVALID_PARAMS, "seed must be an integer or 'random'")
    return int(value)


def parse_operators(params) -> list:
    """Normalise the nested operator dict into a list of OperatorSpec.

    Accepts either a list of {"name", "params"} objects or the UI's nested
    {"<name>": {"enabled": true, ...params}} mapping; disabled entries drop out.
    """
    from .registry import GENERATION_REGISTRY

    specs: list[OperatorSpec] = []
    try:
        if isinstance(params, dict):
            items = [
                (name, cfg if isinstance(cfg, dict) else {})
                for name, cfg in params.items()
            ]
            for name, cfg in items:
                if not cfg.get("enabled", True):
                    continue
                op_params = {k: v for k, v in cfg.items() if k != "enabled"}
                GENERATION_REGISTRY.operator(name)  # validates the name early
                op_params = _normalize_operator_params(name, op_params)
                specs.append(OperatorSpec(name=name, params=op_params))
        elif isinstance(params, list):
            for entry in params:
                if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                    raise AppError(INVALID_PARAMS, "operators entries must be {name, params} objects")
                op_params = entry.get("params") or {}
                if not isinstance(op_params, dict):
                    raise AppError(INVALID_PARAMS, "operator params must be an object")
                GENERATION_REGISTRY.operator(entry["name"])
                specs.append(
                    OperatorSpec(
                        name=entry["name"],
                        params=_normalize_operator_params(entry["name"], dict(op_params)),
                    )
                )
        else:
            raise AppError(INVALID_PARAMS, "operators must be an object or a list")
    except KeyError as exc:
        raise AppError(INVALID_PARAMS, str(exc)) from exc
    if not specs:
        raise AppError(INVALID_PARAMS, "at least one operator must be enabled")
    return specs


def _normalize_operator_params(name: str, params: dict) -> dict:
    if name not in ("interstitial_atom", "substitution") or "element" not in params:
        return params
    element = params["element"]
    if element is None:
        params.pop("element")
        return params
    if not isinstance(element, str):
        raise AppError(INVALID_PARAMS, f"{name} element must be a valid chemical symbol")
    element = element.strip()
    if not element:
        params.pop("element")
        return params
    symbol = element[0].upper() + element[1:].lower()
    if symbol not in _SYMBOL_TO_Z:
        raise AppError(INVALID_PARAMS, f"{name} element must be a valid chemical symbol")
    params["element"] = symbol
    return params


def normalize_pair_min_distances(value) -> dict[str, float]:
    """Validate element-pair contact overrides and canonicalize pair order."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AppError(INVALID_PARAMS, "min_distance_pairs must be an object")
    result: dict[str, float] = {}
    for pair, distance in value.items():
        parts = [symbol.strip() for symbol in pair.split("-")] if isinstance(pair, str) else []
        if len(parts) != 2 or any(symbol not in _SYMBOL_TO_Z for symbol in parts):
            raise AppError(INVALID_PARAMS, f"invalid element pair in min_distance_pairs: {pair}")
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            raise AppError(INVALID_PARAMS, f"distance for {pair} must be a positive finite number")
        try:
            distance = float(distance)
        except (OverflowError, ValueError) as exc:
            raise AppError(INVALID_PARAMS, f"distance for {pair} must be between 0 and 20 Å") from exc
        if not np.isfinite(distance) or not 0 < distance <= 20:
            raise AppError(INVALID_PARAMS, f"distance for {pair} must be between 0 and 20 Å")
        first, second = sorted(parts, key=_SYMBOL_TO_Z.__getitem__)
        canonical_pair = f"{first}-{second}"
        if canonical_pair in result:
            raise AppError(INVALID_PARAMS, f"duplicate element pair in min_distance_pairs: {pair}")
        result[canonical_pair] = distance
    return result


def parse_request(params: dict) -> GenerationRequest:
    """Validate the generation.submit payload into a typed request."""
    from .registry import GENERATION_REGISTRY

    params = params or {}
    dataset_id = params.get("dataset_id")
    descriptor_run_id = params.get("descriptor_run_id")
    if not isinstance(dataset_id, str) or not dataset_id:
        raise AppError(INVALID_PARAMS, "dataset_id is required")
    if not isinstance(descriptor_run_id, str) or not descriptor_run_id:
        raise AppError(INVALID_PARAMS, "descriptor_run_id is required")
    seed_view_id = params.get("seed_view_id")
    if seed_view_id is not None and (not isinstance(seed_view_id, str) or not seed_view_id):
        raise AppError(INVALID_PARAMS, "seed_view_id must be a non-empty string or null")
    optimizer = str(params.get("optimizer") or "random")
    objective = params.get("objective") or {"type": "novelty"}
    if not isinstance(objective, dict) or not isinstance(objective.get("type"), str):
        raise AppError(INVALID_PARAMS, "objective must be an object with a type")
    try:
        GENERATION_REGISTRY.optimizer(optimizer)
        GENERATION_REGISTRY.objective(objective["type"])
    except KeyError as exc:
        raise AppError(INVALID_PARAMS, str(exc)) from exc
    constraints = params.get("constraints") or {}
    if not isinstance(constraints, dict):
        raise AppError(INVALID_PARAMS, "constraints must be an object")
    constraints = dict(constraints)
    for key in ("composition_locked", "atom_count_locked"):
        # Locked-by-default is the single scientific default (shared with the
        # GeometryConstraints dataclass, build_constraints and the catalog):
        # perturbation operators must not silently change composition.
        value = constraints.get(key, True)
        if not isinstance(value, bool):
            raise AppError(INVALID_PARAMS, f"{key} must be a boolean")
        constraints[key] = value
    mode = constraints.get("min_distance_mode", "none")
    if mode not in ("none", "absolute", "covalent"):
        raise AppError(INVALID_PARAMS, "min_distance_mode must be none, absolute, or covalent")
    if mode != "none":
        factor = constraints.get("min_distance_factor", 0.7)
        if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not 0.1 <= float(factor) <= 2.0:
            raise AppError(INVALID_PARAMS, "min_distance_factor must be between 0.1 and 2.0")
    pair_min_distances = normalize_pair_min_distances(constraints.get("min_distance_pairs"))
    if pair_min_distances:
        constraints["min_distance_pairs"] = pair_min_distances
    else:
        constraints.pop("min_distance_pairs", None)
    for key in ("min_volume_per_atom", "max_volume_per_atom"):
        value = constraints.get(key)
        if value is None:
            constraints.pop(key, None)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise AppError(INVALID_PARAMS, f"{key} must be a positive finite number")
        try:
            value = float(value)
        except (OverflowError, ValueError) as exc:
            raise AppError(INVALID_PARAMS, f"{key} must be a positive finite number") from exc
        if not np.isfinite(value) or value <= 0:
            raise AppError(INVALID_PARAMS, f"{key} must be a positive finite number")
        constraints[key] = value
    minimum_volume = constraints.get("min_volume_per_atom")
    maximum_volume = constraints.get("max_volume_per_atom")
    if minimum_volume is not None and maximum_volume is not None and minimum_volume > maximum_volume:
        raise AppError(INVALID_PARAMS, "min_volume_per_atom must not exceed max_volume_per_atom")
    optimizer_params = params.get("optimizer_params") or {}
    if not isinstance(optimizer_params, dict):
        raise AppError(INVALID_PARAMS, "optimizer_params must be an object")
    reuse_accepted_seeds = optimizer_params.get("reuse_accepted_seeds", False)
    if not isinstance(reuse_accepted_seeds, bool):
        raise AppError(INVALID_PARAMS, "reuse_accepted_seeds must be a boolean")
    if optimizer == "random":
        # Full request-level validation (G3.5 A10): an illegal payload must
        # fail at Run-click time, not after the job was queued — the
        # optimizer constructor would only reject these mid-worker.
        unknown = set(optimizer_params) - {"children_per_seed", "batch_accept", "n_seeds", "reuse_accepted_seeds"}
        if unknown:
            raise AppError(
                INVALID_PARAMS,
                f"unknown optimizer params for '{optimizer}': {', '.join(sorted(unknown))}",
            )
        n_seeds = _optional_count(optimizer_params, "n_seeds", 64, lo=1, hi=1024)
        children_per_seed = _optional_count(optimizer_params, "children_per_seed", 8, lo=1, hi=1024)
        # The accepted budget cannot exceed what one round actually proposes.
        batch_accept = _optional_count(
            optimizer_params, "batch_accept", 8, lo=1, hi=n_seeds * children_per_seed
        )
        optimizer_params["n_seeds"] = n_seeds
        optimizer_params["children_per_seed"] = children_per_seed
        optimizer_params["batch_accept"] = batch_accept
    operators = parse_operators(params.get("operators"))
    operator_names = {spec.name for spec in operators}
    for spec in operators:
        if spec.name == "atomic_displacement":
            # σ and the hard cutoff are separate parameters (G3.5 A11):
            # σ bounds the Gaussian distribution, the cutoff bounds each
            # displacement. Both validated here so bad values cannot queue.
            max_sigma = spec.params.get("max_sigma", 0.15)
            if (
                isinstance(max_sigma, bool)
                or not isinstance(max_sigma, (int, float))
                or not np.isfinite(float(max_sigma))
                or not 0.0 < float(max_sigma) <= 5.0
            ):
                raise AppError(INVALID_PARAMS, "atomic_displacement max_sigma must be in (0, 5] Å")
            hard_cutoff = spec.params.get("hard_cutoff")
            if hard_cutoff is not None:
                if (
                    isinstance(hard_cutoff, bool)
                    or not isinstance(hard_cutoff, (int, float))
                    or not np.isfinite(float(hard_cutoff))
                    or not 0.0 < float(hard_cutoff) <= 20.0
                ):
                    raise AppError(INVALID_PARAMS, "atomic_displacement hard_cutoff must be in (0, 20] Å")
    count_changing = operator_names.intersection({"vacancy", "interstitial_atom"})
    if count_changing and (constraints["atom_count_locked"] or constraints["composition_locked"]):
        raise AppError(
            INVALID_PARAMS,
            "Vacancy and interstitial operators require unlocked composition and atom count",
        )
    if "substitution" in operator_names and constraints["composition_locked"]:
        raise AppError(INVALID_PARAMS, "Substitution requires unlocked composition")
    if reuse_accepted_seeds and not any(spec.name == "atomic_displacement" for spec in operators):
        raise AppError(INVALID_PARAMS, "Accepted-seed feedback requires atomic displacement")
    return GenerationRequest(
        dataset_id=dataset_id,
        descriptor_run_id=descriptor_run_id,
        optimizer=optimizer,
        objective=dict(objective),
        operators=operators,
        constraints=constraints,
        budget=parse_budget(params.get("budget")),
        seed=parse_seed(params.get("seed", 42)),
        seed_view_id=seed_view_id,
        optimizer_params=dict(optimizer_params),
    )
