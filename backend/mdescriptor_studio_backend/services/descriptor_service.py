"""DescriptorService: registry list, describe, submit (validation, cache, compute job)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..datasets import compute_fingerprint
from ..errors import (
    AppError,
    DATASET_NOT_FOUND,
    DESCRIPTOR_CONFIGURATION_ERROR,
    INVALID_PARAMS,
    UNSUPPORTED_PERIODICITY,
)
from ..mdescriptor_adapter import engine_exception_to_app_error
from ..storage.database import Database
from .dataset_service import DatasetService
from .job_service import JobService

log = logging.getLogger(__name__)

_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731

_VALIDATE_TYPES = {"integer", "number", "boolean", "string", "enum", "array", "object", "model", "species"}

# Loading frames occupies [0, 0.1) of the job bar; engine compute owns the rest
# (mapped from ComputeControl counters by _report_engine_progress). Without
# this split the load phase drives the bar to 100% before compute even starts.
_LOAD_BAR_SHARE = 0.1


def _process_rss_bytes() -> int | None:
    """Return the current process resident set size when the OS exposes it."""
    try:
        if os.name == "nt":
            import ctypes

            class _Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = _Counters()
            counters.cb = ctypes.sizeof(_Counters)
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(counters),
                counters.cb,
            )
            return int(counters.WorkingSetSize) if ok else None
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        return value * (1024 if os.uname().sysname == "Linux" else 1)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


class _MemorySampler:
    """Sample process RSS during native descriptor computation."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak: int | None = None

    def _sample(self) -> None:
        value = _process_rss_bytes()
        if value is not None:
            self.peak = max(self.peak or 0, value)

    def start(self) -> None:
        self._sample()

        def loop() -> None:
            while not self._stop.wait(0.05):
                self._sample()

        self._thread = threading.Thread(target=loop, name="descriptor-memory-sampler", daemon=True)
        self._thread.start()

    def stop(self) -> int | None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self._sample()
        return self.peak


