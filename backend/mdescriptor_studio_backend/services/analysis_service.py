"""AnalysisService: generic analysis jobs on stored descriptor results.

Atom/pair-level results are mean-pooled per structure so every point maps to
exactly one frame for the analysis -> Explore reverse jump.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

from ..analysis import ANALYSIS_REGISTRY
from ..analysis.sampling import group_sizes, sqrt_quota
from .analysis_helpers import (
    ANALYSIS_ALGORITHM_VERSION,
    _ANALYSIS_SCHEMA_VERSION,
    _MAX_PREVIEW_POINTS,
    _block_names,
)
from .analysis_loader import AnalysisDataMixin
from .preview_service import AnalysisPreviewMixin
from .artifact_service import AnalysisArtifactMixin
from .export_service import AnalysisExportMixin
from .job_runner import AnalysisRunMixin
from ..errors import (
    ANALYSIS_INPUT_INVALID,
    ARTIFACT_INVALID,
    AppError,
    INVALID_PARAMS,
    RESULT_INCOMPATIBLE,
)
from .result_service import ResultService
from .analysis_artifact_store import AnalysisArtifactStore
from ..storage.database import Database
from .job_service import JobService
from ..security import UnsafePathError, escape_like

log = logging.getLogger(__name__)


ANALYSIS_METHOD_CATALOG = ANALYSIS_REGISTRY.catalog()
# These methods deliberately normalize parameters before submission. They stay
# explicit and must not be replaced by the generated pass-through methods.
_EXPLICIT_RPC_METHODS = frozenset({
    "cluster", "outlier", "sampling",
    "feature_variance", "feature_correlation", "property_correlation", "local_diversity",
})
_GENERIC_TYPES = frozenset(
    name for name in ANALYSIS_METHOD_CATALOG
    if name not in _EXPLICIT_RPC_METHODS
)


class AnalysisService(
    AnalysisRunMixin,
    AnalysisDataMixin,
    AnalysisArtifactMixin,
    AnalysisPreviewMixin,
    AnalysisExportMixin,
):
    def __init__(self, db: Database, jobs: JobService, results: ResultService, datasets, data_dir: Path):
        self.db = db
        self.jobs = jobs
        self.results = results
        self.datasets = datasets
        self.data_dir = Path(data_dir).resolve(strict=False)
        self._artifacts = AnalysisArtifactStore(
            self.data_dir,
            algorithm_version=ANALYSIS_ALGORITHM_VERSION,
            schema_version=_ANALYSIS_SCHEMA_VERSION,
        )
        # RPC requests are handled concurrently. Serialize only the
        # cache-lookup/create section so identical requests cannot enqueue
        # duplicate analysis jobs; calculations still run in JobService.
        self._submit_lock = threading.Lock()
        # Element-set labels of completed runs are immutable; a tiny LRU keeps
        # the grouped-FPS quota preview cheap across parameter twiddling.
        self._group_labels_cache: dict[tuple[str, str, str | None], np.ndarray] = {}


    # -- generic Analysis API -------------------------------------------------
    # Every new analysis uses the common artifact/cache contract below. The
    # generic PCA writer also emits pca.json so old clients can continue to use
    # result.get_pca as a compatibility reader.

    def list(self, params: dict) -> list[dict]:
        """List analysis metadata without loading any large array."""
        params = params or {}
        sql = "SELECT * FROM analysis_runs"
        conditions: list[str] = []
        args: list[object] = []
        if params.get("run_id"):
            run_id = str(params["run_id"])
            conditions.append("(descriptor_run_id = ? OR input_run_ids_json LIKE ? ESCAPE '!')")
            args.extend([run_id, f'%"{escape_like(run_id)}"%'])
        if params.get("analysis_type"):
            conditions.append("analysis_type = ?")
            args.append(params["analysis_type"])
        if params.get("status"):
            conditions.append("status = ?")
            args.append(params["status"])
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at DESC, id DESC LIMIT 500"
        rows = self.db.query(sql, tuple(args))
        return [self._public_analysis_row(row) for row in rows]

    def get(self, params: dict) -> dict:
        row = self._analysis_row(params.get("analysis_id") or params.get("id"))
        return self._public_analysis_row(row, include_preview=True)

    def delete(self, params: dict) -> dict:
        analysis_id = params.get("analysis_id") or params.get("id")
        row = self._analysis_row(analysis_id)
        if row["status"] in ("QUEUED", "RUNNING"):
            raise AppError(RESULT_INCOMPATIBLE, f"analysis {analysis_id} is {row['status']}")
        try:
            artifact_path = self._managed_artifact_path(str(analysis_id), row.get("result_path")) if row.get("result_path") else None
        except (TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(ARTIFACT_INVALID, "stored analysis artifact path is invalid") from exc
        self.db.execute("DELETE FROM jobs WHERE analysis_run_id = ?", (analysis_id,))
        self.db.execute("DELETE FROM analysis_runs WHERE id = ?", (analysis_id,))
        self._rmtree_quiet(artifact_path)
        return {"ok": True, "analysis_id": analysis_id}

    def preview(self, params: dict) -> dict:
        """Return a bounded identity/plot preview from a completed artifact."""
        row = self._analysis_row(params.get("analysis_id") or params.get("id"))
        self._require_artifact(row)
        try:
            offset = max(0, int(params.get("offset", 0)))
            limit = min(_MAX_PREVIEW_POINTS, max(1, int(params.get("limit", 5000))))
        except (TypeError, ValueError) as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "offset and limit must be integers") from exc
        raw = row.get("preview_json")
        preview = self._json_load(raw, {})
        if not isinstance(preview, dict):
            preview = {}
        has_bounded_data = False
        if isinstance(preview.get("points"), list):
            preview = {**preview, "points": preview["points"][offset : offset + limit]}
            has_bounded_data = True
        if isinstance(preview.get("rows"), list):
            preview = {**preview, "rows": preview["rows"][offset : offset + limit]}
            has_bounded_data = True
        # Feature-variance previews are feature-oriented rather than row-
        # oriented.  Their complete scalar records are already persisted in
        # ``features``; rebuilding generic rows here would reload the entire
        # descriptor matrix just to produce an unused empty table.
        if row.get("analysis_type") == "feature_variance" and isinstance(preview.get("features"), list):
            has_bounded_data = True
        if not has_bounded_data:
            # Rebuild a row-oriented preview when a result was written by an
            # earlier generic backend that only stored arrays.
            preview["rows"] = self._rows_from_artifact(row, offset, limit)
        preview.update({"analysis_id": row["id"], "offset": offset, "limit": limit})
        return preview

    def chunk(self, params: dict) -> dict:
        """Read a bounded slice of one named artifact array."""
        row = self._analysis_row(params.get("analysis_id") or params.get("id"))
        self._require_artifact(row)
        name = str(params.get("array") or params.get("array_name") or "")
        manifest = self._json_load(row.get("artifact_manifest_json"), {})
        files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
        file_meta = files.get(name)
        if not isinstance(file_meta, dict):
            raise AppError(ANALYSIS_INPUT_INVALID, f"array {name!r} is not present in analysis artifact")
        try:
            root = self._managed_artifact_path(str(row["id"]), row.get("result_path"))
        except (TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(ARTIFACT_INVALID, "stored analysis artifact path is invalid") from exc
        path = self._artifact_file(root, file_meta.get("path"))
        if path is None or not path.is_file():
            raise AppError(ARTIFACT_INVALID, "missing analysis artifact array")
        try:
            array = np.load(path, mmap_mode="r", allow_pickle=False)
            offset = max(0, int(params.get("offset", 0)))
            limit = min(_MAX_PREVIEW_POINTS, max(1, int(params.get("limit", 2000))))
        except (OSError, TypeError, ValueError) as exc:
            raise AppError(ARTIFACT_INVALID, f"cannot read analysis array {name!r}: {exc}") from exc
        if array.ndim == 0:
            data = array.reshape(1)
            offset = min(offset, 1)
            stop = min(offset + limit, 1)
            chunk = data[offset:stop]
        else:
            offset = min(offset, array.shape[0])
            stop = min(offset + limit, array.shape[0])
            chunk = array[offset:stop]
        # A chunk is row-bounded and also value-bounded.  The latter prevents a
        # single very-wide descriptor row from turning IPC into a large JSON
        # transport; callers can page columns with column_start/column_end.
        if chunk.ndim == 2:
            col_start = max(0, int(params.get("column_start", 0)))
            col_end = min(chunk.shape[1], int(params.get("column_end", col_start + 256)))
            col_end = max(col_start, col_end)
            chunk = chunk[:, col_start:col_end]
        values = chunk.tolist()
        return {
            "analysis_id": row["id"],
            "array": name,
            "offset": offset,
            "next_offset": stop,
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "data": values,
        }

    # Wrappers that normalize parameters (or funnel to a dedicated runner)
    # before submit_generic stay explicit; the pure pass-throughs listed in
    # _GENERIC_TYPES are generated at the end of this module, which keeps the
    # supported analysis vocabulary discoverable to frontend code and
    # integration tests in one place.
    def cluster(self, params: dict) -> dict:
        algorithm = str(params.get("algorithm") or params.get("method") or "kmeans").lower()
        return self.submit_generic(algorithm, params)

    def outlier(self, params: dict) -> dict:
        algorithm = str(params.get("algorithm") or params.get("method") or "lof").lower()
        return self.submit_generic(algorithm, params)

    def feature_variance(self, params: dict) -> dict:
        # The richer per-feature contract supersedes the legacy top-K-only
        # artifact. Keep this revision in the canonical parameters so old
        # feature-variance artifacts cannot be mistaken for the new schema,
        # without invalidating caches for unrelated analysis modules.
        params = {**dict(params or {}), "feature_variance_schema": 2}
        return self.submit_generic("feature_variance", params)

    def feature_correlation(self, params: dict) -> dict:
        params = dict(params or {})
        params["method"] = str(params.get("method") or "pearson").lower()
        params["feature_correlation_schema"] = 3
        return self.submit_generic("feature_correlation", params)

    def property_correlation(self, params: dict) -> dict:
        params = dict(params or {})
        try:
            params["folds"] = int(params.get("folds") or 5)
            params["reliability_k"] = int(params.get("reliability_k") or 5)
            params["sparse_quantile"] = float(params.get("sparse_quantile") or 0.90)
            params["ood_quantile"] = float(params.get("ood_quantile") or 0.99)
        except (TypeError, ValueError) as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "property correlation CV and reliability settings must be numeric") from exc
        params["distance_metric"] = str(params.get("distance_metric") or "euclidean").lower()
        params["property_correlation_schema"] = 2
        return self.submit_generic("property_correlation", params)

    def local_diversity(self, params: dict) -> dict:
        params = {**dict(params or {}), "mode": "atom"}
        return self.submit_generic("local_diversity", params)

    def sampling(self, params: dict) -> dict:
        algorithm = str(params.get("algorithm") or params.get("method") or "random").lower()
        return self.submit_generic(algorithm, params)


    def fps_quota(self, params: dict) -> dict:
        """√N_g quota preview for grouped FPS: budget per element set, before running."""
        params = dict(params or {})
        run_id = params.get("run_id")
        if not run_id:
            raise AppError(INVALID_PARAMS, "'run_id' is required")
        run = self._usable_run(str(run_id))
        mode = str(params.get("mode") or "structure")
        if mode not in ("structure", "atom"):
            raise AppError(ANALYSIS_INPUT_INVALID, "mode must be structure or atom")
        try:
            n_samples = int(params.get("n_samples") or 1000)
        except (TypeError, ValueError) as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "n_samples must be an integer") from exc
        if n_samples < 1:
            raise AppError(ANALYSIS_INPUT_INVALID, "n_samples must be >= 1")
        view_id = params.get("view_id")
        view = self._usable_view(str(view_id), run["dataset_id"]) if view_id else None
        samples = self._load_samples(run, {"mode": mode}, "fps", view_id=view_id)
        labels = self._element_group_labels(run, samples, (view["selection_hash"] if view else "", samples.n_samples))
        names, sizes = group_sizes(labels)
        quota = sqrt_quota(sizes, min(n_samples, samples.n_samples))
        payload = {
            "run_id": run["id"],
            "mode": mode,
            "n_candidates": int(samples.n_samples),
            "requested_samples": int(n_samples),
            "groups": [
                {"group": name, "structures": int(size), "quota": int(share)}
                for name, size, share in zip(names, sizes, quota)
            ],
        }
        # A composite space changes what the grouping runs on and how large the
        # space is; report both so the preview matches the eventual run.
        block_names = _block_names(params)
        if block_names:
            element_list = self._dataset_elements(run)
            blocks = self._sampling_blocks(run, samples, block_names, params, element_list)
            payload["blocks"] = [
                {"name": block.name, "dimension": int(np.asarray(block.values).shape[1]), "weight": float(block.weight), "scaling": block.scaling}
                for block in blocks
            ]
            payload["sampling_dimension"] = int(sum(item["dimension"] for item in payload["blocks"]))
        return self._json_safe(payload)


    # Composite sampling blocks.  ``descriptor`` and ``descriptor_summary``
    # derive from the descriptor matrix; the rest are physical signals read from
    # the dataset.  Names are the API vocabulary shared with the GUI.


def _generic_pass_through(analysis_type: str):
    def method(self, params: dict) -> dict:
        return self.submit_generic(analysis_type, params)

    return method


for _analysis_type in sorted(_GENERIC_TYPES):
    setattr(AnalysisService, _analysis_type, _generic_pass_through(_analysis_type))
