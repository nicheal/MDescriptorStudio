"""DescriptorService: registry list, describe, submit (validation, cache, compute job)."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..datasets import compute_fingerprint
from ..errors import (
    AppError,
    DATASET_NOT_FOUND,
    DESCRIPTOR_CONFIGURATION_ERROR,
    INVALID_PARAMS,
    JOB_CANCELLED,
    OUT_OF_MEMORY,
    UNSUPPORTED_PERIODICITY,
)
from ..mdescriptor_adapter import engine_exception_to_app_error
from ..security import (
    UnsafePathError,
    ensure_no_reparse_points,
    open_text_for_write,
    remove_managed_tree,
    validate_local_path,
)
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
_MAX_COMPUTE_FRAMES = 1_000_000
_MAX_COMPUTE_ATOMS = 10_000_000
_MAX_COMPUTE_INPUT_BYTES = 2 * 1024 * 1024 * 1024
_MODEL_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def _process_rss_bytes() -> int | None:
    """Resident memory as the OS reports it, for the per-run peak.

    Windows answers with the current working set, so sampling and taking the
    max measures this compute. POSIX `ru_maxrss` is already the lifetime high
    water mark of the whole process, so on Linux/macOS `memory_peak_bytes` is
    an upper bound that includes startup imports, not this run's own cost.
    """
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
            # K32GetProcessMemoryInfo is the current export; psapi's unsuffixed
            # name is the pre-Win7 one and still resolves.
            memory_info = getattr(ctypes.windll.kernel32, "K32GetProcessMemoryInfo", None)
            if memory_info is None:
                memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
            # Declaring the signature matters: passed as the default c_int, the
            # 64-bit process handle is truncated and the call quietly fails,
            # which left memory_peak_bytes null in every Windows build.
            memory_info.restype = ctypes.c_bool
            memory_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Counters), ctypes.c_ulong]
            ok = memory_info(
                ctypes.c_void_p(-1),  # GetCurrentProcess()
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
        # RPC requests run concurrently: serialize the cache-check → insert →
        # submit section of descriptor.submit so two identical requests cannot
        # enqueue duplicate engine computes (the analysis service does the
        # same for its cache).
        self._submit_lock = threading.Lock()

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
        if scope == "frame" and (
            not isinstance(frame_index, int) or isinstance(frame_index, bool) or frame_index < 0
        ):
            raise AppError(INVALID_PARAMS, "scope=frame requires integer 'frame_index'")
        row = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (ds_id,))
        if row is None:
            raise AppError(DATASET_NOT_FOUND, f"dataset {ds_id} does not exist")
        if scope == "frame" and frame_index >= int(row["number_of_frames"] or 0):
            # Checked here, not in the job: a frame-scoped run past the end of
            # the dataset would otherwise settle as a failed job and reach the
            # user as the generic "backend failed" message.
            raise AppError(
                INVALID_PARAMS,
                f"frame_index {frame_index} is outside dataset {ds_id}"
                f" ({row['number_of_frames']} frames)",
            )
        # A changed source must be explicitly rescanned before a new run can
        # be created. Existing lightweight test/dry-run dataset services may
        # not implement the optional freshness hook.
        refresh = getattr(self.datasets, "refresh_if_changed", None)
        if callable(refresh):
            refresh(row)
        schema = self.adapter.schema(name) if name else None
        if schema is None:
            raise AppError(INVALID_PARAMS, "'descriptor_name' is required")
        self._validate_parameters(schema, parameters)
        self._check_input_capability(schema, row)
        device = str(params.get("device") or "cpu")
        declared_devices = (schema.get("execution") or {}).get("devices") or ["cpu"]
        if device not in declared_devices:
            raise AppError(
                INVALID_PARAMS,
                f"device {device!r} is not declared for {name}; supported: {', '.join(declared_devices)}",
            )

        num_threads = params.get("num_threads")
        if num_threads is not None:
            if type(num_threads) is not int or not 1 <= num_threads <= 64:
                raise AppError(INVALID_PARAMS, "num_threads must be an integer between 1 and 64")
            if device != "cpu" or not (schema.get("execution") or {}).get("num_threads"):
                raise AppError(INVALID_PARAMS, "thread count is not supported for this execution mode")

        try:
            source = validate_local_path(row["source_path"], field="dataset source path")
            fingerprint = compute_fingerprint(source, row["number_of_frames"], use_cache=False)
        except (TypeError, ValueError, OSError, UnsafePathError) as exc:
            raise AppError(INVALID_PARAMS, "dataset source is unavailable") from exc
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
                    device,
                ]
            ).encode("utf-8")
        ).hexdigest()
        if num_threads is not None:
            cache_key = hashlib.sha256(f"{cache_key}\x1fthreads={num_threads}".encode()).hexdigest()
        force = bool(params.get("force"))
        with self._submit_lock:
            hit = self.db.query_one(
                "SELECT id FROM descriptor_runs WHERE cache_key = ? AND status = 'COMPLETED'",
                (cache_key,),
            )
            if hit and not force:
                return {
                    "job_id": None,
                    "cache": {"existing_run_id": hit["id"], "cache_key": cache_key},
                }
            if not force:
                # An identical compute may already be queued or running (double
                # click, Overview + submit panel). Reuse the live job instead
                # of running the engine twice on the same input. The join with
                # jobs excludes orphaned run rows, which the restart sweep has
                # already settled to CANCELLED anyway.
                active = self.db.query_one(
                    "SELECT r.id AS run_id, j.id AS job_id FROM descriptor_runs r"
                    " JOIN jobs j ON j.descriptor_run_id = r.id"
                    " WHERE r.cache_key = ? AND r.status IN ('QUEUED', 'RUNNING')"
                    " AND j.status IN ('QUEUED', 'RUNNING')"
                    " ORDER BY j.created_at DESC, j.id DESC LIMIT 1",
                    (cache_key,),
                )
                if active:
                    return {
                        "job_id": active["job_id"],
                        "cache": {
                            "existing_run_id": active["run_id"],
                            "cache_key": cache_key,
                            "in_flight": True,
                        },
                    }

            run_id = f"run_{uuid.uuid4().hex[:12]}"
            info = self.adapter.runtime_info()
            self.db.execute(
                "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, descriptor_version,"
                " engine_version, parameters_json, scope, frame_index, output_dtype, device, cache_key,"
                " status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', ?)",
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
                    device,
                    cache_key,
                    _NOW(),
                ),
            )

            def runner(ctx):
                return self._run_compute(ctx, run_id, row, name, parameters, scope, frame_index, params.get("output_dtype"), device, num_threads)

            try:
                job_id = self.jobs.submit(
                    "descriptor.compute", runner, dataset_id=ds_id, descriptor_run_id=run_id
                )
            except Exception:
                # JobService applies backpressure before insertion. If submission
                # still fails after the run row was created, do not leave a
                # permanently QUEUED result visible in Results.
                self.db.execute("DELETE FROM descriptor_runs WHERE id = ?", (run_id,))
                raise
            return {"job_id": job_id, "cache": None}

    # -- validation / compat (ADR-11) ---------------------------------------------
    def _validate_parameters(self, schema: dict, parameters: dict) -> None:
        if not isinstance(parameters, dict):
            raise AppError(DESCRIPTOR_CONFIGURATION_ERROR, "descriptor parameters must be an object")
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
        if ptype == "object":
            if not isinstance(value, dict):
                raise AppError(DESCRIPTOR_CONFIGURATION_ERROR, f"parameter {key}: expected object", {"parameter": key})
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
                self._check_numeric_bounds(key, meta, value)
            elif ptype == "number":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("expected number")
                if not math.isfinite(float(value)):
                    raise ValueError("expected a finite number")
                self._check_numeric_bounds(key, meta, float(value))
            elif ptype == "boolean":
                if not isinstance(value, bool):
                    raise ValueError("expected boolean")
            elif ptype == "species":
                if not (
                    isinstance(value, list)
                    and value
                    and len(value) <= 118
                    and all(isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 118 for v in value)
                ):
                    raise ValueError("expected list of atomic numbers")
            elif ptype in ("array",):
                if not isinstance(value, list):
                    raise ValueError("expected array")
                minimum = meta.get("minItems", meta.get("minimum"))
                maximum = meta.get("maxItems", meta.get("maximum"))
                if minimum is not None and len(value) < int(minimum):
                    raise ValueError(f"expected at least {minimum} items")
                if maximum is not None and len(value) > int(maximum):
                    raise ValueError(f"expected at most {maximum} items")
                item_meta = meta.get("items")
                if isinstance(item_meta, dict):
                    for index, item in enumerate(value):
                        self._check_value(f"{key}[{index}]", item_meta, item)
            elif ptype == "enum":
                if value not in (meta.get("enum") or []):
                    raise ValueError(f"expected one of {meta.get('enum')}")
            elif ptype == "string":
                if not isinstance(value, str):
                    raise ValueError("expected string")
                if len(value) > int(meta.get("maxLength", 4096)) or any(ord(ch) < 0x20 for ch in value):
                    raise ValueError("string is too long or contains a control character")
            elif ptype == "model":
                self._check_model_value(key, meta, value)
            elif ptype not in _VALIDATE_TYPES:
                raise ValueError(f"unsupported schema type {ptype!r}")
        except ValueError as exc:
            raise AppError(
                DESCRIPTOR_CONFIGURATION_ERROR,
                f"parameter {key}: {exc}",
                {"parameter": key},
            ) from exc

    @staticmethod
    def _check_numeric_bounds(key: str, meta: dict, value: float | int) -> None:
        minimum = meta.get("minimum")
        maximum = meta.get("maximum")
        if minimum is not None and value < minimum:
            raise ValueError(f"must be at least {minimum}")
        if maximum is not None and value > maximum:
            raise ValueError(f"must be at most {maximum}")
        exclusive_minimum = meta.get("exclusiveMinimum")
        exclusive_maximum = meta.get("exclusiveMaximum")
        if exclusive_minimum is not None and value <= exclusive_minimum:
            raise ValueError(f"must be greater than {exclusive_minimum}")
        if exclusive_maximum is not None and value >= exclusive_maximum:
            raise ValueError(f"must be less than {exclusive_maximum}")

    @staticmethod
    def _check_model_value(key: str, meta: dict, value) -> None:
        if isinstance(value, str):
            if len(value) > 4096 or any(ord(ch) < 0x20 for ch in value):
                raise ValueError("model path is too long or contains a control character")
            try:
                path = validate_local_path(value, field=f"{key} model path")
            except UnsafePathError as exc:
                raise ValueError("model path must be an absolute local path") from exc
            if path.exists() and not path.is_file():
                raise ValueError("model path is not a regular file")
            extensions = meta.get("file_extensions") or []
            if extensions and path.suffix.lower() not in {str(item).lower() for item in extensions}:
                raise ValueError("model file extension is not supported")
            return
        if not isinstance(value, dict) or value.get("__type__") != "ModelResource":
            raise ValueError("expected a local model path or ModelResource object")
        name = value.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 255 or any(ch in name for ch in ("/", "\\", ":")):
            raise ValueError("model resource name is invalid")
        expected = value.get("expected_sha256")
        if expected is not None and (not isinstance(expected, str) or not _MODEL_SHA256_RE.fullmatch(expected)):
            raise ValueError("model resource checksum is invalid")
        identifier = value.get("identifier")
        if identifier is not None and (
            not isinstance(identifier, str)
            or len(identifier) > 255
            or any(ord(ch) < 0x20 or ch in ("/", "\\", ":") for ch in identifier)
        ):
            raise ValueError("model resource identifier is invalid")

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
            return  # mixed (checked above) or neither kind alone: nothing to require
        if need not in allowed:
            raise AppError(
                UNSUPPORTED_PERIODICITY,
                f"{schema['name']} supports periodicity {allowed}, dataset is {need}",
            )

    # -- compute job ------------------------------------------------------------
    @staticmethod
    def _frame_memory_bytes(frame) -> int:
        total = 0
        for name in ("numbers", "positions", "cell", "pbc", "forces", "virial"):
            value = getattr(frame, name, None)
            if value is not None:
                try:
                    total += int(np.asarray(value).nbytes)
                except (TypeError, ValueError):
                    pass
        return total

    @staticmethod
    def _check_batch_limits(total_atoms: int, estimated_bytes: int, frames: int) -> None:
        if frames > _MAX_COMPUTE_FRAMES or total_atoms > _MAX_COMPUTE_ATOMS:
            raise AppError(OUT_OF_MEMORY, "descriptor input exceeds the supported batch size")
        if estimated_bytes > _MAX_COMPUTE_INPUT_BYTES:
            raise AppError(OUT_OF_MEMORY, "descriptor input exceeds the supported memory budget")

    def _run_compute(self, ctx, run_id, row, name, parameters, scope, frame_index, output_dtype, device="cpu", num_threads=None):
        # The status guard mirrors job_runner._mark_run_running: cancel() has
        # already settled this row to CANCELLED, and an unguarded write here
        # would resurrect it to RUNNING so that _complete_run's CAS matches and
        # a cancelled compute commits as COMPLETED.
        self.db.execute(
            "UPDATE descriptor_runs SET status = 'RUNNING', started_at = ?"
            " WHERE id = ? AND status IN ('QUEUED', 'RUNNING')",
            (_NOW(), run_id),
        )
        ctx.check_cancelled()
        log.info("compute %s: building descriptor %s (device=%s)", run_id, name, device)
        execution = {"device": device}
        if num_threads is not None:
            execution["num_threads"] = num_threads
        descriptor = self.adapter.build(name, parameters, **execution)
        log.info("compute %s: built, loading frames", run_id)
        if scope == "frame":
            frames = [self.datasets._adapter_for(row).get_frame(frame_index)]
            total = 1
        else:
            adapter = self.datasets._adapter_for(row)
            frames = []
            total = max(len(adapter), 1)
            if total > _MAX_COMPUTE_FRAMES:
                raise AppError(OUT_OF_MEMORY, "dataset has too many frames for one descriptor batch")
        total_atoms = 0
        estimated_bytes = 0
        if scope == "frame":
            try:
                numbers = getattr(frames[0], "numbers", None)
                total_atoms = len(numbers) if numbers is not None else 0
            except TypeError:
                total_atoms = 0
            estimated_bytes = self._frame_memory_bytes(frames[0])
            self._check_batch_limits(total_atoms, estimated_bytes, 1)
        else:
            for i, frame in enumerate(adapter.iter_frames()):
                ctx.check_cancelled()
                frames.append(frame)
                try:
                    numbers = getattr(frame, "numbers", None)
                    total_atoms += len(numbers) if numbers is not None else 0
                except TypeError:
                    total_atoms += 0
                estimated_bytes += self._frame_memory_bytes(frame)
                self._check_batch_limits(total_atoms, estimated_bytes, i + 1)
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
        ensure_no_reparse_points(run_dir)
        np.save(run_dir / "values.npy", values, allow_pickle=False)
        if result.row_offsets is not None:
            np.save(run_dir / "row_offsets.npy", np.asarray(result.row_offsets), allow_pickle=False)
        metadata = {
            "run_id": run_id,
            "descriptor": name,
            "engine_version": self.engine_version,
            "descriptor_version": (self.adapter.schema(name).get("descriptor_version") or self.engine_version),
            "descriptor_info_schema": self.adapter.runtime_info().get("descriptor_info_schema_version"),
            "configuration": parameters,
            "device": device,
            "num_threads": num_threads,
            "dataset_id": row["id"],
            "dataset_fingerprint": compute_fingerprint(
                validate_local_path(row["source_path"], field="dataset source path"),
                row["number_of_frames"],
                use_cache=False,
            ),
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
            "created_at": _NOW(),
        }
        with open_text_for_write(run_dir / "metadata.json") as fh:
            fh.write(json.dumps(metadata, ensure_ascii=False, indent=2))
        self._complete_run(run_id, run_dir, memory_peak_bytes, metadata)
        ctx.progress(None, None, "done", fraction=1.0)
        return {
            "run_id": run_id,
            "shape": metadata["shape"],
            "dtype": metadata["dtype"],
            "level": metadata["level"],
            "feature_count": metadata["feature_count"],
        }

    def _complete_run(self, run_id: str, run_dir: Path, memory_peak_bytes: int | None, metadata: dict) -> None:
        """Commit a descriptor artifact only while its run is still RUNNING.

        The shape/feature/semantics triple is copied out of the artifact metadata
        into columns here, because the run *list* needs them for every row on a
        page and must not read and parse one JSON file per row to show them.
        """
        changed = self.db.execute(
            "UPDATE descriptor_runs SET status = 'COMPLETED', finished_at = ?, result_path = ?,"
            " memory_peak_bytes = ?, result_shape_json = ?, feature_count = ?, row_semantics = ?"
            " WHERE id = ? AND status = 'RUNNING'",
            (
                _NOW(),
                str(run_dir),
                memory_peak_bytes,
                json.dumps(metadata["shape"], ensure_ascii=False),
                metadata["feature_count"],
                metadata["row_semantics"],
                run_id,
            ),
        )
        if changed == 1:
            return
        try:
            remove_managed_tree(run_dir)
        except (OSError, UnsafePathError):
            log.warning("could not discard cancelled descriptor artifact", exc_info=True)
        raise AppError(JOB_CANCELLED, f"descriptor run {run_id} was cancelled")

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
