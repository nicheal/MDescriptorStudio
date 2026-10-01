"""Energy/force screening adapter (audit R5.1, second-stage filter).

The two-layer contract: geometry (cheap) and descriptor novelty (the
objective) decide what is worth looking at; this module decides whether an
accepted candidate is *physically plausible* — novelty never implies
trustworthiness. Prediction runs on the mdescriptor engine's independent
NEP/DPA4C predictors (``mdescriptor.predictors``, requires >= 0.3.5,
CPU or CUDA); the default NEP model (nep89) is bundled with the wheel.

Statuses surfaced per candidate (accepted.extxyz frame headers):
geometry_passed, descriptor_novel, energy_screened, energy_screen_pass,
train_set_ready. A candidate the screener cannot judge (partial
periodicity, predictor unavailable) stays energy_screened=false and is
*not* rejected — screening is best-effort per frame, never a silent
rejection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from .models import StructureCandidate

_SCREENING_MODELS = ("NEP", "DPA4C")


def mdescriptor_predictors_available() -> bool:
    """True when the installed mdescriptor ships the predictors package."""
    import importlib.util

    try:
        import mdescriptor  # noqa: F401
    except ImportError:
        return False
    return importlib.util.find_spec("mdescriptor.predictors") is not None


@dataclass(frozen=True)
class ScreeningSpec:
    """Validated energy/force screening configuration."""

    model: str = "NEP"
    checkpoint: str | None = None  # required for DPA4C (model checkpoint path)
    device: str = "cpu"  # cpu | cuda
    num_threads: int | None = None
    max_energy_per_atom: float | None = None  # eV/atom upper bound
    max_force: float | None = None  # eV/angstrom max-|F| upper bound

    def validate(self) -> None:
        if self.model not in _SCREENING_MODELS:
            raise ValueError(f"energy_screening model must be one of {', '.join(_SCREENING_MODELS)}")
        if self.device not in ("cpu", "cuda"):
            raise ValueError("energy_screening device must be cpu or cuda")
        if self.model == "DPA4C" and not self.checkpoint:
            raise ValueError("energy_screening with DPA4C requires a checkpoint path")
        for key, value in (("max_energy_per_atom", self.max_energy_per_atom), ("max_force", self.max_force)):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value)
            ):
                # Energy bounds are typically NEGATIVE (eV/atom); forces are
                # positive norms. The contract is "finite number", the sign
                # is the caller's physics.
                raise ValueError(f"energy_screening {key} must be a finite number when set")

    @classmethod
    def from_constraints(cls, constraints: dict) -> "ScreeningSpec | None":
        """Parse ``constraints["energy_screening"]``; None when absent/disabled."""
        raw = (constraints or {}).get("energy_screening")
        if not isinstance(raw, dict) or not raw.get("enabled"):
            return None
        spec = cls(
            model=str(raw.get("model") or "NEP"),
            checkpoint=raw.get("checkpoint"),
            device=str(raw.get("device") or "cpu"),
            num_threads=raw.get("num_threads"),
            max_energy_per_atom=raw.get("max_energy_per_atom"),
            max_force=raw.get("max_force"),
        )
        spec.validate()
        if spec.num_threads is not None and (isinstance(spec.num_threads, bool) or not isinstance(spec.num_threads, int) or not 1 <= spec.num_threads <= 64):
            raise ValueError("energy_screening num_threads must be an integer in [1, 64]")
        return spec


@dataclass(frozen=True)
class ScreeningVerdict:
    """One candidate's screening outcome."""

    status: Literal["pass", "fail", "unscreenable"]
    energy: float | None = None  # total eV
    energy_per_atom: float | None = None
    max_force: float | None = None  # eV/angstrom
    reasons: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.status != "fail"


