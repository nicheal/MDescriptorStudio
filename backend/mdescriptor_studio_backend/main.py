"""Backend entry: wires services, emits backend.ready, serves NDJSON on stdio."""

from __future__ import annotations

import logging
import math
import os
import platform
import sys
import threading
import time

# Numba cache entries are pickle-bearing. Keep this defense-in-depth flag for
# releases that recognize it; AnalysisEngine also installs a runtime NullCache
# guard because the supported Numba version does not.
os.environ["NUMBA_DISABLE_JIT_CACHE"] = "1"

from . import __version__
from .analysis import AnalysisEngine, arm_analysis_warmup_gate
from .config import data_dir
from .errors import AppError, INVALID_PARAMS, JOB_NOT_FOUND
from .logging_setup import setup_logging
from .mdescriptor_adapter import EngineAdapter
from .protocol import frames
from .protocol.server import Server
from .services.analysis_service import AnalysisService
from .services.dataset_service import DatasetService
from .services.descriptor_service import DescriptorService
from .services.job_service import JobService
from .services.result_service import ResultService
from .services.update_service import UpdateService
from .storage.database import Database

log = logging.getLogger(__name__)

_ALLOWED_SETTINGS = {
    "workspace.activeDatasetId",
    "workspace.activeDescriptorRunId",
    "workspace.analysisUi",
    "ui.language",
    "compute.default_threads",
}
_MAX_SETTING_VALUE = 4096


def _configure_stdio() -> None:
    """Keep the NDJSON transport UTF-8 even when Windows uses a legacy code page."""
    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def build_methods(db, jobs, datasets, descriptors, results, analysis, settings_kv, engine_info, root, updates):
    def system_info(_params):
        return {
            "backend_version": __version__,
            "protocol_version": frames.PROTOCOL_VERSION,
            "platform": platform.platform(),
            "mdescriptor_version": engine_info.get("version"),
            "mdescriptor_api_version": engine_info.get("api_version"),
            "mdescriptor_baseline_version": engine_info.get("baseline_version"),
            "mdescriptor_descriptor_info_schema_version": engine_info.get("descriptor_info_schema_version"),
            "analysis_api_version": 1,
            "analysis_algorithm_version": "studio-analysis-2",
            "analysis_dependencies": {
                "scikit-learn": "1.9.0",
                "umap-learn": "0.5.12",
                "hdbscan": "0.8.44",
            },
            "data_dir": str(root),
            "cpu_threads": platform.os.cpu_count(),
        }

    def engine_check_update(_params):
        return updates.start_check()

    def engine_update(params):
        snap = updates.snapshot()
        target = (params or {}).get("version") or snap.get("latest")
        if not target:
            raise AppError(INVALID_PARAMS, "no target version — call engine.check_update first")
        job_id = jobs.submit("engine.update", lambda ctx: updates.update_runner(ctx, str(target)))
        return {"job_id": job_id, "target_version": str(target)}

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
        "dataset.frame": datasets.frame,
        "dataset.findings": datasets.findings,
        "dataset.exclude": datasets.exclude,
        "dataset.restore": datasets.restore,
        "dataset.excluded": datasets.excluded,
        "dataset.export_cleaned": datasets.export_cleaned,
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
        "analysis.pca": analysis.pca,
        "analysis.list": analysis.list,
        "analysis.get": analysis.get,
        "analysis.delete": analysis.delete,
        "analysis.preview": analysis.preview,
        "analysis.chunk": analysis.chunk,
        "analysis.umap": analysis.umap,
        "analysis.tsne": analysis.tsne,
        "analysis.neighbors": analysis.neighbors,
        "analysis.similarity": analysis.similarity,
        "analysis.pairwise": analysis.pairwise,
        "analysis.cluster": analysis.cluster,
        "analysis.outlier": analysis.outlier,
        "analysis.fps": analysis.fps,
        "analysis.sampling": analysis.sampling,
        "analysis.random": lambda params: analysis.submit_generic("random", params),
        "analysis.stratified": lambda params: analysis.submit_generic("stratified", params),
        "analysis.cluster_representative": lambda params: analysis.submit_generic("cluster_representative", params),
        "analysis.per_element": lambda params: analysis.submit_generic("per_element", params),
        "analysis.coverage": analysis.coverage,
        "analysis.overlap": analysis.overlap,
        "analysis.acquisition": analysis.acquisition,
        "analysis.compare": analysis.compare,
        "analysis.mantel": analysis.mantel,
        "analysis.feature_variance": analysis.feature_variance,
        "analysis.feature_correlation": analysis.feature_correlation,
        "analysis.property_correlation": analysis.property_correlation,
        "analysis.local_diversity": analysis.local_diversity,
        "analysis.kernel": analysis.kernel,
        "analysis.effective_dimension": analysis.effective_dimension,
        "analysis.trajectory": analysis.trajectory,
        "analysis.drift": analysis.drift,
        "analysis.sensitivity": analysis.sensitivity,
        "analysis.perturbation_sensitivity": analysis.perturbation_sensitivity,
        "analysis.export": analysis.export,
        "engine.check_update": engine_check_update,
        "engine.update": engine_update,
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

    # note: no on_stop here — the db must outlive the job pool; main() closes it
    server = Server(methods={})
    server.warmup_finished = threading.Event()
    jobs = JobService(db, server.emit)
    datasets = DatasetService(db, adapter, jobs)
    results = ResultService(db, root)
    descriptors = DescriptorService(
        db, adapter, jobs, datasets, root, info.get("version", "unknown")
    )
    analysis = AnalysisService(db, jobs, results, datasets, root)
    updates = UpdateService(server.emit, info.get("version", "unknown"))
    server.methods = build_methods(
        db, jobs, datasets, descriptors, results, analysis, db, info, root, updates
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
    # sklearn/numba chain and scipy.spatial cKDTree). Runtime paths — dataset
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
    server.emit(
        "backend.ready",
        {
            "backend_version": __version__,
            "mdescriptor_version": info.get("version"),
            "mdescriptor_api_version": info.get("api_version"),
            "mdescriptor_baseline_version": info.get("baseline_version"),
            "mdescriptor_descriptor_info_schema_version": info.get("descriptor_info_schema_version"),
            "analysis_api_version": 1,
            "analysis_algorithm_version": "studio-analysis-2",
        },
    )
    # non-blocking PyPI check so the UI can offer an engine update (ADR-2)
    updates.start_check()

    def _warmup() -> None:
        # Engine first, then analysis dependencies: one thread, one import
        # pass. Safe alongside serve_forever only because the stdin loop
        # polls instead of blocking in ReadFile (see Server.serve_forever —
        # a blocking stdin read concurrent with these imports deadlocks the
        # Windows DLL loader).
        adapter.warmup()
        log.info("engine warmup complete")
        analysis_dependencies = AnalysisEngine.warmup()
        log.info("analysis dependencies: %s", analysis_dependencies)
        server.warmup_finished.set()

    threading.Thread(target=_warmup, name="warmup", daemon=True).start()
    log.info("backend ready; warmup continues in background")
    try:
        server.serve_forever()
    finally:
        # job pool first (it finalizes rows), then the database (red-team #3)
        jobs.shutdown()
        db.close()
    log.info("backend stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
