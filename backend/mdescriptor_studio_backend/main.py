"""Backend entry: wires services, emits backend.ready, serves NDJSON on stdio."""

from __future__ import annotations

import importlib.metadata
import logging
import math
import os
import platform
import sys
import threading
import time

from . import __version__
from .analysis import ANALYSIS_REGISTRY, arm_analysis_warmup_gate, warmup as warmup_analysis
from .config import data_dir
from .errors import AppError, INVALID_PARAMS, JOB_NOT_FOUND
from .logging_setup import setup_logging
from .mdescriptor_adapter import EngineAdapter
from .protocol import frames
from .protocol.server import Server
from .services.analysis_service import ANALYSIS_ALGORITHM_VERSION, AnalysisService
from .services.dataset_frame_service import DatasetFrameService
from .services.dataset_service import DatasetService
from .services.dataset_view_service import DatasetViewService
from .services.descriptor_service import DescriptorService
from .services.job_service import JobService
from .services.result_service import ResultService
from .storage.database import Database

log = logging.getLogger(__name__)

# Bumped when an analysis payload changes shape in a way old clients cannot read.
ANALYSIS_API_VERSION = 1

_ALLOWED_SETTINGS = {
    "workspace.activeDatasetId",
    "workspace.activeDescriptorRunId",
    "workspace.analysisUi",
    "workspace.analysisSlots",
    "ui.language",
    "compute.default_threads",
}
_MAX_SETTING_VALUE = 4096


