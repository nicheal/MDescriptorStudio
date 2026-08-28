"""Backend entry: wires services, emits backend.ready, serves NDJSON on stdio."""

from __future__ import annotations

import logging
import platform
import sys

from . import __version__
from .config import data_dir
from .errors import INVALID_PARAMS
from .logging_setup import setup_logging
from .mdescriptor_adapter import EngineAdapter
from .protocol import frames
from .protocol.server import Server
from .services.analysis_service import AnalysisService
from .services.dataset_service import DatasetService
from .services.descriptor_service import DescriptorService
from .services.job_service import JobService
from .services.result_service import ResultService
from .storage.database import Database

log = logging.getLogger(__name__)


def build_methods(db, jobs, datasets, descriptors, results, analysis, settings_kv, engine_info):
    def system_info(_params):
        return {
            "backend_version": __version__,
            "protocol_version": frames.PROTOCOL_VERSION,
            "platform": platform.platform(),
            "mdescriptor_version": engine_info.get("version"),
            "mdescriptor_api_version": engine_info.get("api_version"),
        }

    def settings_get(params):
        key = params.get("key")
        if not key:
            raise AppError(INVALID_PARAMS, "'key' is required")
        return {"key": key, "value": settings_kv.get_setting(key)}

    def settings_set(params):
        key, value = params.get("key"), params.get("value")
        if not key or value is None:
            raise AppError(INVALID_PARAMS, "'key' and 'value' are required")
        settings_kv.set_setting(key, str(value))
        return {"ok": True}

    return {
        "system.info": system_info,
        "settings.get": settings_get,
        "settings.set": settings_set,
        "dataset.list": datasets.list,
        "dataset.register": datasets.register,
        "dataset.remove": datasets.remove,
        "dataset.get": datasets.get,
        "dataset.statistics": datasets.statistics,
        "dataset.frame": datasets.frame,
        "descriptor.list": descriptors.list,
        "descriptor.describe": descriptors.describe,
        "descriptor.submit": descriptors.submit,
        "job.list": jobs.list_jobs,
        "job.get": lambda params: jobs.get_job(params.get("id")),
        "job.cancel": jobs.cancel,
        "result.list": results.list,
        "result.get": results.get,
        "analysis.pca": analysis.pca,
    }


def main() -> int:
    setup_logging()
    root = data_dir()
    log.info("backend %s starting; data dir %s", __version__, root)

    db = Database(root / "database.sqlite")
    adapter = EngineAdapter()
    info = adapter.runtime_info()
    log.info("engine %s (api v%s)", info.get("version"), info.get("api_version"))

    server = Server(methods={}, on_stop=db.close)
    jobs = JobService(db, server.emit)
    datasets = DatasetService(db, adapter, jobs)
    results = ResultService(db)
    descriptors = DescriptorService(
        db, adapter, jobs, datasets, root, info.get("version", "unknown")
    )
    analysis = AnalysisService(db, jobs, results, datasets, root)
    server.methods = build_methods(
        db, jobs, datasets, descriptors, results, analysis, db, info
    )

    # handshake must be the first frame (docs/plan/02 §2)
    server.emit(
        "backend.ready",
        {
            "backend_version": __version__,
            "mdescriptor_version": info.get("version"),
            "mdescriptor_api_version": info.get("api_version"),
        },
    )
    try:
        server.serve_forever()
    finally:
        jobs.shutdown()
    log.info("backend stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