class EnergyForceScreen:
    """Batch energy/force screening backed by one mdescriptor predictor."""

    def __init__(self, spec: ScreeningSpec) -> None:
        spec.validate()
        self.spec = spec
        if not mdescriptor_predictors_available():
            raise ValueError(
                "energy_screening requires mdescriptor >= 0.3.5 (the installed wheel lacks the predictors package)"
            )
        from mdescriptor.core.options import ExecutionOptions
        from mdescriptor.predictors import DPA4C, NEP

        execution = ExecutionOptions(device=spec.device, num_threads=spec.num_threads)
        if spec.model == "NEP":
            self._predictor = NEP(model=spec.checkpoint, execution=execution)
        else:
            self._predictor = DPA4C(model=spec.checkpoint, execution=execution)

    @staticmethod
    def _batch(candidates: list[StructureCandidate]):
        from mdescriptor.core.input import StructureBatch

        # mdescriptor rejects partial periodicity inside one frame; those
        # candidates are reported as unscreenable instead of failing the batch.
        numbers, positions, cells, pbc, offsets, ids = [], [], [], [], [0], []
        screenable: list[int] = []
        for index, candidate in enumerate(candidates):
            flags = np.asarray(candidate.pbc, dtype=bool)
            periodic = bool(flags.any())
            if periodic and not flags.all():
                continue  # partial periodicity: not screenable
            rows = np.asarray(candidate.atomic_numbers, dtype=np.int64)
            numbers.extend(rows.tolist())
            positions.extend(np.asarray(candidate.positions, dtype=np.float64).tolist())
            cells.append(np.asarray(candidate.cell, dtype=np.float64).tolist())
            pbc.append([1 if periodic else 0] * 3 if periodic else [0, 0, 0])
            offsets.append(offsets[-1] + rows.size)
            ids.append(candidate.candidate_id)
            screenable.append(index)
        batch = StructureBatch(
            numbers=np.asarray(numbers, dtype=np.int64),
            positions=np.asarray(positions, dtype=np.float64).reshape(-1, 3),
            cells=np.asarray(cells, dtype=np.float64).reshape(-1, 3, 3),
            pbc=np.asarray(pbc, dtype=bool).reshape(-1, 3),
            offsets=np.asarray(offsets, dtype=np.int64),
            ids=tuple(ids),
        )
        return batch, screenable

    def screen(self, candidates: list[StructureCandidate]) -> list[ScreeningVerdict]:
        """Screen one batch; the verdict list aligns with ``candidates``."""
        spec = self.spec
        if not candidates:
            return []
        batch, screenable = self._batch(candidates)
        verdicts: list[ScreeningVerdict | None] = [None] * len(candidates)
        for index in range(len(candidates)):
            if index not in screenable:
                verdicts[index] = ScreeningVerdict(status="unscreenable", reasons=("partial_periodicity_not_screenable",))
        if screenable:
            prediction = self._predictor.predict(batch)
            energy = np.asarray(prediction.energy, dtype=np.float64)
            forces = np.asarray(prediction.forces, dtype=np.float64)
            offsets = np.asarray(prediction.offsets, dtype=np.int64)
            for position, index in enumerate(screenable):
                lo, hi = int(offsets[position]), int(offsets[position + 1])
                atoms = hi - lo
                total = float(energy[position])
                per_atom = total / atoms if atoms else None
                max_force = float(np.linalg.norm(forces[lo:hi], axis=1).max()) if atoms else None
                reasons = []
                if spec.max_energy_per_atom is not None and per_atom is not None and per_atom > spec.max_energy_per_atom:
                    reasons.append("energy_above_bound")
                if spec.max_force is not None and max_force is not None and max_force > spec.max_force:
                    reasons.append("force_above_bound")
                verdicts[index] = ScreeningVerdict(
                    status="fail" if reasons else "pass",
                    energy=total,
                    energy_per_atom=per_atom,
                    max_force=max_force,
                    reasons=tuple(reasons),
                )
        return [verdict if verdict is not None else ScreeningVerdict(status="unscreenable", reasons=("unscreenable",)) for verdict in verdicts]
