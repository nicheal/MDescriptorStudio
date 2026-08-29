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

    # -- warmup --------------------------------------------------------------
    def warmup(self) -> None:
        """Build every registered descriptor once on the calling (main) thread.

        create_descriptor lazily imports native extension modules; resolving
        those imports while worker threads (or a stdin reader thread) are live
        deadlocks the import machinery (observed on 0.2.3/win). Running the
        warmup before any thread exists removes the lazy import entirely.
        """
        import numpy as np

        numbers = np.array([1], dtype=np.int64)
        batch = md.StructureBatch(
            numbers=numbers,
            positions=np.array([[0.0, 0.0, 0.0]]),
            cells=np.zeros((1, 3, 3)),
            pbc=np.zeros((1, 3), dtype=bool),
            offsets=np.array([0, 1]),
            ids=("warmup",),
        )
        for name in self.list_names():
            schema = self.schema(name)
            params: dict = {}
            for key, meta in schema.get("parameters", {}).items():
                if not meta.get("required"):
                    continue
                ptype = meta.get("type")
                if ptype == "species":
                    params[key] = [1]
                elif ptype == "integer":
                    params[key] = int(meta.get("default") or 1)
                elif ptype == "number":
                    params[key] = float(meta.get("default") if meta.get("default") is not None else 1.0)
                elif ptype == "boolean":
                    params[key] = bool(meta.get("default", False))
                elif ptype == "enum":
                    params[key] = (meta.get("enum") or [""])[0]
                elif ptype in ("array",):
                    params[key] = meta.get("default") or []
                elif ptype == "model":
                    continue  # bundled resource resolves automatically
                else:
                    default = meta.get("default")
                    if default is not None:
                        params[key] = default
            try:
                descriptor = self.build(name, params)
                self.compute(descriptor, batch)
            except Exception as exc:  # noqa: BLE001 - warmup is best-effort per descriptor
                log.info("warmup skipped %s: %s", name, exc)
            else:
                log.info("warmup ok: %s", name)

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