def _dependency_version(dist_name: str) -> str | None:
    try:
        return importlib.metadata.version(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _configure_stdio() -> None:
    """Keep the NDJSON transport UTF-8 even when Windows uses a legacy code page."""
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def _version_payload(engine_info: dict) -> dict:
    """The versions the handshake reports, and that system.info reports again.

    Both sides tell the UI which engine and analysis build they are talking to,
    and the handshake is what the version gate compares against. Writing the
    field list twice meant a version added to one answer could be missing from
    the other - and the UI would read that as "the backend does not report it".
    """
    return {
        "backend_version": __version__,
        "mdescriptor_version": engine_info.get("version"),
        "mdescriptor_api_version": engine_info.get("api_version"),
        "mdescriptor_baseline_version": engine_info.get("baseline_version"),
        "mdescriptor_descriptor_info_schema_version": engine_info.get("descriptor_info_schema_version"),
        "analysis_api_version": ANALYSIS_API_VERSION,
        "analysis_algorithm_version": ANALYSIS_ALGORITHM_VERSION,
    }


def build_methods(jobs, datasets, views, frame_service, descriptors, results, analysis, settings_kv, engine_info, root):
    def system_info(_params):
        return {
            **_version_payload(engine_info),
            "protocol_version": frames.PROTOCOL_VERSION,
            "platform": platform.platform(),
            "analysis_dependencies": {
                name: _dependency_version(name)
                for name in ("scikit-learn", "hdbscan")
            },
            "data_dir": str(root),
            "cpu_threads": os.cpu_count(),
        }

    def settings_get(params):
        key = params.get("key")
        if not isinstance(key, str) or key not in _ALLOWED_SETTINGS:
            raise AppError(INVALID_PARAMS, "'key' is required")
        return {"key": key, "value": settings_kv.get_setting(key)}

    def settings_set(params):
        key, value = params.get("key"), params.get("value")
        if not isinstance(key, str) or key not in _ALLOWED_SETTINGS or value is None:
            raise AppError(INVALID_PARAMS, "'key' and 'value' are required")
        if (
            not isinstance(value, (str, int, float, bool))
            or isinstance(value, float) and not math.isfinite(value)
            or len(str(value)) > _MAX_SETTING_VALUE
        ):
            raise AppError(INVALID_PARAMS, "setting value is invalid")
        settings_kv.set_setting(key, str(value))
        return {"ok": True}

    def job_get(params):
        row = jobs.get_job(params.get("id"))
        if row is None:
            raise AppError(JOB_NOT_FOUND, f"job {params.get('id')} does not exist")
        return row

    analysis_methods = {
        "analysis.list": analysis.list,
        "analysis.get": analysis.get,
        "analysis.delete": analysis.delete,
        "analysis.preview": analysis.preview,
        "analysis.chunk": analysis.chunk,
        "analysis.fps_quota": analysis.fps_quota,
        "analysis.export": analysis.submit_export,
    }
    for analysis_type in ANALYSIS_REGISTRY.names():
        # Registry entries own the RPC vocabulary: an algorithm added to the
        # registry becomes callable without another main/service edit. Explicit
        # normalizing methods (cluster/outlier/sampling, ...) win because the
        # generated pass-throughs skip them, so a registry name that resolves
        # to no attribute is a wiring bug and must fail here, not silently.
        analysis_methods[f"analysis.{analysis_type}"] = getattr(analysis, analysis_type)

    return {
        "system.info": system_info,
        "settings.get": settings_get,
        "settings.set": settings_set,
        "dataset.list": datasets.list,
        "dataset.register": datasets.register,
        "dataset.remove": datasets.remove,
        "dataset.rename": datasets.rename,
        "dataset.get": datasets.get,
        "dataset.statistics": datasets.statistics,
        "dataset.rescan": datasets.rescan,
        "dataset.frame": frame_service.frame,
        "dataset.findings": datasets.findings,
        "dataset.view.list": views.list,
        "dataset.view.create": views.create,
        "dataset.view.rename": views.rename,
        "dataset.view.remove": views.remove,
        "dataset.view.split": views.split,
        "dataset.view.materialize": views.materialize,
        "descriptor.list": descriptors.list,
        "descriptor.describe": descriptors.describe,
        "descriptor.submit": descriptors.submit,
        "job.list": jobs.list_jobs,
        "job.get": job_get,
        "job.cancel": lambda params: jobs.cancel(params.get("id")),
        "result.list": results.list,
        "result.get": results.get,
        "result.remove": results.remove,
        "result.get_pca": results.get_pca,
        "result.heatmap": results.heatmap,
        **analysis_methods,
    }


def main() -> int:
    _configure_stdio()
    root = data_dir()
    setup_logging(root)
    log.info("backend %s starting; data dir %s", __version__, root)

    db = Database(root / "database.sqlite")
    adapter = EngineAdapter()
    info = adapter.runtime_info()
    log.info("engine %s (api v%s)", info.get("version"), info.get("api_version"))

    server = Server(methods={})
    server.warmup_finished = threading.Event()
    jobs = JobService(db, server.emit)
    datasets = DatasetService(db, adapter, jobs, root)
    views = DatasetViewService(datasets)
    frame_service = DatasetFrameService(datasets)
    results = ResultService(db, root)
    # JobService has already settled the rows a crash left non-terminal, so the
    # directories those runs abandoned are now unreachable and reclaimable.
    results.sweep_abandoned()
    descriptors = DescriptorService(
        db, adapter, jobs, datasets, root, info.get("version", "unknown")
    )
    analysis = AnalysisService(db, jobs, results, datasets, root)
    server.methods = build_methods(
        jobs, datasets, views, frame_service, descriptors, results, analysis, db, info, root
    )

    # Arm both warmup gates before the handshake: the heavy warmups below run
    # on a background thread, and gated entry points (EngineAdapter.build,
    # analysis engine _safe_import) hold requests until that import pass is
    # done instead of racing it.
    adapter.arm_deferred_warmup()
    arm_analysis_warmup_gate()

    # First-import every remaining DLL-bearing package here, serially, before
    # any request can run: concurrent first-imports of native packages
    # deadlock the Windows DLL loader on this platform (observed between the
    # sklearn chain and scipy.spatial cKDTree). Runtime paths — dataset
    # scan jobs, DeepMD reads — only touch these preloaded modules afterwards;
    # the numeric stack itself is imported by the background warmup pass,
    # whose analysis consumers wait on the gate above.
    t_preload = time.perf_counter()
    import scipy.spatial  # noqa: F401  (cKDTree for dataset scan jobs)
    import dpdata  # noqa: F401  (DeepMD dataset reader)

    log.info("native preloads done in %.2fs", time.perf_counter() - t_preload)

    # handshake must be the first frame (docs/plan/02 §2). It deliberately
    # precedes the warmups so the UI opens immediately; the warmups continue
    # on the background thread below.
    server.emit("backend.ready", _version_payload(info))
    def _warmup() -> None:
        # Engine first, then analysis dependencies: one thread, one import
        # pass. Safe alongside serve_forever only because the stdin loop
        # polls instead of blocking in ReadFile (see Server.serve_forever —
        # a blocking stdin read concurrent with these imports deadlocks the
        # Windows DLL loader).
        # Each step is guarded on its own: the analysis warmup releases
        # the analysis gate in its finally, so letting an engine failure skip
        # it would hang every later analysis request on _safe_import's
        # untimed wait, and warmup_finished keeps the polled stdin loop from
        # spinning at 1 ms for the life of the process.
        try:
            adapter.warmup()
            log.info("engine warmup complete")
        except Exception:  # noqa: BLE001 - a failed warmup must not strand requests
            log.exception("engine warmup failed")
        try:
            analysis_dependencies = warmup_analysis()
            log.info("analysis dependencies: %s", analysis_dependencies)
        except Exception:  # noqa: BLE001
            log.exception("analysis warmup failed")
        finally:
            server.warmup_finished.set()

    threading.Thread(target=_warmup, name="warmup", daemon=True).start()
    log.info("backend ready; warmup continues in background")
    exit_code = 0
    try:
        server.serve_forever()
    except Exception:  # noqa: BLE001 - the sidecar must never die with a traceback only a log sees
        log.exception("protocol server crashed")
        exit_code = 1
    finally:
        # job pool first: shutdown() settles job/run rows, so it must run while
        # the database is still open
        jobs.shutdown()
        db.close()
    log.info("backend stopped")
    logging.shutdown()
    # Executor threads are non-daemon and would be joined at interpreter exit;
    # a runner stuck in a long native call (t-SNE fit, SVD) would hang the
    # process here. shutdown() already cancelled what it could and settled all
    # durable rows, so exit hard instead of joining.
    os._exit(exit_code)


if __name__ == "__main__":
    sys.exit(main())
