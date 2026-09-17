"""AnalysisService: generic analysis jobs on stored descriptor results.

Atom/pair-level results are mean-pooled per structure so every point maps to
exactly one frame for the analysis -> Explore reverse jump.
"""

from __future__ import annotations

import json
import hashlib
import csv
import logging
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..analysis import AnalysisEngine, SampleMatrix
from ..analysis.sampling import FeatureBlock, group_sizes, sqrt_quota
from ..errors import (
    ANALYSIS_INPUT_INVALID,
    ANALYSIS_INSUFFICIENT_SAMPLES,
    ANALYSIS_NOT_FOUND,
    ANALYSIS_STALE,
    ARTIFACT_INVALID,
    EXPORT_FAILED,
    AppError,
    INVALID_PARAMS,
    JOB_CANCELLED,
    RESULT_INCOMPATIBLE,
)
from .result_service import ResultService
from .analysis_artifact_store import AnalysisArtifactStore
from ..storage.database import Database
from .job_service import JobService
from ..security import (
    UnsafePathError,
    ensure_no_reparse_points,
    escape_like,
    open_text_for_write,
    validate_local_path,
)

log = logging.getLogger(__name__)

_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731
_ANALYSIS_SCHEMA_VERSION = 1
ANALYSIS_ALGORITHM_VERSION = "studio-analysis-4"
_MAX_PREVIEW_POINTS = 20_000

# One execution catalogue feeds generic RPC registration and the runner
# dispatch below.  Adding a new method therefore changes one table, while
# the category still makes the numerical call shape explicit.
ANALYSIS_METHOD_CATALOG = {
    "pca": "engine",
    "umap": "engine",
    "tsne": "engine",
    "neighbors": "engine",
    "similarity": "engine",
    "pairwise": "engine",
    "feature_variance": "engine",
    "feature_correlation": "engine",
    "property_correlation": "engine",
    "local_diversity": "engine",
    "kernel": "engine",
    "effective_dimension": "engine",
    "trajectory": "engine",
    "coverage": "pair",
    "overlap": "pair",
    "acquisition": "pair",
    "compare": "pair",
    "mantel": "pair",
    "drift": "pair",
    "kmeans": "cluster",
    "dbscan": "cluster",
    "hdbscan": "cluster",
    "agglomerative": "cluster",
    "hierarchical": "cluster",
    "knn": "outlier",
    "lof": "outlier",
    "isolation_forest": "outlier",
    "isolation-forest": "outlier",
    "iforest": "outlier",
    "mahalanobis": "outlier",
    "mahalanobis_distance": "outlier",
    "fps": "sampling",
    "random": "sampling",
    "stratified": "sampling",
    "cluster_representative": "sampling",
    "per_element": "sampling",
    "cluster": "wrapper",
    "outlier": "wrapper",
    "sampling": "wrapper",
    "element": "sampling",
    "sensitivity": "sensitivity",
    "perturbation_sensitivity": "perturbation",
}
_ENGINE_TYPES = frozenset(name for name, category in ANALYSIS_METHOD_CATALOG.items() if category == "engine")
_ENGINE_PAIR_TYPES = frozenset(name for name, category in ANALYSIS_METHOD_CATALOG.items() if category == "pair")
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

# Per-sample array keys the preview builder maps onto points/rows.
_PREVIEW_ARRAY_KEYS = (
    "labels", "scores", "distances", "cluster_labels", "elements",
    "coordination", "novelty", "uncertainty", "diversity",
)


def _block_names(params: dict) -> list[str]:
    """Validated composite block list from the request (empty = plain descriptor)."""
    raw = params.get("blocks")
    if raw in (None, "", []):
        return []
    if not isinstance(raw, list):
        raise AppError(ANALYSIS_INPUT_INVALID, "blocks must be a list of block names")
    names: list[str] = []
    for value in raw:
        name = str(value)
        if name not in AnalysisService.COMPOSITE_BLOCKS:
            raise AppError(ANALYSIS_INPUT_INVALID, f"unknown sampling block {name!r}", {"blocks": list(AnalysisService.COMPOSITE_BLOCKS)})
        if name not in names:
            names.append(name)
    return names


def _cell_parameters(cell: np.ndarray) -> np.ndarray:
    """a, b, c, α, β, γ of a 3×3 lattice matrix (angles in degrees)."""
    vectors = np.asarray(cell, dtype=np.float64)
    lengths = np.linalg.norm(vectors, axis=1)
    angles: list[float] = []
    for i, j in ((1, 2), (0, 2), (0, 1)):
        denominator = lengths[i] * lengths[j]
        cosine = float(vectors[i] @ vectors[j]) / denominator if denominator > 0 else 1.0
        angles.append(float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))))
    return np.asarray([*lengths.tolist(), *angles], dtype=np.float64)


def _descriptor_summary(values: np.ndarray) -> np.ndarray:
    """Per-structure descriptor mean, std, and tail magnitudes.

    A compact companion block: it lets a composite space react to where a
    structure sits in descriptor-summary space even when its raw vector is
    dominated by a few large components.
    """
    x = np.asarray(values, dtype=np.float64)
    magnitude = np.linalg.norm(x, axis=1)
    return np.column_stack([x.mean(axis=1), x.std(axis=1), magnitude])


def _composition_matrix(atom_numbers: list[list[int]], element_list: list[str]) -> np.ndarray:
    """Element fractions per structure, laid out along a shared element list."""
    from ..datasets.deepmd_symbols import _Z_TO_SYMBOL

    index = {symbol: position for position, symbol in enumerate(element_list)}
    fractions = np.zeros((len(atom_numbers), len(element_list)), dtype=np.float64)
    for row, numbers in enumerate(atom_numbers):
        total = max(len(numbers), 1)
        for z in numbers:
            symbol = _Z_TO_SYMBOL.get(int(z), f"Z{int(z)}")
            position = index.get(symbol)
            if position is not None:
                fractions[row, position] += 1.0
        fractions[row] /= total
    return fractions


def _require_finite(values: np.ndarray, name: str, description: str) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if not bool(np.isfinite(array).all()):
        missing = int((~np.isfinite(array)).sum())
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            f"the {name} block is incomplete: {missing} sample(s) lack {description}",
            {"block": name, "missing": missing},
        )
    return array


def _pool_rows(values: np.ndarray, offsets: np.ndarray) -> np.ndarray:
    """Mean-pool atom/pair rows per structure in one vectorized pass.

    Equivalent to the historical per-structure Python loop (which held the GIL
    for seconds on large atom-level runs), but each pool step also runs with
    the GIL released. Empty structures pool to zero; np.add.reduceat needs two
    quirks handled explicitly: a start index equal to len(values) (trailing
    empty structures) is out of bounds, and repeated indices (empty groups)
    return a single element instead of zero — both are masked below.
    """
    values = np.asarray(values, dtype=np.float64)
    counts = np.diff(offsets).astype(np.int64)
    pooled = np.zeros((counts.size, values.shape[1]), dtype=np.float64)
    starts = offsets[:-1]
    # offsets are monotonic and end at len(values), so starts >= len(values)
    # can only be a run of trailing empty structures — trim them.
    k = int(np.searchsorted(starts, values.shape[0], side="left"))
    if k > 0:
        sums = np.add.reduceat(values, starts[:k], axis=0)
        pooled[:k] = sums / np.maximum(counts[:k], 1)[:, None]
    pooled[counts == 0] = 0.0
    return pooled


