"""Single boundary to the mdescriptor engine (docs/plan/05-ENGINE_ADAPTER.md).

No other module may `import mdescriptor`. All engine exceptions are converted
to AppError here. Pinned: mdescriptor==0.2.3 (ADR-2).
"""

from __future__ import annotations

import logging

import numpy as np
import mdescriptor as md

from .errors import AppError, INTERNAL_ERROR, MODEL_NOT_FOUND

log = logging.getLogger(__name__)

_ERROR_MAP = [
    (md.DescriptorConfigError, "DESCRIPTOR_CONFIGURATION_ERROR"),
    (md.ModelLoadError, "MODEL_NOT_FOUND"),
    (md.DescriptorInputError, "UNSUPPORTED_PERIODICITY"),
    (md.PackageNotFoundError, "MDESCRIPTOR_INCOMPATIBLE"),
    (md.CancelledError, "JOB_CANCELLED"),
    (md.ClosedDescriptorError, "INTERNAL_ERROR"),
]


class EngineAdapter:
    """Stateless facade over mdescriptor; thread-safe via per-compute controls."""

    def __init__(self) -> None:
        self._schema_cache: dict[str, dict] = {}

    # -- info / registry -------------------------------------------------
    def runtime_info(self) -> dict:
        return dict(md.get_runtime_info())

    def list_names(self) -> list[str]:
        return sorted(md.list_descriptors())

    def schema(self, name: str) -> dict:
        cached = self._schema_cache.get(name)
        if cached is None:
            try:
                cached = dict(md.describe_descriptor(name))
            except md.MDescriptorError as exc:
                raise self._convert(exc) from exc
            self._schema_cache[name] = cached
        return cached

    # -- construction / compute ------------------------------------------
    def build(self, name: str, parameters: dict):
        try:
            cfg = md.DescriptorConfiguration(
                schema_version=md.CONFIGURATION_SCHEMA_VERSION,
                descriptor=name,
                parameters=parameters,
            )
            return md.create_descriptor(cfg)
        except md.MDescriptorError as exc:
            raise self._convert(exc) from exc

    def make_control(self) -> md.ComputeControl:
        return md.ComputeControl()

    @staticmethod
    def compute(descriptor, batch, control: md.ComputeControl | None = None):
        try:
            if control is not None:
                return descriptor.compute(batch, control=control)
            return descriptor.compute(batch)
        except md.MDescriptorError as exc:
            raise EngineAdapter._convert(exc) from exc

    # -- batch conversion (design doc §10/§11) ----------------------------
    @staticmethod
    def to_structure_batch(frames, ids: tuple[str, ...] | None = None) -> md.StructureBatch:
        """frames: sequence of datasets.base.DatasetFrame -> engine batch.

        Isolated frames must carry a zero cell and pbc=(0,0,0); periodic frames
        a nonsingular cell and pbc=(1,1,1). Mixed periodicity in one batch is
        rejected (engine contract).
        """
        if not frames:
            raise AppError("INVALID_PARAMS", "no frames to compute")
        n = len(frames)
        natoms = [len(f.numbers) for f in frames]
        offsets = np.zeros(n + 1, dtype=np.int64)
        np.cumsum(natoms, out=offsets[1:])
        numbers = np.concatenate([np.asarray(f.numbers) for f in frames])
        positions = np.concatenate(
            [np.asarray(f.positions, dtype=np.float64) for f in frames]
        )
        cells = np.stack([np.asarray(f.cell, dtype=np.float64) for f in frames])
        pbc = np.stack([np.asarray(f.pbc, dtype=bool) for f in frames])
        if ids is None:
            ids = tuple(f.id if f.id else f"frame_{i}" for i, f in enumerate(frames))
        try:
            return md.StructureBatch(
                numbers=numbers,
                positions=positions,
                cells=cells,
                pbc=pbc,
                offsets=offsets,
                ids=tuple(ids),
            )
        except (ValueError, TypeError) as exc:
            raise AppError(
                "UNSUPPORTED_PERIODICITY",
                f"engine rejected StructureBatch: {exc}",
            ) from exc

    # -- errors ------------------------------------------------------------
    @staticmethod
    def _convert(exc: md.MDescriptorError) -> AppError:
        for exc_type, code in _ERROR_MAP:
            if isinstance(exc, exc_type):
                return AppError(code, str(exc))
        return AppError(INTERNAL_ERROR, f"engine error: {exc}")


def engine_exception_to_app_error(exc: Exception) -> AppError:
    """Best-effort conversion for stray engine exceptions escaping compute."""
    if isinstance(exc, md.MDescriptorError):
        return EngineAdapter._convert(exc)
    if isinstance(exc, MemoryError):
        return AppError("OUT_OF_MEMORY", str(exc))
    if isinstance(exc, FileNotFoundError):
        return AppError(MODEL_NOT_FOUND, str(exc))
    return AppError(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
