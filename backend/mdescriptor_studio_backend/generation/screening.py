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
periodicity, non-finite prediction) stays energy_screened=false and is
*not* rejected — screening is best-effort per frame, never a silent
rejection. A predictor that *errors* is a global failure, not a per-frame
one: the exception propagates and fails the round (configuration and
capability problems are caught at submit time instead).
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Literal

import numpy as np

from .models import StructureCandidate

_SCREENING_MODELS = ("NEP", "DPA4C")


def mdescriptor_predictors_available() -> bool:
    """True when the installed mdescriptor ships working energy/force predictors.

    Two gates (2026-10-01/02 audits): an explicit find_spec preflight keeps
    the old-wheel rejection working deterministically, and the deeper checks
    behind it — the predictors package must export both predictor classes and
    the native extension must carry the DPA4C backend — reject a broken or
    partial wheel at submit time instead of failing inside the worker. Neither
    gate loads or checksums a model — bundled-model integrity and explicit
    checkpoint loadability surface at EnergyForceScreen construction."""
    if importlib.util.find_spec("mdescriptor.predictors") is None:
        return False
    try:
        from mdescriptor.predictors import DPA4C, NEP  # noqa: F401

        import mdescriptor._native as native
    except (ImportError, AttributeError):
        return False
    return hasattr(native, "Dpa4cPredictor")


@dataclass(frozen=True)
class ScreeningSpec:
    """Validated energy/force screening configuration."""

    model: str = "NEP"
    checkpoint: str | None = None  # required for DPA4C (model checkpoint path)
    device: str = "cpu"  # cpu | cuda
    num_threads: int | None = None
    max_energy_per_atom: float | None = None  # eV/atom upper bound
    max_force: float | None = None  # eV/angstrom max-|F| upper bound

    def __post_init__(self) -> None:
        # The frontend trims the checkpoint path before submitting; the API
        # must not hand a whitespace-only string (or a non-string like 42) to
        # the predictor loader (2026-10-01 audit P2). Empty after trimming
        # means "no override" — the bundled default applies.
        if self.checkpoint is not None:
            if not isinstance(self.checkpoint, str):
                raise ValueError("energy_screening checkpoint must be a string path when set")
            object.__setattr__(self, "checkpoint", self.checkpoint.strip() or None)

    def validate(self) -> None:
        if self.model not in _SCREENING_MODELS:
            raise ValueError(f"energy_screening model must be one of {', '.join(_SCREENING_MODELS)}")
        if self.device not in ("cpu", "cuda"):
            raise ValueError("energy_screening device must be cpu or cuda")
        # Both models ship a bundled default (NEP: nep89_20250409, DPA4C:
        # DPA4C-Air-OMat24-v20260819) that auto-resolves with checksum
        # verification; ``checkpoint`` is an optional explicit override.
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
        self._model_identity: dict | None = None
        if not mdescriptor_predictors_available():
            raise ValueError(
                "energy_screening requires mdescriptor >= 0.3.5 (the installed wheel lacks the predictors package)"
            )
        from mdescriptor.core.options import ExecutionOptions
        from mdescriptor.predictors import DPA4C, NEP

        execution = ExecutionOptions(device=spec.device, num_threads=spec.num_threads)
        # model=None resolves the bundled default resource for either model
        # (checkpoint is pre-normalized: None or a non-empty trimmed path).
        if spec.model == "NEP":
            self._predictor = NEP(model=spec.checkpoint, execution=execution)
        else:
            self._predictor = DPA4C(model=spec.checkpoint, execution=execution)

    def model_identity(self) -> dict:
        """Content identity of the loaded predictor resource (2026-10-02 audit P1).

        The configured checkpoint PATH cannot tell an in-place file
        replacement apart, so cache keys, snapshot fingerprints and artifact
        metadata carry the resolved resource's own checksum (verified at load
        time) plus the installed wheel version instead. Cached once: the
        resolved-model digest is computed on first access."""
        if self._model_identity is None:
            try:
                package_version = importlib.metadata.version("mdescriptor")
            except importlib.metadata.PackageNotFoundError:
                package_version = "unknown"
            resolved = getattr(self._predictor, "resolved_model", None)
            path = getattr(resolved, "path", None)
            self._model_identity = {
                "model": self.spec.model,
                "checkpoint": self.spec.checkpoint,
                "resolved_source": getattr(resolved, "source", None),
                "resolved_path": None if path is None else str(path),
                "sha256": getattr(resolved, "digest", None),
                "mdescriptor_version": package_version,
            }
        return self._model_identity

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
                if any(
                    value is not None and not np.isfinite(value) for value in (total, per_atom, max_force)
                ):
                    # A non-finite prediction is a frame the screener cannot
                    # judge, not a bound violation — never a silent pass
                    # (2026-10-01 audit). The official 0.3.5 PredictionResult
                    # rejects non-finite values upstream, so this guards
                    # third-party predictor stand-ins.
                    verdicts[index] = ScreeningVerdict(status="unscreenable", reasons=("non_finite_prediction",))
                    continue
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