class DescriptorService:
    def __init__(
        self,
        db: Database,
        adapter,
        jobs: JobService,
        datasets: DatasetService,
        data_dir: Path,
        engine_version: str,
    ):
        self.db = db
        self.adapter = adapter
        self.jobs = jobs
        self.datasets = datasets
        self.data_dir = data_dir
        self.engine_version = engine_version

    # -- registry / describe --------------------------------------------------
    def list(self, params: dict) -> list[dict]:
        out = []
        for name in self.adapter.list_names():
            s = self.adapter.schema(name)
            out.append(
                {
                    "name": s["name"],
                    "display_name": s.get("display_name", name),
                    "description": s.get("description", ""),
                    "schema_version": s.get("schema_version"),
                    "descriptor_version": s.get("descriptor_version"),
                    "level": s.get("level"),
                    "backend": s.get("backend"),
                    "execution_engine": s.get("execution_engine"),
                    "category": s.get("category"),
                    "capabilities": s.get("capabilities", []),
                    "input": s.get("input", {}),
                }
            )
        return out

    def describe(self, params: dict) -> dict:
        name = params.get("name")
        if not name:
            raise AppError(INVALID_PARAMS, "'name' is required")
        return self.adapter.schema(name)

    # -- submit -----------------------------------------------------------------
    def submit(self, params: dict) -> dict:
        ds_id = params.get("dataset_id")
        name = params.get("descriptor_name")
        parameters = params.get("parameters") or {}
        scope = params.get("scope", "dataset")
        if scope not in ("frame", "dataset"):
            raise AppError(INVALID_PARAMS, "scope must be 'frame' or 'dataset'")
        frame_index = params.get("frame_index")
        if scope == "frame" and not isinstance(frame_index, int):
            raise AppError(INVALID_PARAMS, "scope=frame requires integer 'frame_index'")
        row = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (ds_id,))
        if row is None:
            raise AppError(DATASET_NOT_FOUND, f"dataset {ds_id} does not exist")
        # A changed source must be explicitly rescanned before a new run can
        # be created.  Existing runs remain auditable but are marked STALE by
        # DatasetService.refresh_if_changed.
        # A few pre-migration/imported rows may carry a legacy sentinel rather
        # than a SHA-256 fingerprint; keep those rows diagnosable for the job
        # lifecycle path and let the normal compute error settle them.
        if isinstance(row.get("fingerprint"), str) and len(row["fingerprint"]) == 64:
            self.datasets.refresh_if_changed(row)
        schema = self.adapter.schema(name) if name else None
        if schema is None:
            raise AppError(INVALID_PARAMS, "'descriptor_name' is required")
        self._validate_parameters(schema, parameters)
        self._check_input_capability(schema, row)

        fingerprint = compute_fingerprint(Path(row["source_path"]), row["number_of_frames"])
        canonical = json.dumps(parameters, sort_keys=True, ensure_ascii=False)
        cache_key = hashlib.sha256(
            "\x1f".join(
                [
                    fingerprint,
                    name,
                    canonical,
                    self.engine_version,
                    scope,
                    str(frame_index),
                    str(params.get("output_dtype") or ""),
                ]
            ).encode("utf-8")
        ).hexdigest()
        hit = self.db.query_one(
            "SELECT id FROM descriptor_runs WHERE cache_key = ? AND status = 'COMPLETED'",
            (cache_key,),
        )
        if hit and not params.get("force"):
            return {
                "job_id": None,
                "cache": {"existing_run_id": hit["id"], "cache_key": cache_key},
            }

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        info = self.adapter.runtime_info()
        self.db.execute(
            "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, descriptor_version,"
            " engine_version, parameters_json, scope, frame_index, output_dtype, cache_key,"
            " status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', ?)",
            (
                run_id,
                ds_id,
                name,
                (schema or {}).get("descriptor_version") or info.get("version"),
                info.get("version"),
                canonical,
                scope,
                frame_index,
                params.get("output_dtype"),
                cache_key,
                _NOW(),
            ),
        )

        def runner(ctx):
            return self._run_compute(ctx, run_id, row, name, parameters, scope, frame_index, params.get("output_dtype"))

        job_id = self.jobs.submit(
            "descriptor.compute", runner, dataset_id=ds_id, descriptor_run_id=run_id
        )
        return {"job_id": job_id, "cache": None}

    # -- validation / compat (ADR-11) ---------------------------------------------
    def _validate_parameters(self, schema: dict, parameters: dict) -> None:
        spec = schema.get("parameters", {})
        for key, value in parameters.items():
            if key not in spec:
                raise AppError(
                    DESCRIPTOR_CONFIGURATION_ERROR,
                    f"unknown parameter {key!r} for {schema['name']}",
                    {"parameter": key},
                )
            self._check_value(key, spec[key], value)
        for key, meta in spec.items():
            if meta.get("required") and key not in parameters:
                raise AppError(
                    DESCRIPTOR_CONFIGURATION_ERROR,
                    f"missing required parameter {key!r} for {schema['name']}",
                    {"parameter": key},
                )

    def _check_value(self, key: str, meta: dict, value) -> None:
        ptype = meta.get("type")
        if ptype == "object" and isinstance(value, dict):
            for sub_key, sub_value in value.items():
                sub_meta = (meta.get("properties") or {}).get(sub_key)
                if sub_meta is None:
                    raise AppError(
                        DESCRIPTOR_CONFIGURATION_ERROR,
                        f"unknown nested parameter {key}.{sub_key}",
                    )
                self._check_value(f"{key}.{sub_key}", sub_meta, sub_value)
            return
        if value is None:
            return
        try:
            if ptype == "integer":
                if not isinstance(value, int) or isinstance(value, bool):
                    raise ValueError("expected integer")
            elif ptype == "number":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("expected number")
            elif ptype == "boolean":
                if not isinstance(value, bool):
                    raise ValueError("expected boolean")
            elif ptype == "species":
                if not (isinstance(value, list) and value and all(isinstance(v, int) for v in value)):
                    raise ValueError("expected list of atomic numbers")
            elif ptype in ("array",):
                if not isinstance(value, list):
                    raise ValueError("expected array")
            elif ptype == "enum":
                if value not in (meta.get("enum") or []):
                    raise ValueError(f"expected one of {meta.get('enum')}")
            elif ptype in ("string", "model"):
                if not isinstance(value, str):
                    raise ValueError("expected string path")
        except ValueError as exc:
            raise AppError(
                DESCRIPTOR_CONFIGURATION_ERROR,
                f"parameter {key}: {exc}",
                {"parameter": key},
            ) from exc

    def _check_input_capability(self, schema: dict, dataset_row: dict) -> None:
        caps = schema.get("input") or {}
        periodicity = json.loads(dataset_row["periodicity"])
        allowed = caps.get("periodicity") or ["isolated", "fully_periodic"]
        if periodicity.get("mixed") and not caps.get("mixed_periodicity", True):
            raise AppError(
                UNSUPPORTED_PERIODICITY,
                f"{schema['name']} rejects mixed periodicity datasets",
            )
        if periodicity.get("fully_periodic") and not periodicity.get("isolated"):
            need = "fully_periodic"
        elif periodicity.get("isolated") and not periodicity.get("fully_periodic"):
            need = "isolated"
        else:
            return  # uniform datasets above; mixed handled below
        if need not in allowed:
            raise AppError(
                UNSUPPORTED_PERIODICITY,
                f"{schema['name']} supports periodicity {allowed}, dataset is {need}",
            )

    # -- compute job ------------------------------------------------------------
    def _run_compute(self, ctx, run_id, row, name, parameters, scope, frame_index, output_dtype):
        import numpy as np

        self.db.execute(
            "UPDATE descriptor_runs SET status = 'RUNNING', started_at = ? WHERE id = ?",
            (_NOW(), run_id),
        )
        ctx.check_cancelled()
        log.info("compute %s: building descriptor %s", run_id, name)
        descriptor = self.adapter.build(name, parameters)
        log.info("compute %s: built, loading frames", run_id)
        if scope == "frame":
            frames = [self.datasets._adapter_for(row).get_frame(frame_index)]
            total = 1
        else:
            adapter = self.datasets._adapter_for(row)
            frames = []
            total = max(len(adapter), 1)
            for i, frame in enumerate(adapter.iter_frames()):
                ctx.check_cancelled()
                frames.append(frame)
                if (i + 1) % 500 == 0 or (i + 1) == total:
                    ctx.progress(
                        i + 1, total, "loading frames", fraction=(i + 1) / total * _LOAD_BAR_SHARE
                    )
        batch = self.adapter.to_structure_batch(frames)
        log.info("compute %s: batch ready (%d frames)", run_id, len(frames))
        control = self.adapter.make_control()
        ctx.attach_control(control)
        # the poller owns completed/total during compute; the reset event clears
        # the loading phase's frame counters so the UI shows only the message
        # until the engine reports its first checkpoint
        ctx.progress(None, None, "computing descriptor", fraction=_LOAD_BAR_SHARE)
        stop_poll = threading.Event()
        poller = threading.Thread(
            target=self._report_engine_progress, args=(ctx, control, stop_poll), daemon=True
        )
        poller.start()
        memory_sampler = _MemorySampler()
        memory_sampler.start()
        try:
            result = self.adapter.compute(descriptor, batch, control)
        except Exception as exc:
            raise engine_exception_to_app_error(exc) from exc
        finally:
            memory_peak_bytes = memory_sampler.stop()
            # settle the poller before any later emit so no stale fraction can
            # land after "done"
            stop_poll.set()
            poller.join(timeout=1.0)
        ctx.check_cancelled()

        values = np.asarray(result.values)
        if output_dtype == "float32" and values.dtype != np.float32:
            values = values.astype(np.float32)
        run_dir = self.data_dir / "results" / f"run_{run_id.removeprefix('run_')}"
        run_dir.mkdir(parents=True, exist_ok=True)
        np.save(run_dir / "values.npy", values)
        if result.row_offsets is not None:
            np.save(run_dir / "row_offsets.npy", np.asarray(result.row_offsets))
        metadata = {
            "run_id": run_id,
            "descriptor": name,
            "engine_version": self.engine_version,
            "descriptor_version": (self.adapter.schema(name).get("descriptor_version") or self.engine_version),
            "descriptor_info_schema": self.adapter.runtime_info().get("descriptor_info_schema_version"),
            "configuration": parameters,
            "dataset_id": row["id"],
            "dataset_fingerprint": compute_fingerprint(Path(row["source_path"]), row["number_of_frames"]),
            "scope": scope,
            "frame_index": frame_index,
            "shape": list(values.shape),
            "dtype": str(values.dtype),
            "level": str(getattr(result, "level", "")),
            # Analysis may only attach atom/local-environment identities when
            # the producer declares the row semantics.  Keep this explicit in
            # the result metadata instead of inferring it from array shape.
            "row_semantics": (
                "atom"
                if any(token in str(getattr(result, "level", "")).lower() for token in ("atom", "local", "pair"))
                else "structure"
            ),
            "row_offsets_verified": result.row_offsets is not None,
            "feature_count": int(getattr(result, "feature_count", values.shape[-1] if values.ndim > 1 else 0)),
            "structure_ids": list(getattr(result, "structure_ids", []) or []),
            "created_at": _NOW(),
        }
        (run_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.db.execute(
            "UPDATE descriptor_runs SET status = 'COMPLETED', finished_at = ?, result_path = ?, memory_peak_bytes = ? WHERE id = ?",
            (_NOW(), str(run_dir), memory_peak_bytes, run_id),
        )
        ctx.progress(None, None, "done", fraction=1.0)
        return {
            "run_id": run_id,
            "shape": metadata["shape"],
            "dtype": metadata["dtype"],
            "level": metadata["level"],
            "feature_count": metadata["feature_count"],
        }

    @staticmethod
    def _report_engine_progress(ctx, control, stop: threading.Event) -> None:
        """Poll engine ComputeControl counters onto the compute phase's slice of
        the job bar ([_LOAD_BAR_SHARE, 1], 05 文档 §3 进度映射).

        0.2.5 kernels never advanced completed() — the bar then held at
        the phase base; 0.2.6 and later feed per-frame checkpoints. Runs on a daemon
        thread stopped/joined by the compute caller.
        """
        while not stop.wait(0.2):
            total = control.total()
            if total <= 0:
                continue
            completed = min(int(control.completed()), total)
            ctx.progress(
                completed,
                total,
                "computing descriptor",
                fraction=_LOAD_BAR_SHARE + (1 - _LOAD_BAR_SHARE) * completed / total,
            )