class AnalysisService:
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

    def _result_root(self, row: dict) -> Path:
        try:
            root = self.results._managed_result_path(str(row.get("id") or ""), row.get("result_path"))
            if not root.is_dir():
                raise OSError("descriptor result directory is missing")
            ensure_no_reparse_points(root)
            return root
        except (OSError, TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(RESULT_INCOMPATIBLE, "descriptor result artifact is unavailable") from exc

    def _row_offsets(self, run_row: dict) -> np.ndarray | None:
        """Row-offsets file of a descriptor result, if present."""
        path = self._result_root(run_row)
        offsets_file = path / "row_offsets.npy"
        ensure_no_reparse_points(offsets_file)
        return np.asarray(np.load(offsets_file, allow_pickle=False), dtype=np.int64) if offsets_file.is_file() else None

    @staticmethod
    def _run_frame_values(run_row: dict, count: int) -> np.ndarray:
        if run_row.get("scope") == "frame":
            return np.full(count, int(run_row.get("frame_index") or 0), dtype=np.int64)
        return np.arange(count, dtype=np.int64)

    def _frame_properties(self, run_row: dict, n_points: int) -> list[dict]:
        """Energy/force/volume per frame for color-by (aligned to frame index)."""
        frame_scope = run_row["scope"] == "frame"
        need = max(n_points, (run_row["frame_index"] + 1) if frame_scope else n_points)
        props: list[dict] = [{"energy_per_atom": None, "force_max": None, "volume": None} for _ in range(need)]
        dataset_row = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset_row is None:
            return props
        adapter = self.datasets._adapter_for(dataset_row)
        indices = [run_row["frame_index"]] if frame_scope else list(range(min(n_points, len(adapter))))
        for i in indices:
            try:
                f = adapter.get_frame(i)
            except AppError:
                continue
            natoms = len(f.numbers)
            entry = {
                "energy_per_atom": f.energy / natoms if f.energy is not None and natoms else None,
                "force_max": None,
                "volume": None,
            }
            if f.forces is not None and len(f.forces):
                mags = np.linalg.norm(np.asarray(f.forces), axis=1)
                entry["force_max"] = round(float(mags.max()), 5)
            det = abs(float(np.linalg.det(np.asarray(f.cell))))
            if det > 1e-8:
                entry["volume"] = round(det, 4)
            props[i] = entry
        return props

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

    def submit_generic(self, analysis_type: str, params: dict) -> dict:
        """Create or reuse a generic analysis job.

        The returned shape is intentionally identical for every long-running
        module, including sampling and export.  The cache key contains all
        input run IDs and normalized parameters, plus this backend algorithm
        version, so changing implementation cannot reuse an old artifact.
        """
        params = dict(params or {})
        if analysis_type == "pca":
            mode = str(params.get("mode") or "structure")
            if mode not in ("structure", "atom"):
                raise AppError(INVALID_PARAMS, f"mode must be 'structure' or 'atom', got {mode!r}")
            preprocess = str(params.get("preprocess") or "center")
            if preprocess not in ("raw", "center", "standardized"):
                raise AppError(ANALYSIS_INPUT_INVALID, "preprocess must be raw, center, or standardized")
            # PCA is deterministic; keep old callers and the generic request
            # path on one cache identity regardless of an irrelevant seed.
            params.update({"mode": mode, "preprocess": preprocess, "seed": 42})
        if analysis_type == "effective_dimension" and ("preprocess" not in params or params["preprocess"] is None or params["preprocess"] == ""):
            # Keep the backend default identical to the UI default so omitted
            # and explicit standardized requests share one cache identity.
            params["preprocess"] = "standardized"
        input_ids = self._input_ids(analysis_type, params)
        run_rows = [self._usable_run(run_id) for run_id in input_ids]
        cross_dataset = analysis_type in ("coverage", "overlap", "acquisition", "drift")
        warm_start_fps = analysis_type == "fps" and len(input_ids) == 2
        if cross_dataset or warm_start_fps:
            signatures_and_meta = [self.results.feature_space_signature(run_id) for run_id in input_ids]
            signatures = [item[0] for item in signatures_and_meta]
            if not signatures[0] or signatures[0] != signatures[1]:
                side = "reference and query" if cross_dataset else "candidate and existing"
                raise AppError(
                    ANALYSIS_INPUT_INVALID,
                    f"{side} runs must use the same descriptor feature space",
                    {
                        "reference_run_id": input_ids[0],
                        "query_run_id": input_ids[1],
                        "reference_descriptor": run_rows[0].get("descriptor_name"),
                        "query_descriptor": run_rows[1].get("descriptor_name"),
                        "reference_feature_count": signatures_and_meta[0][1].get("feature_count"),
                        "query_feature_count": signatures_and_meta[1][1].get("feature_count"),
                    },
                )
            view_ids = [params.get("reference_view_id"), params.get("query_view_id")]
            for index, view_id in enumerate(view_ids):
                if not view_id:
                    continue
                view = self._usable_view(str(view_id), run_rows[index]["dataset_id"])
                side = "reference" if index == 0 else "query"
                params[f"{side}_selection_hash"] = view["selection_hash"]
        elif params.get("view_id"):
            # Single-run analyses scope to a dataset view by slicing the run's
            # samples to the view frames (same lens the cross-dataset modules
            # use). The selection hash joins the cache key so a view's content,
            # not just its id, decides reuse.  Warm-start FPS scopes the view
            # to the candidate run; the existing set is always used whole.
            if len(run_rows) != 1 and not warm_start_fps:
                raise AppError(
                    ANALYSIS_INPUT_INVALID,
                    "view_id applies to single-run analyses only",
                    {"analysis_type": analysis_type},
                )
            view_id = params["view_id"]
            if not isinstance(view_id, str) or not view_id.strip():
                raise AppError(INVALID_PARAMS, "view_id must be a non-empty string")
            view = self._usable_view(view_id, run_rows[0]["dataset_id"])
            params["selection_hash"] = view["selection_hash"]
        if analysis_type == "sensitivity":
            descriptor_names = {str(row["descriptor_name"]) for row in run_rows}
            if len(descriptor_names) > 1:
                raise AppError(
                    ANALYSIS_INPUT_INVALID,
                    "parameter sensitivity requires the same descriptor; use Compare for different descriptors",
                    {"descriptors": sorted(descriptor_names)},
                )
        if analysis_type == "fps":
            # Validate the composite block vocabulary before enqueuing: a typo
            # should fail the request, not create a job that fails a minute later.
            _block_names(params)
        canonical_params = self._canonical_params(params)
        cache_key = self._analysis_cache_key(analysis_type, input_ids, canonical_params)
        with self._submit_lock:
            cached = self.db.query_one(
                "SELECT * FROM analysis_runs WHERE cache_key = ? AND status = 'COMPLETED' ORDER BY created_at DESC LIMIT 1",
                (cache_key,),
            )
            if cached and self._artifact_is_complete(cached):
                return {"job_id": None, "analysis_id": cached["id"], "cache": {"existing_analysis_id": cached["id"], "cache_key": cache_key}}
            active = self.db.query_one(
                "SELECT * FROM analysis_runs WHERE cache_key = ? AND status IN ('QUEUED', 'RUNNING') ORDER BY created_at DESC LIMIT 1",
                (cache_key,),
            )
            analysis_id = active["id"] if active else f"ana_{uuid.uuid4().hex[:12]}"
            if active:
                job = self.db.query_one(
                    "SELECT id FROM jobs WHERE analysis_run_id = ? AND status IN ('QUEUED', 'RUNNING') ORDER BY created_at DESC LIMIT 1",
                    (analysis_id,),
                )
                if job:
                    return {"job_id": job["id"], "analysis_id": analysis_id, "cache": {"existing_analysis_id": analysis_id, "status": active["status"], "cache_key": cache_key}}
            primary = run_rows[0]
            now = _NOW()
            if not active:
                self.db.execute(
                    "INSERT INTO analysis_runs (id, descriptor_run_id, analysis_type, params_json, status, created_at, input_run_ids_json, dataset_ids_json, cache_key, schema_version, algorithm_version, preprocessing_json, warnings_json, preview_json, updated_at)"
                    " VALUES (?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        analysis_id,
                        primary["id"],
                        analysis_type,
                        json.dumps(canonical_params, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                        now,
                        json.dumps(input_ids, ensure_ascii=False),
                        json.dumps(sorted({r["dataset_id"] for r in run_rows}), ensure_ascii=False),
                        cache_key,
                        _ANALYSIS_SCHEMA_VERSION,
                        ANALYSIS_ALGORITHM_VERSION,
                        json.dumps({"preprocess": params.get("preprocess"), "scaling": params.get("scaling")}, ensure_ascii=False),
                        json.dumps([], ensure_ascii=False),
                        json.dumps({}, ensure_ascii=False),
                        now,
                    ),
                )

            def runner(ctx):
                self._mark_run_running(analysis_id)
                ctx.progress(0, 1, "loading descriptor results")
                view_ids = (
                    [params.get("reference_view_id"), params.get("query_view_id")]
                    if cross_dataset
                    else [params.get("view_id"), None]
                    if warm_start_fps
                    else [params.get("view_id")] * len(run_rows)
                )
                samples = [
                    self._load_samples(
                        row,
                        params,
                        analysis_type,
                        check=ctx.check_cancelled,
                        view_id=view_ids[index],
                    )
                    for index, row in enumerate(run_rows)
                ]
                ctx.check_cancelled()
                result = self._run_engine(analysis_type, params, run_rows, samples, ctx)
                ctx.check_cancelled()
                preview_samples = samples[1] if cross_dataset else samples[0]
                preview = self._build_preview(
                    result,
                    preview_samples,
                    analysis_type,
                    reference_samples=samples[0] if cross_dataset else None,
                )
                if analysis_type == "pca" and isinstance(preview.get("points"), list):
                    frame_count = int(preview_samples.frame.max()) + 1 if preview_samples.frame.size else 1
                    properties = self._frame_properties(run_rows[0], frame_count)
                    preview["points"] = [
                        {**point, **properties[int(point["frame"])]}
                        if isinstance(point, dict) and 0 <= int(point.get("frame", -1)) < len(properties)
                        else point
                        for point in preview["points"]
                    ]
                out_dir, manifest = self._commit_artifact(
                    analysis_id,
                    analysis_type,
                    input_ids,
                    canonical_params,
                    result,
                    preview,
                    ctx,
                )
                if analysis_type == "pca":
                    # Keep result.get_pca usable for databases created by the
                    # legacy frontend. New code reads the generic preview.
                    self._write_legacy_pca_compatibility(out_dir, analysis_id, input_ids[0], params, preview)
                self._complete_analysis_run(
                    analysis_id,
                    {
                        "result_path": str(out_dir),
                        "finished_at": _NOW(),
                        "warnings_json": json.dumps(result.get("warnings", []), ensure_ascii=False),
                        "artifact_manifest_json": json.dumps(manifest, ensure_ascii=False),
                        "preview_json": json.dumps(preview, ensure_ascii=False),
                        "preprocessing_json": json.dumps({"preprocess": params.get("preprocess")}, ensure_ascii=False),
                        "updated_at": _NOW(),
                    },
                    out_dir,
                )
                ctx.progress(1, 1, "analysis complete")
                n_points = preview_samples.n_samples if cross_dataset else samples[0].n_samples
                return {"analysis_id": analysis_id, "n_points": n_points, "analysis_type": analysis_type, "warnings": result.get("warnings", [])}

            try:
                job_id = self.jobs.submit(
                    f"analysis.{analysis_type}",
                    runner,
                    dataset_id=primary["dataset_id"],
                    analysis_run_id=analysis_id,
                )
            except Exception:
                if not active:
                    self.db.execute("DELETE FROM analysis_runs WHERE id = ?", (analysis_id,))
                raise
            return {"job_id": job_id, "analysis_id": analysis_id, "cache": None}

    def submit_export(self, params: dict) -> dict:
        params = dict(params or {})
        input_ids = self._input_ids("export", params)
        if len(input_ids) != 1:
            raise AppError(ANALYSIS_INPUT_INVALID, "export requires exactly one run_id")
        run = self._usable_run(input_ids[0])
        export_format = str(params.get("format") or params.get("output_format") or "json").lower()
        if export_format not in ("json", "csv", "extxyz", "deepmd", "indices", "report"):
            raise AppError(ANALYSIS_INPUT_INVALID, "format must be json, csv, extxyz, deepmd, indices, or report")
        mode = str(params.get("mode") or "structure")
        if mode not in ("structure", "atom"):
            raise AppError(ANALYSIS_INPUT_INVALID, "mode must be structure or atom")
        # The sampling report carries the provenance of the analysis it came
        # from; every other format only needs the run plus the selection.
        report_analysis = self._analysis_row(str(params["analysis_id"])) if export_format == "report" and params.get("analysis_id") else None
        if export_format == "report" and report_analysis is None:
            raise AppError(ANALYSIS_INPUT_INVALID, "report export requires the analysis_id of a completed analysis")
        selected = params.get("indices")
        if selected is None:
            selected = []
        if not isinstance(selected, list) or not all(
            isinstance(i, int) and not isinstance(i, bool) and i >= 0 for i in selected
        ):
            raise AppError(ANALYSIS_INPUT_INVALID, "indices must be a list of non-negative integers")
        target = str(params.get("output_path") or "")
        if not target:
            raise AppError(ANALYSIS_INPUT_INVALID, "output_path is required for export")
        try:
            target = str(validate_local_path(target, field="export output path"))
        except UnsafePathError as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "export output must be an absolute local path") from exc
        # The analysis id is part of a report's identity: two different
        # sampling analyses must never share one report cache entry.
        canonical = self._canonical_params({
            "format": export_format,
            "indices": sorted(set(selected)),
            "output_path": target,
            "mode": mode,
            **({"analysis_id": str(params["analysis_id"])} if export_format == "report" and params.get("analysis_id") else {}),
        })
        cache_key = self._analysis_cache_key("export", input_ids, canonical)

        with self._submit_lock:
            cached = self.db.query_one("SELECT * FROM analysis_runs WHERE cache_key = ? AND status = 'COMPLETED'", (cache_key,))
            if cached and self._artifact_is_complete(cached):
                return {"job_id": None, "analysis_id": cached["id"], "cache": {"existing_analysis_id": cached["id"], "cache_key": cache_key}}
            active = self.db.query_one(
                "SELECT * FROM analysis_runs WHERE cache_key = ? AND status IN ('QUEUED', 'RUNNING') ORDER BY created_at DESC LIMIT 1",
                (cache_key,),
            )
            if active:
                job = self.db.query_one(
                    "SELECT id FROM jobs WHERE analysis_run_id = ? AND status IN ('QUEUED', 'RUNNING') ORDER BY created_at DESC LIMIT 1",
                    (active["id"],),
                )
                if job:
                    return {"job_id": job["id"], "analysis_id": active["id"], "cache": {"existing_analysis_id": active["id"], "status": active["status"], "cache_key": cache_key}}
            analysis_id = active["id"] if active else f"ana_{uuid.uuid4().hex[:12]}"
            if not active:
                self.db.execute(
                    "INSERT INTO analysis_runs (id, descriptor_run_id, analysis_type, params_json, status, created_at, input_run_ids_json, dataset_ids_json, cache_key, schema_version, algorithm_version, updated_at) VALUES (?, ?, 'export', ?, 'QUEUED', ?, ?, ?, ?, ?, ?, ?)",
                    (analysis_id, run["id"], json.dumps(canonical, ensure_ascii=False, sort_keys=True), _NOW(), json.dumps(input_ids), json.dumps([run["dataset_id"]]), cache_key, _ANALYSIS_SCHEMA_VERSION, ANALYSIS_ALGORITHM_VERSION, _NOW()),
                )

            def runner(ctx):
                self._mark_run_running(analysis_id)
                ctx.progress(0, 1, "writing export")
                path = self._write_export(run, selected, export_format, mode, Path(target), ctx, report_analysis)
                out_dir, manifest = self._commit_artifact(
                    analysis_id,
                    "export",
                    input_ids,
                    canonical,
                    {"arrays": {}, "warnings": [], "export_path": str(path)},
                    {"kind": "export", "format": export_format, "output_path": str(path), "selected_count": len(selected)},
                    ctx,
                )
                self._complete_analysis_run(
                    analysis_id,
                    {
                        "result_path": str(out_dir),
                        "finished_at": _NOW(),
                        "artifact_manifest_json": json.dumps(manifest),
                        "preview_json": json.dumps({"kind": "export", "format": export_format, "output_path": str(path), "selected_count": len(selected)}),
                        "updated_at": _NOW(),
                    },
                    out_dir,
                )
                ctx.progress(1, 1, "export complete")
                return {"analysis_id": analysis_id, "output_path": str(path), "selected_count": len(selected)}

            try:
                job_id = self.jobs.submit("analysis.export", runner, dataset_id=run["dataset_id"], analysis_run_id=analysis_id)
            except Exception:
                if not active:
                    self.db.execute("DELETE FROM analysis_runs WHERE id = ?", (analysis_id,))
                raise
            return {"job_id": job_id, "analysis_id": analysis_id, "cache": None}

    def _mark_run_running(self, analysis_id: str) -> None:
        """Flip the analysis run to RUNNING when its job actually starts.
        Without this the row sits at QUEUED for the whole computation and the
        history shows 排队中 for a result that is being computed right now.
        The WHERE guard keeps a cancel-settled row from being resurrected."""
        self.db.execute(
            "UPDATE analysis_runs SET status = 'RUNNING', updated_at = ? WHERE id = ? AND status IN ('QUEUED', 'RUNNING')",
            (_NOW(), analysis_id),
        )

    def _complete_analysis_run(self, analysis_id: str, values: dict[str, object], artifact_path: Path) -> None:
        """Settle an analysis artifact with a cancellation-safe status CAS."""
        allowed = {
            "result_path",
            "finished_at",
            "warnings_json",
            "artifact_manifest_json",
            "preview_json",
            "preprocessing_json",
            "updated_at",
        }
        if not set(values) <= allowed:
            raise ValueError("unsupported analysis completion field")
        assignments = ["status = 'COMPLETED'"]
        params: list[object] = []
        for column, value in values.items():
            assignments.append(f"{column} = ?")
            params.append(value)
        params.append(analysis_id)
        changed = self.db.execute(
            f"UPDATE analysis_runs SET {', '.join(assignments)} WHERE id = ? AND status = 'RUNNING'",
            tuple(params),
        )
        if changed == 1:
            return
        self._rmtree_quiet(artifact_path)
        raise AppError(JOB_CANCELLED, f"analysis run {analysis_id} was cancelled")

    def _apply_thread_limit(self) -> None:
        """Apply the user's `compute.default_threads` setting to the numeric
        stack used by analysis compute (BLAS/OpenMP pools via threadpoolctl).

        Applied process-wide and deliberately left in place: the setting is a
        user preference for analysis parallelism, not a per-job override, and
        concurrent analysis jobs all read the same value. Descriptor engine
        threads are managed by the engine itself and unaffected. Missing,
        invalid, or non-positive values mean "engine default" (no change).
        """
        raw = self.db.get_setting("compute.default_threads")
        if not raw:
            return
        try:
            limit = int(str(raw).strip())
        except (TypeError, ValueError):
            return
        if limit <= 0:
            return
        from threadpoolctl import threadpool_limits

        threadpool_limits(limits=limit)

    def _run_engine(self, analysis_type: str, params: dict, rows: list[dict], samples: list[SampleMatrix], ctx) -> dict:
        progress = lambda fraction, message: (ctx.check_cancelled(), ctx.progress(None, None, message, fraction=0.1 + 0.85 * float(fraction)))
        self._apply_thread_limit()
        if analysis_type in _ENGINE_TYPES or analysis_type in _ENGINE_PAIR_TYPES:
            args = (samples[0], params) if analysis_type in _ENGINE_TYPES else (samples[0], samples[1], params)
            return getattr(AnalysisEngine, analysis_type)(*args, progress)
        if analysis_type in ("kmeans", "dbscan", "hdbscan", "agglomerative", "hierarchical"):
            return AnalysisEngine.cluster(samples[0], params, analysis_type, progress)
        if analysis_type in ("knn", "lof", "isolation_forest", "isolation-forest", "iforest", "mahalanobis", "mahalanobis_distance"):
            return AnalysisEngine.outlier(samples[0], params, analysis_type, progress)
        if analysis_type in ("fps", "random", "stratified", "cluster_representative", "cluster", "per_element", "element"):
            existing = samples[1] if analysis_type == "fps" and len(samples) > 1 else None
            group_labels = None
            blocks = None
            existing_blocks = None
            if analysis_type == "fps":
                block_names = _block_names(params)
                if str(params.get("strategy") or "global") == "grouped":
                    group_labels = self._element_group_labels(rows[0], samples[0], (params.get("selection_hash"), samples[0].n_samples))
                if block_names:
                    # Composition fractions must share one element layout across
                    # candidate and existing sets, so use their union.
                    element_list = sorted({
                        *self._dataset_elements(rows[0]),
                        *(self._dataset_elements(rows[1]) if existing is not None else []),
                    })
                    blocks = self._sampling_blocks(rows[0], samples[0], block_names, params, element_list)
                    if existing is not None:
                        existing_blocks = self._sampling_blocks(rows[1], samples[1], block_names, params, element_list)
            return AnalysisEngine.sampling(samples[0], params, analysis_type, progress, existing=existing, group_labels=group_labels, blocks=blocks, existing_blocks=existing_blocks)
        if analysis_type == "sensitivity":
            return AnalysisEngine.sensitivity(list(zip(rows, samples)), params, progress)
        if analysis_type == "perturbation_sensitivity":
            return self._run_perturbation_sensitivity(params, rows[0], samples[0], ctx)
        raise AppError(ANALYSIS_INPUT_INVALID, f"unsupported analysis type: {analysis_type}")

    def _run_perturbation_sensitivity(self, params: dict, run_row: dict, samples: SampleMatrix, ctx) -> dict:
        """Recompute one descriptor on deterministic structure perturbations."""
        if self.datasets is None:
            raise AppError(ANALYSIS_INPUT_INVALID, "structural perturbation requires the dataset service")
        if samples.mode != "structure":
            raise AppError(ANALYSIS_INPUT_INVALID, "structural perturbation sensitivity is structure-level only")
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset is None:
            raise AppError(ANALYSIS_INPUT_INVALID, f"dataset {run_row['dataset_id']} does not exist")
        source_adapter = self.datasets._adapter_for(dataset)
        frame_count = samples.n_samples
        if frame_count < 1:
            raise AppError(ANALYSIS_INSUFFICIENT_SAMPLES, "at least one structure is required")
        max_structures = params.get("max_structures", 64)
        try:
            max_structures = int(max_structures)
        except (TypeError, ValueError) as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "max_structures must be an integer") from exc
        if max_structures < 1:
            raise AppError(ANALYSIS_INPUT_INVALID, "max_structures must be >= 1")
        # Every selected structure is recomputed once per amplitude, so the cap
        # is a compute budget: keep it explicit and bounded instead of letting
        # one request schedule an unbounded descriptor sweep.
        if max_structures > 2048:
            raise AppError(ANALYSIS_INPUT_INVALID, "max_structures must be <= 2048")
        selected_indices = np.linspace(0, frame_count - 1, min(frame_count, max_structures), dtype=np.int64)
        selected_frames = np.asarray(samples.frame, dtype=np.int64)[selected_indices]
        baseline = SampleMatrix(
            values=np.asarray(samples.values, dtype=np.float64)[selected_indices],
            frame=selected_frames,
            sample_ids=[samples.sample_ids[int(index)] for index in selected_indices.tolist()],
            mode="structure",
        )

        perturbation = str(params.get("perturbation") or "jitter").lower()
        if perturbation not in ("jitter", "strain"):
            raise AppError(ANALYSIS_INPUT_INVALID, "perturbation must be jitter or strain")
        raw_amplitudes = params.get("amplitudes")
        if raw_amplitudes is None:
            try:
                count = int(params.get("n_amplitudes", 8))
                maximum = float(params.get("max_amplitude", 0.2 if perturbation == "jitter" else 0.1))
            except (TypeError, ValueError) as exc:
                raise AppError(ANALYSIS_INPUT_INVALID, "n_amplitudes and max_amplitude must be numeric") from exc
            if count < 2:
                raise AppError(ANALYSIS_INPUT_INVALID, "n_amplitudes must be >= 2")
            raw_amplitudes = np.linspace(0.0, maximum, count).tolist()
        if not isinstance(raw_amplitudes, (list, tuple)):
            raise AppError(ANALYSIS_INPUT_INVALID, "amplitudes must be a list of numbers")
        if len(raw_amplitudes) < 2 or len(raw_amplitudes) > 32:
            raise AppError(ANALYSIS_INPUT_INVALID, "amplitudes must contain between 2 and 32 values")
        try:
            amplitudes = [float(value) for value in raw_amplitudes]
        except (TypeError, ValueError) as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "amplitudes must be a list of numbers") from exc
        if not all(np.isfinite(value) and value >= 0 for value in amplitudes):
            raise AppError(ANALYSIS_INPUT_INVALID, "amplitudes must be finite and non-negative")
        if perturbation == "strain" and any(value >= 1.0 for value in amplitudes):
            raise AppError(ANALYSIS_INPUT_INVALID, "strain amplitudes must be smaller than 1")
        if not any(np.isclose(value, 0.0) for value in amplitudes):
            amplitudes = [0.0, *amplitudes]

        try:
            seed = int(params.get("seed", 42))
        except (TypeError, ValueError) as exc:
            raise AppError(ANALYSIS_INPUT_INVALID, "seed must be an integer") from exc
        rng = np.random.default_rng(seed)
        source_frames = [source_adapter.get_frame(int(frame)) for frame in selected_frames.tolist()]
        jitter_vectors = []
        for frame in source_frames:
            vector = rng.normal(size=np.asarray(frame.positions).shape)
            scale = float(np.sqrt(np.mean(vector * vector)))
            jitter_vectors.append(vector / max(scale, 1e-15))

        try:
            descriptor_parameters = json.loads(run_row.get("parameters_json") or "{}")
        except (TypeError, ValueError):
            descriptor_parameters = {}
        # Sensitivity recomputes the producer descriptor; keep the device the
        # run originally declared so baselines stay reproducible.
        run_device = str(run_row.get("device") or "cpu")
        descriptor = self.datasets.adapter.build(run_row["descriptor_name"], descriptor_parameters, device=run_device)
        control = self.datasets.adapter.make_control()
        ctx.attach_control(control)
        perturbation_results: list[tuple[float, SampleMatrix]] = []
        for amplitude_index, amplitude in enumerate(amplitudes):
            ctx.check_cancelled()
            perturbed_frames = [
                self._perturb_frame(frame, amplitude, perturbation, jitter_vectors[index])
                for index, frame in enumerate(source_frames)
            ]
            batch = self.datasets.adapter.to_structure_batch(perturbed_frames)
            computed = self.datasets.adapter.compute(descriptor, batch, control)
            values = self._computed_structure_values(computed, len(perturbed_frames))
            perturbation_results.append((
                amplitude,
                SampleMatrix(
                    values=values,
                    frame=selected_frames.copy(),
                    sample_ids=list(baseline.sample_ids),
                    mode="structure",
                ),
            ))
            ctx.progress(
                amplitude_index + 1,
                len(amplitudes),
                "computing structural perturbations",
                fraction=0.1 + 0.65 * (amplitude_index + 1) / len(amplitudes),
            )

        def report(fraction: float, message: str) -> None:
            ctx.check_cancelled()
            ctx.progress(None, None, message, fraction=0.75 + 0.25 * float(fraction))

        result = AnalysisEngine.perturbation_sensitivity(baseline, perturbation_results, params, report)
        # The response curve is a summary over the selected structures, so the
        # artifact must say how many structures the run could have offered.
        # Otherwise "Structures: 64" reads as a property of the dataset.
        sampled = int(selected_indices.size)
        result["preview"]["available_structure_count"] = int(frame_count)
        if sampled < frame_count:
            result["warnings"] = [
                *result.get("warnings", []),
                f"sampled {sampled} of {frame_count} structures evenly across the run",
            ]
        return result

    @staticmethod
    def _perturb_frame(frame, amplitude: float, perturbation: str, jitter_vector: np.ndarray):
        positions = np.asarray(frame.positions, dtype=np.float64)
        if perturbation == "jitter":
            return replace(frame, positions=positions + amplitude * jitter_vector)
        center = positions.mean(axis=0, keepdims=True) if positions.size else np.zeros((1, 3), dtype=np.float64)
        scale = 1.0 + amplitude
        cell = np.asarray(frame.cell, dtype=np.float64)
        if cell.shape == (3, 3) and abs(float(np.linalg.det(cell))) > 1e-10:
            cell = cell * scale
        return replace(frame, positions=center + (positions - center) * scale, cell=cell)

    def _computed_structure_values(self, computed, frame_count: int) -> np.ndarray:
        values = np.asarray(computed.values, dtype=np.float64)
        if values.ndim > 2:
            values = values.reshape(values.shape[0], -1)
        if values.ndim == 1:
            values = values.reshape(-1, 1)
        if values.ndim != 2 or not np.isfinite(values).all():
            raise AppError(ANALYSIS_INPUT_INVALID, "perturbed descriptor result is not a finite 2D matrix")
        offsets = np.asarray(getattr(computed, "row_offsets", None), dtype=np.int64) if getattr(computed, "row_offsets", None) is not None else None
        if self._valid_offsets(offsets, values.shape[0]) and offsets.size == frame_count + 1:
            pooled = np.empty((frame_count, values.shape[1]), dtype=np.float64)
            for index in range(frame_count):
                lo, hi = int(offsets[index]), int(offsets[index + 1])
                pooled[index] = values[lo:hi].mean(axis=0) if hi > lo else 0.0
            return pooled
        if values.shape[0] == frame_count:
            return values
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            "perturbed descriptor result cannot be aligned to structures",
            {"rows": int(values.shape[0]), "structures": frame_count},
        )

    def _input_ids(self, analysis_type: str, params: dict) -> list[str]:
        if analysis_type in ("coverage", "overlap", "acquisition", "drift"):
            ids = [params.get("reference_run_id"), params.get("query_run_id")]
        elif analysis_type == "fps" and params.get("existing_run_id"):
            # Warm-start FPS: candidates plus the existing training set.
            ids = [params.get("run_id"), params.get("existing_run_id")]
        elif analysis_type in ("compare", "mantel"):
            ids = [params.get("left_run_id") or params.get("reference_run_id"), params.get("right_run_id") or params.get("query_run_id")]
        elif analysis_type == "sensitivity":
            ids = params.get("run_ids") or []
        else:
            ids = params.get("run_ids") or [params.get("run_id")]
        if isinstance(ids, (str, bytes)):
            ids = [ids]
        ids = [str(v) for v in ids if v]
        if not ids:
            raise AppError(INVALID_PARAMS, "at least one descriptor run id is required")
        if analysis_type in ("coverage", "overlap", "acquisition", "drift", "compare", "mantel") and len(ids) != 2:
            raise AppError(ANALYSIS_INPUT_INVALID, f"{analysis_type} requires reference and query run IDs")
        return ids

    def _element_group_labels(self, run_row: dict, samples: SampleMatrix, scope: tuple) -> np.ndarray:
        """One element-set label ("C", "C-O-Si", …) per sample, for grouped FPS.

        Atom-mode rows already carry their central element; structure-mode rows
        resolve their composition through the dataset adapter.  A failure here
        is fatal for grouped FPS — silently degrading the groups would change
        the scientific result without telling anyone.  ``scope`` identifies the
        sample set (view selection hash + size) for the label cache; completed
        runs are immutable, so cached labels cannot go stale.
        """
        cache_key = (str(run_row["id"]), samples.mode) + tuple(str(part) for part in scope)
        cached = self._group_labels_cache.get(cache_key)
        if cached is not None and len(cached) == samples.n_samples:
            return cached
        from ..datasets.deepmd_symbols import _Z_TO_SYMBOL

        if samples.mode == "atom" and samples.elements is not None and len(samples.elements) == samples.n_samples:
            labels = np.asarray(
                [_Z_TO_SYMBOL.get(int(z), f"Z{int(z)}") for z in np.asarray(samples.elements).tolist()],
                dtype=object,
            )
        else:
            if self.datasets is None:
                raise AppError(ANALYSIS_INPUT_INVALID, "grouped FPS requires dataset access to read element metadata")
            dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
            if dataset is None:
                raise AppError(ANALYSIS_INPUT_INVALID, f"dataset {run_row['dataset_id']} does not exist")
            adapter = self.datasets._adapter_for(dataset)
            count = len(adapter)
            frame_cache: dict[int, str] = {}
            labels_list: list[str] = []
            for frame_index in np.asarray(samples.frame, dtype=np.int64).tolist():
                label = frame_cache.get(frame_index)
                if label is None:
                    if not 0 <= frame_index < count:
                        raise AppError(ANALYSIS_INPUT_INVALID, f"sample frame {frame_index} is outside dataset {dataset['id']}")
                    frame = adapter.get_frame(frame_index)
                    label = "-".join(sorted({_Z_TO_SYMBOL.get(int(z), f"Z{int(z)}") for z in np.asarray(frame.numbers).tolist()}))
                    frame_cache[frame_index] = label
                labels_list.append(label)
            labels = np.asarray(labels_list, dtype=object)
        if len(labels) != samples.n_samples:
            raise AppError(ANALYSIS_INPUT_INVALID, "element metadata does not align with the descriptor samples")
        self._group_labels_cache[cache_key] = labels
        while len(self._group_labels_cache) > 8:
            self._group_labels_cache.pop(next(iter(self._group_labels_cache)))
        return labels

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

    def _usable_run(self, run_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (run_id,))
        if row is None:
            raise AppError(INVALID_PARAMS, f"run {run_id} does not exist")
        if row["status"] == "STALE":
            raise AppError(ANALYSIS_STALE, f"run {run_id} is STALE and cannot feed new analysis", {"run_id": run_id})
        if row["status"] != "COMPLETED" or not row.get("result_path"):
            raise AppError(RESULT_INCOMPATIBLE, f"run {run_id} is {row['status']}")
        self._assert_dataset_current(row)
        return row

    def _assert_dataset_current(self, row: dict) -> None:
        if self.datasets is None:
            return
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (row["dataset_id"],))
        if dataset is None:
            return
        refresh = getattr(self.datasets, "refresh_if_changed", None)
        if callable(refresh):
            refresh(dataset)
            return
        from ..datasets import compute_fingerprint

        try:
            source = validate_local_path(dataset["source_path"], field="dataset source path")
            current = compute_fingerprint(source, dataset["number_of_frames"], use_cache=False)
        except (OSError, TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(RESULT_INCOMPATIBLE, "dataset source is unavailable") from exc
        if current != dataset["fingerprint"]:
            self.datasets._mark_runs_stale(row["dataset_id"], f"source fingerprint changed ({dataset['fingerprint']} -> {current})")
            raise AppError(ANALYSIS_STALE, f"run {row['id']} is stale because the source dataset changed", {"run_id": row["id"], "dataset_id": row["dataset_id"]})

    def _usable_view(self, view_id: str, dataset_id: str) -> dict:
        view = self.db.query_one("SELECT * FROM dataset_views WHERE id = ?", (view_id,))
        if view is None:
            raise AppError(INVALID_PARAMS, f"dataset view {view_id} does not exist")
        if view["dataset_id"] != dataset_id:
            raise AppError(ANALYSIS_INPUT_INVALID, "dataset view does not belong to the descriptor run dataset")
        dataset = self.db.query_one("SELECT fingerprint FROM datasets WHERE id = ?", (dataset_id,))
        if dataset is None or view["dataset_fingerprint"] != dataset["fingerprint"]:
            raise AppError(ANALYSIS_STALE, f"dataset view {view_id} is stale")
        return view

    @staticmethod
    def _slice_samples_to_frames(samples: SampleMatrix, frame_indices: list[int]) -> SampleMatrix:
        selected = np.flatnonzero(np.isin(samples.frame, np.asarray(frame_indices, dtype=np.int64)))
        if selected.size == 0:
            raise AppError(ANALYSIS_INPUT_INVALID, "dataset view contains no descriptor samples")
        return SampleMatrix(
            values=samples.values[selected],
            frame=samples.frame[selected],
            row=samples.row[selected] if samples.row is not None else None,
            sample_ids=[samples.sample_ids[int(index)] for index in selected.tolist()],
            elements=samples.elements[selected] if samples.elements is not None else None,
            mode=samples.mode,
            warnings=list(samples.warnings),
            properties={key: value[selected] for key, value in samples.properties.items()},
            positions=samples.positions[selected] if samples.positions is not None else None,
            cells=samples.cells[selected] if samples.cells is not None else None,
            pbc=samples.pbc[selected] if samples.pbc is not None else None,
        )

    def _load_samples(self, run_row: dict, params: dict, analysis_type: str, check=None, view_id=None) -> SampleMatrix:
        # Cooperative-cancellation checkpoints between the load/pool stages:
        # without them a cancel during a multi-GB load waits for the whole
        # phase to finish before it takes effect.
        check = check or (lambda: None)
        values, row = self.results.load_values(run_row["id"])
        check()
        values = np.asarray(values, dtype=np.float64)
        if values.ndim > 2:
            values = values.reshape(values.shape[0], -1)
        if values.ndim == 1:
            values = values.reshape(-1, 1)
        if values.ndim != 2 or values.shape[0] == 0 or values.shape[1] == 0:
            raise AppError(ANALYSIS_INPUT_INVALID, "descriptor result is empty or not a 2D feature matrix")
        # Feature variance is a diagnostic: it keeps finite values per column
        # and reports invalid counts. Every other analysis remains strict so a
        # bad descriptor cannot be silently hidden by preprocessing.
        if analysis_type != "feature_variance" and not np.isfinite(values).all():
            raise AppError(ANALYSIS_INPUT_INVALID, "descriptor result contains NaN or Inf")
        check()
        offsets = self._row_offsets(row)
        meta = self._result_metadata(row)
        requested_mode = str(params.get("mode") or "structure")
        if requested_mode not in ("structure", "atom"):
            raise AppError(ANALYSIS_INPUT_INVALID, "mode must be structure or atom")
        valid_offsets = self._valid_offsets(offsets, values.shape[0])
        positions = None
        cells = None
        pbc = None
        level = str(meta.get("level") or "").lower()
        declared_atom = bool(meta.get("row_semantics") in ("atom", "local_environment", "pair") or any(token in level for token in ("atom", "local", "pair")))
        if requested_mode == "atom":
            if not valid_offsets:
                raise AppError(ANALYSIS_INPUT_INVALID, "atom/local-environment analysis requires verified row_offsets")
            if not declared_atom:
                raise AppError(ANALYSIS_INPUT_INVALID, "descriptor metadata does not declare atom/local-environment rows")
            check()
            local_frames = np.repeat(np.arange(len(offsets) - 1, dtype=np.int64), np.diff(offsets).astype(np.int64))
            frame_values = self._run_frame_values(row, len(offsets) - 1)
            frames = frame_values[local_frames]
            rows = np.arange(values.shape[0], dtype=np.int64) - offsets[local_frames]
            sample_ids = [f"frame:{int(f)}:row:{int(r)}" for f, r in zip(frames, rows)]
            used = values
            elements, positions, cells, pbc = self._atom_metadata(row, offsets, local_frames)
            mode = "atom"
        elif valid_offsets and declared_atom:
            used = _pool_rows(values, offsets)
            check()
            n_frames = used.shape[0]
            frame_value = int(row.get("frame_index") or 0) if row.get("scope") == "frame" else 0
            frames = np.arange(n_frames, dtype=np.int64) + frame_value if row.get("scope") == "frame" else np.arange(n_frames, dtype=np.int64)
            rows = None
            sample_ids = [f"frame:{int(f)}" for f in frames]
            elements = None
            mode = "structure"
        else:
            used = values
            if row.get("scope") == "frame":
                frames = np.arange(values.shape[0], dtype=np.int64) + int(row.get("frame_index") or 0)
            else:
                frames = np.arange(values.shape[0], dtype=np.int64)
            rows = None
            sample_ids = [f"frame:{int(f)}" for f in frames]
            elements = None
            mode = "structure"
        properties = self._sample_properties(row, frames, rows, mode) if analysis_type == "property_correlation" else {}
        samples = SampleMatrix(
            values=used,
            frame=frames,
            row=rows,
            sample_ids=sample_ids,
            elements=elements,
            mode=mode,
            properties=properties,
            positions=positions,
            cells=cells,
            pbc=pbc,
        )
        if view_id:
            view = self._usable_view(str(view_id), run_row["dataset_id"])
            samples = self._slice_samples_to_frames(samples, json.loads(view["frame_indices_json"]))
        return samples

    @staticmethod
    def _valid_offsets(offsets, n_rows: int) -> bool:
        try:
            return bool(offsets is not None and offsets.ndim == 1 and offsets.size >= 2 and int(offsets[0]) == 0 and int(offsets[-1]) == n_rows and np.all(np.diff(offsets) >= 0))
        except (TypeError, ValueError):
            return False

    def _atom_metadata(self, run_row: dict, offsets, local_frames: np.ndarray) -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None, np.ndarray | None]:
        """Element labels and per-atom geometry for atom/local-environment rows.

        Both are optional metadata and become None when the dataset adapter
        cannot verify them against the descriptor rows.
        """
        if self.datasets is None:
            return None, None, None, None
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset is None:
            return None, None, None, None
        try:
            adapter = self.datasets._adapter_for(dataset)
            frames = [adapter.get_frame(int(index)) for index in self._run_frame_values(run_row, len(offsets) - 1).tolist()]
        except Exception:  # optional metadata, not a reason to corrupt a run
            return None, None, None, None

        labels = [int(z) for frame in frames for z in frame.numbers.tolist()]
        elements = np.asarray(labels, dtype=np.int64) if len(labels) == len(local_frames) else None

        try:
            position_rows: list[np.ndarray] = []
            cell_rows: list[np.ndarray] = []
            pbc_rows: list[np.ndarray] = []
            for offset_index, frame in enumerate(frames):
                expected = int(offsets[offset_index + 1] - offsets[offset_index])
                frame_positions = np.asarray(frame.positions, dtype=np.float64)
                if frame_positions.shape != (expected, 3):
                    return elements, None, None, None
                position_rows.append(frame_positions)
                cell = np.asarray(frame.cell, dtype=np.float64)
                if cell.shape != (3, 3):
                    cell = np.zeros((3, 3), dtype=np.float64)
                cell_rows.append(np.repeat(cell[None, :, :], expected, axis=0))
                pbc_rows.append(np.repeat(np.asarray(frame.pbc, dtype=bool)[None, :], expected, axis=0))
            positions = np.concatenate(position_rows, axis=0) if position_rows else np.zeros((0, 3), dtype=np.float64)
            cells = np.concatenate(cell_rows, axis=0) if cell_rows else np.zeros((0, 3, 3), dtype=np.float64)
            pbc = np.concatenate(pbc_rows, axis=0) if pbc_rows else np.zeros((0, 3), dtype=bool)
        except Exception:  # geometry is optional metadata; preserve descriptor analysis if unavailable
            return elements, None, None, None
        return elements, positions, cells, pbc

    def _sample_properties(self, run_row: dict, frames: np.ndarray, rows: np.ndarray | None, mode: str) -> dict[str, np.ndarray]:
        """Load only the physical targets requested by property analysis."""
        if self.datasets is None:
            return {}
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset is None:
            return {}
        try:
            adapter = self.datasets._adapter_for(dataset)
            frame_cache = {int(index): adapter.get_frame(int(index)) for index in np.unique(frames).tolist()}
        except Exception:
            return {}

        names = ("energy", "energy_per_atom", "force_max", "force_magnitude", "volume")
        values = {name: np.full(frames.size, np.nan, dtype=np.float64) for name in names}
        # Compute frame-wide quantities once, then map them to atom rows.
        frame_properties = {}
        for frame_index, frame in frame_cache.items():
            magnitudes = np.linalg.norm(np.asarray(frame.forces, dtype=np.float64), axis=1) if frame.forces is not None and len(frame.forces) else None
            cell = np.asarray(frame.cell, dtype=np.float64)
            volume = abs(float(np.linalg.det(cell))) if cell.shape == (3, 3) else float("nan")
            frame_properties[frame_index] = (magnitudes, float(magnitudes.max()) if magnitudes is not None else None, volume)
        for sample_index, frame_index in enumerate(frames.tolist()):
            frame = frame_cache.get(int(frame_index))
            if frame is None:
                continue
            natoms = max(int(len(frame.numbers)), 1)
            if frame.energy is not None:
                values["energy"][sample_index] = float(frame.energy)
                values["energy_per_atom"][sample_index] = float(frame.energy) / natoms
            magnitudes, force_max, volume = frame_properties[int(frame_index)]
            if magnitudes is not None:
                values["force_max"][sample_index] = force_max
                if mode == "atom" and rows is not None:
                    atom = int(rows[sample_index])
                    if 0 <= atom < magnitudes.size:
                        values["force_magnitude"][sample_index] = float(magnitudes[atom])
            if np.isfinite(volume) and volume > 0:
                values["volume"][sample_index] = volume
        return {name: array for name, array in values.items() if bool(np.isfinite(array).any())}

    # Composite sampling blocks.  ``descriptor`` and ``descriptor_summary``
    # derive from the descriptor matrix; the rest are physical signals read from
    # the dataset.  Names are the API vocabulary shared with the GUI.
    COMPOSITE_BLOCKS = ("descriptor", "descriptor_summary", "lattice", "composition", "energy", "force")
    _PHYSICAL_BLOCKS = ("lattice", "composition", "energy", "force")

    def _dataset_elements(self, run_row: dict) -> list[str]:
        """Chemical element symbols declared by the run's dataset (sorted)."""
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset is None:
            return []
        raw = dataset.get("elements")
        try:
            values = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return []
        if not isinstance(values, list):
            return []
        return sorted({str(value) for value in values if str(value)})

    def _sampling_blocks(self, run_row: dict, samples: SampleMatrix, block_names: list[str], params: dict, element_list: list[str] | None) -> list[FeatureBlock]:
        """Build the requested composite blocks for one sample set.

        Every block's raw (unscaled) values are returned; scaling and the 1/√D
        weight are applied by the sampling layer, on the reference set when
        warm-starting.
        """
        weights = params.get("block_weights") if isinstance(params.get("block_weights"), dict) else {}
        scaling = str(params.get("scaling") or "robust")
        physical = [name for name in block_names if name in self._PHYSICAL_BLOCKS]
        if physical and samples.mode != "structure":
            # Atom rows have no composition or per-structure energy; inventing
            # them would silently change what the sampling space means.
            raise AppError(ANALYSIS_INPUT_INVALID, "composite sampling blocks require structure granularity")
        signals = self._physical_signals(run_row, samples, physical) if physical else {}
        blocks: list[FeatureBlock] = []
        for name in block_names:
            if name == "descriptor":
                values = np.asarray(samples.values, dtype=np.float64)
            elif name == "descriptor_summary":
                values = _descriptor_summary(np.asarray(samples.values, dtype=np.float64))
            elif name == "lattice":
                values = signals["lattice"]
            elif name == "composition":
                if not element_list:
                    raise AppError(ANALYSIS_INPUT_INVALID, "composition sampling requires a known element list")
                values = _composition_matrix(signals["composition"], element_list)
            else:
                values = signals[name]
            try:
                weight = float(weights.get(name, 1.0))
            except (TypeError, ValueError) as exc:
                raise AppError(ANALYSIS_INPUT_INVALID, f"block weight for {name!r} must be a number") from exc
            blocks.append(FeatureBlock(name=name, values=values, weight=weight, scaling=scaling))
        return blocks

    def _physical_signals(self, run_row: dict, samples: SampleMatrix, names: list[str]) -> dict[str, np.ndarray]:
        """Read lattice/composition/energy/force signals aligned to the samples.

        Missing metadata is an error rather than a silently dropped block: a
        composite space the user did not ask for is worse than a clear failure.
        """
        if self.datasets is None:
            raise AppError(ANALYSIS_INPUT_INVALID, "composite sampling requires dataset access")
        from ..datasets.deepmd_symbols import _Z_TO_SYMBOL

        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset is None:
            raise AppError(ANALYSIS_INPUT_INVALID, f"dataset {run_row['dataset_id']} does not exist")
        adapter = self.datasets._adapter_for(dataset)
        count = len(adapter)
        n = samples.n_samples
        lattice = np.full((n, 6), np.nan, dtype=np.float64)
        energy = np.full(n, np.nan, dtype=np.float64)
        force = np.full((n, 3), np.nan, dtype=np.float64)
        elements: list[list[int]] = []
        frame_cache: dict[int, object] = {}
        wanted = set(names)
        for sample_index, frame_index in enumerate(np.asarray(samples.frame, dtype=np.int64).tolist()):
            frame = frame_cache.get(frame_index)
            if frame is None:
                if not 0 <= frame_index < count:
                    raise AppError(ANALYSIS_INPUT_INVALID, f"sample frame {frame_index} is outside dataset {dataset['id']}")
                frame = adapter.get_frame(frame_index)
                frame_cache[frame_index] = frame
            numbers = np.asarray(frame.numbers, dtype=np.int64)
            if "composition" in wanted:
                elements.append(numbers.tolist())
            if "lattice" in wanted:
                cell = np.asarray(frame.cell, dtype=np.float64)
                if cell.shape == (3, 3) and np.abs(cell).sum() > 1e-12:
                    lattice[sample_index] = _cell_parameters(cell)
            if "energy" in wanted and frame.energy is not None:
                energy[sample_index] = float(frame.energy) / max(int(numbers.size), 1)
            if "force" in wanted and frame.forces is not None and len(frame.forces):
                magnitudes = np.linalg.norm(np.asarray(frame.forces, dtype=np.float64), axis=1)
                force[sample_index] = (magnitudes.mean(), magnitudes.max(), magnitudes.std())
        signals: dict[str, np.ndarray] = {}
        for name in names:
            if name == "lattice":
                signals["lattice"] = _require_finite(lattice, "lattice", "lattice parameters (periodic cells)")
            elif name == "energy":
                signals["energy"] = _require_finite(energy.reshape(-1, 1), "energy", "per-atom energies")
            elif name == "force":
                signals["force"] = _require_finite(force, "force", "force statistics")
            elif name == "composition":
                # Atom numbers are not a numeric feature space; they become
                # element fractions once the shared element list is known.
                signals["composition"] = elements
        if "composition" in signals and len(elements) != n:
            raise AppError(ANALYSIS_INPUT_INVALID, "composition metadata does not align with the descriptor samples")
        return signals

    def _result_metadata(self, row: dict) -> dict:
        try:
            root = self._result_root(row)
            metadata = root / "metadata.json"
            ensure_no_reparse_points(metadata)
            return json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, UnsafePathError):
            return {}

    def _build_preview(self, result: dict, samples: SampleMatrix, analysis_type: str, reference_samples: SampleMatrix | None = None) -> dict:
        arrays = result.get("arrays", {})
        preview = dict(result.get("preview") or {})

        def sample_identity(index: int) -> dict | None:
            if index < 0 or index >= samples.n_samples:
                return None
            item = {"i": index, "frame": int(samples.frame[index]), "sample_id": samples.sample_ids[index]}
            if samples.row is not None:
                item["row"] = int(samples.row[index])
            if samples.elements is not None and index < len(samples.elements):
                item["element"] = int(samples.elements[index])
            return item

        def reference_identity(index: int) -> dict | None:
            if reference_samples is None or index < 0 or index >= reference_samples.n_samples:
                return None
            item = {
                "reference_i": index,
                "reference_frame": int(reference_samples.frame[index]),
                "reference_sample_id": reference_samples.sample_ids[index],
            }
            if reference_samples.row is not None:
                item["reference_row"] = int(reference_samples.row[index])
            return item

        if "coords" in arrays:
            coords = np.asarray(arrays["coords"])
            count = min(coords.shape[0], samples.n_samples, _MAX_PREVIEW_POINTS)
            indices = np.linspace(0, coords.shape[0] - 1, count, dtype=np.int64) if coords.shape[0] > count else np.arange(coords.shape[0])
            sample_indices = np.asarray(arrays.get("sample_indices", []), dtype=np.int64)
            points = []
            for i in indices.tolist():
                logical_index = int(sample_indices[i]) if sample_indices.ndim == 1 and i < sample_indices.size else int(i)
                point = sample_identity(logical_index)
                if point is None:
                    continue
                point.update({"x": float(coords[i, 0]), "y": float(coords[i, 1])})
                for key in _PREVIEW_ARRAY_KEYS:
                    if key in arrays and np.asarray(arrays[key]).ndim == 1 and i < len(arrays[key]):
                        output_key = "label" if key == "labels" else "cluster" if key == "cluster_labels" else "element" if key == "elements" else key
                        point[output_key] = int(arrays[key][i]) if key in ("labels", "cluster_labels", "elements", "coordination") else float(arrays[key][i])
                points.append(point)
            preview["points"] = points
            preview["total_points"] = int(coords.shape[0])
            row_keys = [key for key in _PREVIEW_ARRAY_KEYS if key in arrays and np.asarray(arrays[key]).ndim == 1]
            if row_keys:
                rows = []
                for i in range(min(coords.shape[0], _MAX_PREVIEW_POINTS)):
                    logical_index = int(sample_indices[i]) if sample_indices.ndim == 1 and i < sample_indices.size else int(i)
                    item = sample_identity(logical_index)
                    if item is None:
                        continue
                    for key in row_keys:
                        if i >= len(arrays[key]):
                            continue
                        value = np.asarray(arrays[key])[i]
                        output_key = "labels" if key == "labels" else "cluster_labels" if key == "cluster_labels" else "element" if key == "elements" else key
                        item[output_key] = int(value) if key in ("labels", "cluster_labels", "elements", "coordination") else float(value)
                    rows.append(item)
                preview["rows"] = rows
                preview["total_rows"] = int(coords.shape[0])
            if "selected_indices" in arrays:
                selected = np.asarray(arrays["selected_indices"], dtype=np.int64)
                preview["selected"] = [item for i in selected[:_MAX_PREVIEW_POINTS].tolist() if (item := sample_identity(int(i))) is not None]
        elif analysis_type == "pairwise" and "sample_indices" in arrays:
            matrix_indices = np.asarray(arrays["sample_indices"], dtype=np.int64)
            preview["matrix_samples"] = [item for i in matrix_indices[:_MAX_PREVIEW_POINTS].tolist() if (item := sample_identity(int(i))) is not None]
        elif "selected_indices" in arrays:
            selected = np.asarray(arrays["selected_indices"], dtype=np.int64)
            preview["selected"] = [item for i in selected[:_MAX_PREVIEW_POINTS].tolist() if (item := sample_identity(int(i))) is not None]
        elif analysis_type in ("similarity", "neighbors") and "indices" in arrays:
            indices = np.asarray(arrays["indices"], dtype=np.int64)
            distances = np.asarray(arrays.get("distances", []))
            similarities = np.asarray(arrays.get("similarity", []))
            rows = []
            if indices.ndim == 1:
                for rank, neighbor in enumerate(indices.tolist()):
                    item = sample_identity(int(neighbor))
                    if item is None:
                        continue
                    item["rank"] = rank + 1
                    if rank < distances.size:
                        item["distance"] = float(distances[rank])
                    if rank < similarities.size:
                        item["similarity"] = float(similarities[rank])
                    rows.append(item)
            elif indices.ndim == 2:
                for source_index in range(indices.shape[0]):
                    for rank, neighbor in enumerate(indices[source_index].tolist()):
                        if len(rows) >= _MAX_PREVIEW_POINTS:
                            break
                        item = sample_identity(int(neighbor))
                        source = sample_identity(source_index)
                        if item is None or source is None:
                            continue
                        item.update({"source_i": source_index, "source_frame": source["frame"], "rank": rank + 1})
                        if distances.ndim == 2 and rank < distances.shape[1]:
                            item["distance"] = float(distances[source_index, rank])
                        rows.append(item)
                    if len(rows) >= _MAX_PREVIEW_POINTS:
                        break
            preview["rows"] = rows
            preview["total_rows"] = int(indices.size if indices.ndim == 1 else indices.shape[0] * indices.shape[1])
        elif "labels" in arrays or "scores" in arrays or "distances" in arrays or "coordination" in arrays or "uncertainty" in arrays:
            lengths = [len(np.asarray(arrays[key])) for key in _PREVIEW_ARRAY_KEYS if key in arrays and np.asarray(arrays[key]).ndim == 1]
            n = min([samples.n_samples, *lengths]) if lengths else samples.n_samples
            count = min(n, _MAX_PREVIEW_POINTS)
            rows = []
            for i in range(count):
                item = sample_identity(i)
                if item is None:
                    continue
                for key in _PREVIEW_ARRAY_KEYS:
                    if key in arrays and i < len(arrays[key]):
                        value = np.asarray(arrays[key])[i]
                        if np.asarray(value).ndim == 0:
                            output_key = "element" if key == "elements" else key
                            item[output_key] = int(value) if key in ("labels", "elements", "coordination") else float(value)
                        else:
                            item[key] = self._json_safe(value)
                if "nearest_indices" in arrays and i < len(arrays["nearest_indices"]):
                    nearest = reference_identity(int(np.asarray(arrays["nearest_indices"])[i]))
                    if nearest:
                        item.update(nearest)
                rows.append(item)
            preview["rows"] = rows
            preview["total_rows"] = n
        if analysis_type == "feature_variance" and result.get("warnings"):
            preview["warnings"] = list(result["warnings"])
        return self._json_safe(preview)

    def _commit_artifact(self, analysis_id: str, analysis_type: str, input_ids: list[str], params: dict, result: dict, preview: dict, ctx) -> tuple[Path, dict]:
        return self._artifacts.commit(
            analysis_id,
            analysis_type,
            input_ids,
            params,
            result,
            preview,
            ctx,
            self._json_safe,
        )

    def _write_legacy_pca_compatibility(self, out_dir: Path, analysis_id: str, run_id: str, params: dict, preview: dict) -> None:
        points = []
        for point in preview.get("points", []):
            if not isinstance(point, dict):
                continue
            points.append({
                "i": int(point.get("i", len(points))),
                "frame": int(point.get("frame", 0)),
                **({"atom": int(point["row"])} if point.get("row") is not None else {}),
                "pc1": float(point.get("x", 0.0)),
                "pc2": float(point.get("y", 0.0)),
                "energy_per_atom": point.get("energy_per_atom"),
                "force_max": point.get("force_max"),
                "volume": point.get("volume"),
            })
        payload = {
            "analysis_id": analysis_id,
            "run_id": run_id,
            "mode": params.get("mode", "structure"),
            "preprocess": params.get("preprocess", "center"),
            "n_points": int(preview.get("total_points", len(points))),
            "points": points,
            "explained_variance": list(preview.get("explained_variance", [])),
            "x_label": preview.get("x_label", "PC1"),
            "y_label": preview.get("y_label", "PC2"),
        }
        with open_text_for_write(out_dir / "pca.json") as fh:
            json.dump(payload, fh, ensure_ascii=False)

    def _write_export(self, run: dict, selected: list[int], export_format: str, mode: str, target: Path, ctx, report_analysis: dict | None = None) -> Path:
        # Sample-index and provenance exports need neither the dataset service
        # nor a frame resolution, so they short-circuit before any loading.
        if export_format == "indices":
            if not selected:
                raise AppError(EXPORT_FAILED, "export selection is empty")
            target.parent.mkdir(parents=True, exist_ok=True)
            ensure_no_reparse_points(target.parent)
            with open_text_for_write(target) as fh:
                for index in sorted(set(int(i) for i in selected)):
                    fh.write(f"{index}\n")
            return target
        if export_format == "report":
            if not selected:
                raise AppError(EXPORT_FAILED, "export selection is empty")
            target.parent.mkdir(parents=True, exist_ok=True)
            ensure_no_reparse_points(target.parent)
            self._write_sampling_report(run, report_analysis or {}, sorted(set(int(i) for i in selected)), target)
            return target
        if self.datasets is None:
            raise AppError(EXPORT_FAILED, "dataset service is unavailable")
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run["dataset_id"],))
        if dataset is None:
            raise AppError(EXPORT_FAILED, f"dataset {run['dataset_id']} does not exist")
        adapter = self.datasets._adapter_for(dataset)
        count = len(adapter)
        # Analysis selections are sample indices, not dataset frame indices.
        # Resolve them through the same identity table used to build previews;
        # this is essential for frame-scoped runs whose only sample may be
        # dataset frame 7 (or any other non-zero frame).
        samples = self._load_samples(run, {"mode": mode}, "export")
        frames = sorted({int(samples.frame[i]) for i in selected if 0 <= i < samples.n_samples}) if selected else sorted({int(frame) for frame in samples.frame})
        frames = [frame for frame in frames if 0 <= frame < count]
        if not frames:
            raise AppError(EXPORT_FAILED, "export selection is empty")
        try:
            target = validate_local_path(str(target), field="export output path")
        except UnsafePathError as exc:
            raise AppError(EXPORT_FAILED, "export output must be an absolute local path") from exc
        if export_format == "json":
            target.parent.mkdir(parents=True, exist_ok=True)
            records = [{"sample_index": i, "frame": frame, "sample_id": f"frame:{frame}"} for i, frame in enumerate(frames)]
            ensure_no_reparse_points(target.parent)
            with open_text_for_write(target) as fh:
                json.dump({"dataset_id": dataset["id"], "run_id": run["id"], "format": dataset["format"], "records": records}, fh, ensure_ascii=False, indent=2)
            return target
        if export_format == "csv":
            target.parent.mkdir(parents=True, exist_ok=True)
            ensure_no_reparse_points(target.parent)
            with open_text_for_write(target, newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["sample_index", "frame", "sample_id"])
                writer.writeheader()
                for i, frame in enumerate(frames):
                    writer.writerow({"sample_index": i, "frame": frame, "sample_id": f"frame:{frame}"})
            return target
        if export_format == "extxyz":
            target.parent.mkdir(parents=True, exist_ok=True)
            self._write_extxyz(adapter, frames, target, ctx)
            return target
        if dataset["format"] != "deepmd":
            raise AppError(EXPORT_FAILED, "DeepMD export requires a DeepMD source dataset")
        if target.exists() and not target.is_dir():
            raise AppError(EXPORT_FAILED, "DeepMD export requires a directory destination")
        target.mkdir(parents=True, exist_ok=True)
        ensure_no_reparse_points(target)
        self._write_deepmd(adapter, frames, target, ctx)
        return target

    def _write_sampling_report(self, run: dict, analysis_row: dict, selected: list[int], target: Path) -> None:
        """Write the reprovenance JSON for a sampling selection.

        Everything needed to reproduce — and to plot — the selection lives in
        one file: parameters, coverage statistics, the coverage curve, and the
        selected sample indices.
        """
        params = self._json_load(analysis_row.get("params_json"), {})
        preview = self._json_load(analysis_row.get("preview_json"), {})
        input_ids = self._json_load(analysis_row.get("input_run_ids_json"), [run["id"]])
        existing_run_id = params.get("existing_run_id")
        curve: dict[str, list] = {"samples": [], "coverage_radius": [], "mean_residual": []}
        try:
            if analysis_row.get("result_path"):
                root = self._managed_artifact_path(str(analysis_row["id"]), analysis_row.get("result_path"))
                manifest = self._json_load(analysis_row.get("artifact_manifest_json"), {})
                files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
                for name, key in (("coverage_radius_curve", "coverage_radius"), ("coverage_mean_curve", "mean_residual"), ("coverage_r2_curve", "coverage_r2")):
                    meta = files.get(name)
                    if not isinstance(meta, dict):
                        continue
                    path = self._artifact_file(root, meta.get("path"))
                    if path is not None and path.is_file():
                        curve[key] = np.load(path, mmap_mode="r", allow_pickle=False).tolist()
                curve["samples"] = list(range(1, len(curve["coverage_radius"]) + 1))
        except (OSError, ValueError, TypeError, UnsafePathError):
            curve = {"samples": [], "coverage_radius": [], "mean_residual": []}
        report = {
            "algorithm": "farthest_point_sampling",
            "strategy": params.get("strategy") or "global",
            "descriptor": run.get("descriptor_name"),
            "descriptor_run_id": run["id"],
            "dataset_id": run["dataset_id"],
            "descriptor_dimension": preview.get("sampling_dimension"),
            "granularity": params.get("mode") or "structure",
            "scaling": preview.get("scaling"),
            "initialization": preview.get("initialization"),
            "min_distance": preview.get("min_distance"),
            "target_coverage": preview.get("target_coverage"),
            "requested_samples": preview.get("requested_count"),
            "selected_samples": len(selected),
            "n_candidates": preview.get("n_candidates"),
            "stop_reason": preview.get("stop_reason"),
            "warm_start": bool(existing_run_id),
            "existing_descriptor_run_id": existing_run_id,
            "input_run_ids": input_ids,
            "sampling_space": preview.get("sampling_space"),
            "feature_blocks": preview.get("blocks"),
            "group_allocation": preview.get("allocation"),
            "coverage": {
                "radius": preview.get("coverage_radius"),
                "r2": preview.get("coverage_r2"),
                "mean_residual": preview.get("mean_residual"),
                "p50_residual": preview.get("p50_residual"),
                "p90_residual": preview.get("p90_residual"),
                "p95_residual": preview.get("p95_residual"),
                "p99_residual": preview.get("p99_residual"),
                "max_residual": preview.get("max_residual"),
            },
            "coverage_curve": curve,
            "selected_sample_indices": selected,
            "sampling_analysis_id": analysis_row.get("id"),
            "algorithm_version": ANALYSIS_ALGORITHM_VERSION,
            "generated_at": _NOW(),
        }
        with open_text_for_write(target) as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

    @staticmethod
    def _write_extxyz(adapter, frames: list[int], target: Path, ctx) -> None:
        from ..datasets.deepmd_symbols import _Z_TO_SYMBOL

        with open_text_for_write(target) as fh:
            for pos, frame_index in enumerate(frames):
                ctx.check_cancelled()
                frame = adapter.get_frame(frame_index)
                symbols = [_Z_TO_SYMBOL.get(int(z), f"Z{int(z)}") for z in frame.numbers]
                fields = ["species:S:1", "pos:R:3"]
                if frame.forces is not None:
                    fields.append("forces:R:3")
                lattice = " ".join(f"{float(v):.12g}" for v in np.asarray(frame.cell).reshape(-1))
                comment = f'Properties={":".join(fields)}'
                if np.abs(frame.cell).sum() > 1e-12:
                    comment += f' Lattice="{lattice}" pbc="T T T"'
                if frame.energy is not None:
                    comment += f" energy={float(frame.energy):.12g}"
                fh.write(f"{len(symbols)}\n{comment}\n")
                for i, (symbol, xyz) in enumerate(zip(symbols, frame.positions)):
                    line = f"{symbol} {' '.join(f'{float(v):.12g}' for v in xyz)}"
                    if frame.forces is not None:
                        line += " " + " ".join(f"{float(v):.12g}" for v in frame.forces[i])
                    fh.write(line + "\n")

    @staticmethod
    def _write_deepmd(adapter, frames: list[int], target: Path, ctx) -> None:
        from ..datasets.deepmd_symbols import _Z_TO_SYMBOL

        first = adapter.get_frame(frames[0])
        numbers = np.asarray(first.numbers, dtype=np.int64)
        unique = sorted({int(z) for z in numbers.tolist()})
        type_map = {z: i for i, z in enumerate(unique)}
        with open_text_for_write(target / "type.raw") as fh:
            fh.write(" ".join(str(type_map[int(z)]) for z in numbers) + "\n")
        with open_text_for_write(target / "type_map.raw") as fh:
            fh.write(" ".join(_Z_TO_SYMBOL.get(z, f"Z{z}") for z in unique) + "\n")
        set_dir = target / "set.000"
        set_dir.mkdir(parents=True, exist_ok=True)
        ensure_no_reparse_points(set_dir)
        coords, cells, energies, forces, virials = [], [], [], [], []
        has_energy = has_forces = has_virial = True
        for frame_index in frames:
            ctx.check_cancelled()
            frame = adapter.get_frame(frame_index)
            coords.append(np.asarray(frame.positions, dtype=np.float64))
            cells.append(np.asarray(frame.cell, dtype=np.float64))
            if frame.energy is None:
                has_energy = False
            else:
                energies.append(float(frame.energy))
            if frame.forces is None:
                has_forces = False
            else:
                forces.append(np.asarray(frame.forces, dtype=np.float64))
            if frame.virial is None:
                has_virial = False
            else:
                virials.append(np.asarray(frame.virial, dtype=np.float64))
        np.save(set_dir / "coord.npy", np.stack(coords), allow_pickle=False)
        if any(np.abs(cell).sum() > 1e-12 for cell in cells):
            np.save(set_dir / "box.npy", np.stack(cells), allow_pickle=False)
        else:
            with open_text_for_write(set_dir / "nopbc") as fh:
                fh.write("")
        if has_energy:
            np.save(set_dir / "energy.npy", np.asarray(energies, dtype=np.float64), allow_pickle=False)
        if has_forces:
            np.save(set_dir / "force.npy", np.stack(forces), allow_pickle=False)
        if has_virial:
            np.save(set_dir / "virial.npy", np.stack(virials), allow_pickle=False)

    def _analysis_row(self, analysis_id: str | None) -> dict:
        if not analysis_id:
            raise AppError(INVALID_PARAMS, "'analysis_id' is required")
        row = self.db.query_one("SELECT * FROM analysis_runs WHERE id = ?", (analysis_id,))
        if row is None:
            raise AppError(ANALYSIS_NOT_FOUND, f"analysis {analysis_id} does not exist")
        return row

    def _public_analysis_row(self, row: dict, include_preview: bool = False) -> dict:
        output = dict(row)
        for key in ("input_run_ids_json", "dataset_ids_json", "warnings_json", "artifact_manifest_json", "preprocessing_json"):
            target = key.removesuffix("_json")
            output[target] = self._json_load(output.pop(key, None), [] if key.endswith("ids_json") or key == "warnings_json" else {})
        output.pop("params_json", None)
        output["parameters"] = self._json_load(row.get("params_json"), {})
        if include_preview:
            output["preview"] = self._json_load(row.get("preview_json"), {})
            output.pop("preview_json", None)
        else:
            output.pop("preview_json", None)
        return output

    def _require_artifact(self, row: dict) -> None:
        # STALE artifacts remain readable for audit and comparison history;
        # only _usable_run rejects stale descriptor inputs for new work.
        if row.get("status") not in ("COMPLETED", "STALE"):
            raise AppError(RESULT_INCOMPATIBLE, f"analysis {row['id']} is {row['status']}")
        if not self._artifact_is_complete(row):
            raise AppError(ARTIFACT_INVALID, f"analysis {row['id']} has no complete artifact")

    def _managed_artifact_path(self, analysis_id: str, stored: object) -> Path:
        return self._artifacts.managed_path(analysis_id, stored)

    @staticmethod
    def _artifact_file(root: Path, raw_name: object) -> Path | None:
        return AnalysisArtifactStore.artifact_file(root, raw_name)

    def _artifact_is_complete(self, row: dict) -> bool:
        return self._artifacts.is_complete(row)

    @staticmethod
    def _json_load(raw, fallback):
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
            return value if value is not None else fallback
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _canonical_params(params: dict) -> dict:
        def normalize(value):
            if isinstance(value, dict):
                return {str(k): normalize(value[k]) for k in sorted(value)}
            if isinstance(value, (list, tuple)):
                return [normalize(v) for v in value]
            if isinstance(value, float):
                if not np.isfinite(value):
                    raise AppError(ANALYSIS_INPUT_INVALID, "analysis parameters must be finite")
                return float(format(value, ".15g"))
            return value

        return normalize(params)

    @staticmethod
    def _analysis_cache_key(analysis_type: str, input_ids: list[str], params: dict) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "analysis_type": analysis_type,
                    "inputs": input_ids,
                    "params": params,
                    "algorithm_version": ANALYSIS_ALGORITHM_VERSION,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def _rows_from_artifact(self, row: dict, offset: int, limit: int) -> list[dict]:
        manifest = self._json_load(row.get("artifact_manifest_json"), {})
        files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
        arrays = {}
        try:
            root = self._managed_artifact_path(str(row.get("id") or ""), row.get("result_path"))
        except (TypeError, ValueError, UnsafePathError):
            return []
        for name in ("coords", "indices", "labels", "scores", "distances", "similarity", "selected_indices"):
            meta = files.get(name)
            if isinstance(meta, dict):
                try:
                    target = self._artifact_file(root, meta.get("path"))
                    if target is None:
                        continue
                    arrays[name] = np.load(target, mmap_mode="r", allow_pickle=False)
                except (OSError, ValueError):
                    pass
        input_ids = self._json_load(row.get("input_run_ids_json"), [row.get("descriptor_run_id")])
        if isinstance(input_ids, list) and input_ids:
            source_index = 1 if row.get("analysis_type") in ("coverage", "drift") and len(input_ids) > 1 else 0
            source_row = self.db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (input_ids[source_index],))
            if source_row is not None:
                try:
                    params = self._json_load(row.get("params_json"), {})
                    built = self._build_preview({"arrays": arrays}, self._load_samples(source_row, params, row.get("analysis_type") or ""), row.get("analysis_type") or "")
                    candidate = built.get("rows") or built.get("selected") or built.get("points") or []
                    return candidate[offset : offset + limit]
                except (AppError, OSError, TypeError, ValueError, IndexError):
                    pass
        n = max((len(v) for v in arrays.values()), default=0)
        return [{"i": i, **{name: self._json_safe(value[i]) for name, value in arrays.items() if i < len(value)}} for i in range(offset, min(offset + limit, n))]

    @staticmethod
    def _json_safe(value):
        if isinstance(value, dict):
            return {str(k): AnalysisService._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [AnalysisService._json_safe(v) for v in value]
        if isinstance(value, np.ndarray):
            return AnalysisService._json_safe(value.tolist())
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        return value

    def _rmtree_quiet(self, path: Path | None) -> None:
        self._artifacts.remove_quiet(path)


def _generic_pass_through(analysis_type: str):
    def method(self, params: dict) -> dict:
        return self.submit_generic(analysis_type, params)

    return method


for _analysis_type in sorted(_GENERIC_TYPES):
    setattr(AnalysisService, _analysis_type, _generic_pass_through(_analysis_type))
