"""Generic Analysis API storage, cache, and artifact lifecycle tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mdescriptor_studio_backend.analysis import AnalysisEngine, SampleMatrix
from mdescriptor_studio_backend.datasets.base import DatasetFrame
from mdescriptor_studio_backend.errors import ANALYSIS_INPUT_INVALID, ANALYSIS_STALE, AppError
from mdescriptor_studio_backend.services.analysis_service import AnalysisService
from mdescriptor_studio_backend.services.dataset_service import DatasetService
from mdescriptor_studio_backend.services.result_service import ResultService
from mdescriptor_studio_backend.storage.database import Database


class _Context:
    def check_cancelled(self):
        return None

    def progress(self, *args, **kwargs):
        return None

    def attach_control(self, _control):
        return None


class _InlineJobs:
    def __init__(self, db: Database):
        self.db = db
        self.calls = 0

    def submit(self, job_type, runner, *, dataset_id=None, descriptor_run_id=None, analysis_run_id=None):
        self.calls += 1
        job_id = f"job_{self.calls}"
        self.db.execute(
            "INSERT INTO jobs (id, job_type, dataset_id, descriptor_run_id, analysis_run_id, status, progress, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'RUNNING', 0, '2026-01-01T00:00:00+00:00')",
            (job_id, job_type, dataset_id, descriptor_run_id, analysis_run_id),
        )
        result = runner(_Context())
        self.db.execute(
            "UPDATE jobs SET status = 'COMPLETED', progress = 1, finished_at = '2026-01-01T00:00:01+00:00' WHERE id = ?",
            (job_id,),
        )
        return job_id


def _service(tmp_path: Path):
    db = Database(tmp_path / "database.sqlite")
    result_dir = tmp_path / "results" / "run_1"
    result_dir.mkdir(parents=True)
    values = np.arange(96, dtype=np.float32).reshape(12, 8)
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_1", "level": "structure", "row_semantics": "structure", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_1', 'ds_1', 'SOAP', 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )
    jobs = _InlineJobs(db)
    return db, jobs, AnalysisService(db, jobs, ResultService(db), datasets=None, data_dir=tmp_path)


def test_sensitivity_requires_same_descriptor_before_enqueue(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    result_dir = tmp_path / "results" / "run_2"
    result_dir.mkdir(parents=True)
    values = np.arange(96, dtype=np.float32).reshape(12, 8)
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_2", "level": "structure", "row_semantics": "structure", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_2', 'ds_1', 'ACSF', 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )

    with pytest.raises(AppError, match="same descriptor") as exc:
        service.sensitivity({"run_ids": ["run_1", "run_2"]})

    assert exc.value.code == ANALYSIS_INPUT_INVALID
    assert jobs.calls == 0
    db.close()


def test_cross_dataset_analysis_rejects_incompatible_feature_space_before_enqueue(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, file_size, created_at)"
        " VALUES ('ds_2', 'Query', 'extxyz', ?, 12, '[]', '{}', '{}', 'fp_2', 0, '2026-01-01T00:00:00+00:00')",
        (str(tmp_path / "query.extxyz"),),
    )
    result_dir = tmp_path / "results" / "run_2"
    result_dir.mkdir(parents=True)
    values = np.arange(96, dtype=np.float32).reshape(12, 8)
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_2", "level": "structure", "row_semantics": "structure", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_2', 'ds_2', 'ACSF', 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )

    with pytest.raises(AppError, match="same descriptor feature space") as exc:
        service.coverage({"reference_run_id": "run_1", "query_run_id": "run_2"})

    assert exc.value.code == ANALYSIS_INPUT_INVALID
    assert jobs.calls == 0
    db.close()


def test_cross_dataset_analysis_applies_views_and_persists_selection_identity(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, file_size, created_at)"
        " VALUES ('ds_1', 'Reference', 'extxyz', ?, 12, '[]', '{}', '{}', 'fp_1', 0, '2026-01-01T00:00:00+00:00')",
        (str(tmp_path / "reference.extxyz"),),
    )
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, file_size, created_at)"
        " VALUES ('ds_2', 'Query', 'extxyz', ?, 12, '[]', '{}', '{}', 'fp_2', 0, '2026-01-01T00:00:00+00:00')",
        (str(tmp_path / "query.extxyz"),),
    )
    result_dir = tmp_path / "results" / "run_2"
    result_dir.mkdir(parents=True)
    values = (np.arange(96, dtype=np.float32).reshape(12, 8) + 0.5)
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_2", "level": "structure", "row_semantics": "structure", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_2', 'ds_2', 'SOAP', 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )
    for view_id, dataset_id, fingerprint, indices, selection_hash in (
        ("view_ref", "ds_1", "fp_1", [0, 1, 2, 3, 4, 5], "selection_ref"),
        ("view_query", "ds_2", "fp_2", [6, 7, 8, 9, 10, 11], "selection_query"),
    ):
        db.execute(
            "INSERT INTO dataset_views (id, dataset_id, name, role, filter_json, frame_indices_json, selection_hash, dataset_fingerprint, created_at, updated_at)"
            " VALUES (?, ?, ?, 'filtered', '{}', ?, ?, ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (view_id, dataset_id, view_id, json.dumps(indices), selection_hash, fingerprint),
        )

    submitted = service.coverage(
        {
            "reference_run_id": "run_1",
            "query_run_id": "run_2",
            "reference_view_id": "view_ref",
            "query_view_id": "view_query",
            "mode": "structure",
        }
    )

    assert submitted["job_id"] == "job_1"
    row = db.query_one("SELECT params_json FROM analysis_runs WHERE id = ?", (submitted["analysis_id"],))
    saved = json.loads(row["params_json"])
    assert saved["reference_selection_hash"] == "selection_ref"
    assert saved["query_selection_hash"] == "selection_query"
    preview = service.preview({"analysis_id": submitted["analysis_id"], "limit": 20})
    assert {item["frame"] for item in preview["rows"]} == set(range(6, 12))
    db.close()


def test_single_run_analysis_scopes_to_dataset_view(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, file_size, created_at)"
        " VALUES ('ds_1', 'Reference', 'extxyz', ?, 12, '[]', '{}', '{}', 'fp_1', 0, '2026-01-01T00:00:00+00:00')",
        (str(tmp_path / "reference.extxyz"),),
    )
    db.execute(
        "INSERT INTO dataset_views (id, dataset_id, name, role, filter_json, frame_indices_json, selection_hash, dataset_fingerprint, created_at, updated_at)"
        " VALUES ('view_v1', 'ds_1', 'v1', 'filtered', '{}', ?, 'selection_v1', 'fp_1', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        (json.dumps([2, 4, 6, 8]),),
    )

    submitted = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42, "view_id": "view_v1"})

    row = db.query_one("SELECT params_json FROM analysis_runs WHERE id = ?", (submitted["analysis_id"],))
    saved = json.loads(row["params_json"])
    assert saved["view_id"] == "view_v1"
    assert saved["selection_hash"] == "selection_v1"
    preview = service.preview({"analysis_id": submitted["analysis_id"], "limit": 20})
    assert {item["frame"] for item in preview["rows"]} == {2, 4, 6, 8}

    # The selection hash joins the cache key: the identical request reuses the
    # artifact, a different view computes fresh.
    cached = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42, "view_id": "view_v1"})
    assert cached["job_id"] is None
    assert cached["cache"]["existing_analysis_id"] == submitted["analysis_id"]
    db.execute(
        "INSERT INTO dataset_views (id, dataset_id, name, role, filter_json, frame_indices_json, selection_hash, dataset_fingerprint, created_at, updated_at)"
        " VALUES ('view_v2', 'ds_1', 'v2', 'filtered', '{}', ?, 'selection_v2', 'fp_1', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        (json.dumps([1, 3, 5]),),
    )
    other = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42, "view_id": "view_v2"})
    assert other["analysis_id"] != submitted["analysis_id"]
    assert jobs.calls == 2

    # A stale view fingerprint must be rejected before enqueue.
    db.execute(
        "INSERT INTO dataset_views (id, dataset_id, name, role, filter_json, frame_indices_json, selection_hash, dataset_fingerprint, created_at, updated_at)"
        " VALUES ('view_stale', 'ds_1', 'stale', 'filtered', '{}', ?, 'selection_stale', 'fp_old', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        (json.dumps([0, 1]),),
    )
    with pytest.raises(AppError, match="is stale"):
        service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42, "view_id": "view_stale"})
    db.close()


def test_single_run_view_id_rejected_for_two_run_analyses(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    result_dir = tmp_path / "results" / "run_2"
    result_dir.mkdir(parents=True)
    values = np.arange(96, dtype=np.float32).reshape(12, 8)
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_2", "level": "structure", "row_semantics": "structure", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_2', 'ds_1', 'ACSF', 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )

    with pytest.raises(AppError, match="view_id applies to single-run analyses only"):
        service.compare({"left_run_id": "run_1", "right_run_id": "run_2", "mode": "structure", "view_id": "view_v1"})

    assert jobs.calls == 0
    db.close()


def test_dataset_view_slice_preserves_original_frame_identity() -> None:
    samples = SampleMatrix(
        values=np.arange(20, dtype=np.float64).reshape(5, 4),
        frame=np.asarray([0, 1, 1, 2, 4]),
        row=np.asarray([0, 0, 1, 0, 0]),
        sample_ids=["f0:r0", "f1:r0", "f1:r1", "f2:r0", "f4:r0"],
        elements=np.asarray([1, 6, 8, 14, 32]),
        mode="atom",
        properties={"energy": np.asarray([0.0, 1.0, 1.1, 2.0, 4.0])},
        positions=np.arange(15, dtype=np.float64).reshape(5, 3),
        cells=np.repeat(np.eye(3)[None, :, :], 5, axis=0),
        pbc=np.ones((5, 3), dtype=bool),
    )

    sliced = AnalysisService._slice_samples_to_frames(samples, [1, 4])

    assert sliced.frame.tolist() == [1, 1, 4]
    assert sliced.row.tolist() == [0, 1, 0]
    assert sliced.sample_ids == ["f1:r0", "f1:r1", "f4:r0"]
    assert sliced.values.shape == (3, 4)
    assert sliced.properties["energy"].tolist() == [1.0, 1.1, 4.0]
    assert sliced.positions.shape == (3, 3)


def test_generic_analysis_is_cached_and_chunked(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    first = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 3, "seed": 42})
    assert first["job_id"] == "job_1"
    assert jobs.calls == 1

    row = db.query_one("SELECT * FROM analysis_runs WHERE id = ?", (first["analysis_id"],))
    assert row["status"] == "COMPLETED"
    assert row["cache_key"]
    manifest = json.loads((Path(row["result_path"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["completed"] is True
    assert (Path(row["result_path"]) / "labels.npy").is_file()

    cached = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 3, "seed": 42})
    assert cached["job_id"] is None
    assert cached["analysis_id"] == first["analysis_id"]
    assert jobs.calls == 1

    preview = service.preview({"analysis_id": first["analysis_id"], "limit": 3})
    assert len(preview["rows"]) == 3
    chunk = service.chunk({"analysis_id": first["analysis_id"], "array": "labels", "limit": 4})
    assert chunk["data"] and chunk["next_offset"] == 4

    listed = service.list({"analysis_type": "kmeans"})
    assert listed[0]["id"] == first["analysis_id"]
    assert listed[0]["parameters"]["n_clusters"] == 3
    db.close()


def test_feature_variance_persists_full_schema_and_invalid_warning(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    values = np.arange(96, dtype=np.float64).reshape(12, 8)
    values[1, 0] = np.nan
    values[3, 4] = np.inf
    np.save(tmp_path / "results" / "run_1" / "values.npy", values)

    response = service.feature_variance({"run_id": "run_1", "top_k": 4, "near_zero_relative_threshold": 0.002, "low_variance_relative_threshold": 0.02})
    assert response["job_id"] == "job_1"
    analysis = service.get({"analysis_id": response["analysis_id"]})
    assert analysis["parameters"]["feature_variance_schema"] == 2
    assert analysis["parameters"]["near_zero_relative_threshold"] == 0.002

    preview = service.preview({"analysis_id": response["analysis_id"], "limit": 20})
    assert preview["schema_version"] == 2
    assert preview["settings"]["low_variance_relative_threshold"] == 0.02
    assert len(preview["features"]) == 8
    assert "rows" not in preview
    assert preview["features"][0]["invalid_count"] == 1
    assert preview["features"][4]["invalid_count"] == 1
    assert any("non-finite" in warning for warning in preview["warnings"])

    chunk = service.chunk({"analysis_id": response["analysis_id"], "array": "histogram_counts", "offset": 4, "limit": 1})
    assert chunk["data"] and len(chunk["data"][0]) == 32
    db.close()


def test_stale_artifacts_remain_auditable_but_cannot_feed_new_work(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    created = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42})
    assert jobs.calls == 1

    DatasetService(db, adapter=None, jobs=jobs)._mark_runs_stale("ds_1", "fixture source changed")
    run = db.query_one("SELECT status FROM descriptor_runs WHERE id = 'run_1'")
    analysis = db.query_one("SELECT status FROM analysis_runs WHERE id = ?", (created["analysis_id"],))
    assert run["status"] == "STALE"
    assert analysis["status"] == "STALE"

    # Historical artifacts remain readable for audit, but a stale descriptor
    # cannot be used as the input of a new analysis or cache hit.
    assert service.get({"analysis_id": created["analysis_id"]})["status"] == "STALE"
    assert service.preview({"analysis_id": created["analysis_id"], "limit": 2})["rows"]
    try:
        service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42})
    except AppError as exc:
        assert exc.code == ANALYSIS_STALE
    else:
        raise AssertionError("stale descriptor run unexpectedly accepted")
    db.close()


def test_incomplete_artifact_is_not_returned_as_a_cache_hit(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    created = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42})
    row = db.query_one("SELECT result_path FROM analysis_runs WHERE id = ?", (created["analysis_id"],))
    (Path(row["result_path"]) / "manifest.json").unlink()

    recreated = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42})
    assert recreated["job_id"] == "job_2"
    assert recreated["analysis_id"] != created["analysis_id"]
    db.close()


def test_coverage_preview_and_count_follow_query_samples(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    query_dir = tmp_path / "results" / "run_2"
    query_dir.mkdir(parents=True)
    query_values = np.arange(24, dtype=np.float32).reshape(3, 8)
    np.save(query_dir / "values.npy", query_values)
    (query_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_2", "level": "structure", "row_semantics": "structure", "shape": list(query_values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, status, created_at, result_path) VALUES ('run_2', 'ds_1', 'SOAP', 'test', '{}',"
        " 'dataset', 'COMPLETED', '2026-01-01T00:00:01+00:00', ?)",
        (str(query_dir),),
    )

    response = service.coverage({"reference_run_id": "run_1", "query_run_id": "run_2"})
    preview = service.preview({"analysis_id": response["analysis_id"], "limit": 20})
    assert len(preview["rows"]) == 3
    assert [row["sample_id"] for row in preview["rows"]] == ["frame:0", "frame:1", "frame:2"]
    db.close()


def test_legacy_pca_cache_includes_preprocessing_mode(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    centered = service.pca({"run_id": "run_1", "preprocess": "center"})
    raw = service.pca({"run_id": "run_1", "preprocess": "raw"})
    assert centered["analysis_id"] != raw["analysis_id"]
    assert jobs.calls == 2
    cache_keys = db.query("SELECT cache_key FROM analysis_runs WHERE analysis_type = 'pca' ORDER BY id")
    assert all(row["cache_key"] for row in cache_keys)
    assert len({row["cache_key"] for row in cache_keys}) == 2
    raw_row = db.query_one("SELECT result_path FROM analysis_runs WHERE id = ?", (raw["analysis_id"],))
    raw_payload = json.loads((Path(raw_row["result_path"]) / "pca.json").read_text(encoding="utf-8"))
    assert raw_payload["preprocess"] == "raw"
    db.close()


def test_legacy_pca_pads_the_second_coordinate_for_one_feature(tmp_path: Path) -> None:
    db, _jobs, service = _service(tmp_path)
    values = np.arange(4, dtype=np.float64).reshape(4, 1)
    coords, explained = service._pca(values, "center")
    assert coords.shape == (4, 2)
    assert explained.shape == (1,)
    assert np.allclose(coords[:, 1], 0.0)
    db.close()


def test_similarity_preview_resolves_neighbor_indices_to_neighbor_identity(tmp_path: Path) -> None:
    db, _jobs, service = _service(tmp_path)
    values = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 2.0], [10.0, 0.0]])
    samples = SampleMatrix(values, np.array([10, 11, 12, 13]), sample_ids=[f"frame:{i}" for i in [10, 11, 12, 13]])
    result = AnalysisEngine.similarity(samples, {"query_index": 0, "k": 2, "metric": "euclidean"})
    preview = service._build_preview(result, samples, "similarity")
    assert [row["i"] for row in preview["rows"]] == [1, 2]
    assert [row["frame"] for row in preview["rows"]] == [11, 12]
    assert len(preview["rows"]) == 2
    db.close()


def test_neighbors_preview_keeps_source_and_neighbor_identity(tmp_path: Path) -> None:
    db, _jobs, service = _service(tmp_path)
    values = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 2.0]])
    samples = SampleMatrix(values, np.array([20, 21, 22]), sample_ids=["frame:20", "frame:21", "frame:22"])
    result = AnalysisEngine.neighbors(samples, {"k": 1, "metric": "euclidean"})
    preview = service._build_preview(result, samples, "neighbors")
    assert preview["total_rows"] == 3
    assert {row["source_i"] for row in preview["rows"]} == {0, 1, 2}
    assert all("distance" in row and "frame" in row for row in preview["rows"])
    db.close()


def test_frame_scoped_atom_identity_uses_the_actual_frame_index(tmp_path: Path) -> None:
    db, _jobs, service = _service(tmp_path)
    result_dir = tmp_path / "results" / "run_frame"
    result_dir.mkdir(parents=True)
    values = np.arange(6, dtype=np.float32).reshape(2, 3)
    np.save(result_dir / "values.npy", values)
    np.save(result_dir / "row_offsets.npy", np.array([0, 2], dtype=np.int64))
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_frame", "level": "atom", "row_semantics": "atom", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, frame_index, status, created_at, result_path) VALUES ('run_frame', 'ds_1', 'SOAP', 'test', '{}',"
        " 'frame', 7, 'COMPLETED', '2026-01-01T00:00:02+00:00', ?)",
        (str(result_dir),),
    )
    row = db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_frame'")
    samples = service._load_samples(row, {"mode": "atom"}, "similarity")
    assert samples.frame.tolist() == [7, 7]
    assert samples.row.tolist() == [0, 1]
    assert samples.sample_ids == ["frame:7:row:0", "frame:7:row:1"]

    _coords, _explained, frames, atoms = service._pca_points(values, row, "atom")
    assert frames.tolist() == [7, 7]
    assert atoms.tolist() == [0, 1]
    _coords, _explained, frames, atoms = service._pca_points(values, row, "structure")
    assert frames.tolist() == [7]
    assert atoms is None
    heatmap = service.results.heatmap({"run_id": "run_frame", "frame_index": 7})
    assert heatmap["values"] == values.tolist()
    db.close()


def test_frame_scoped_export_resolves_selected_sample_to_actual_frame(tmp_path: Path) -> None:
    db, _jobs, service = _service(tmp_path)
    result_dir = tmp_path / "results" / "run_export_frame"
    result_dir.mkdir(parents=True)
    values = np.array([[1.0, 2.0]])
    np.save(result_dir / "values.npy", values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_export_frame", "level": "structure", "row_semantics": "structure", "shape": list(values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, created_at)"
        " VALUES ('ds_export', 'export', 'deepmd', 'source', 8, '[]', '{}', '{}', 'fingerprint', '2026-01-01T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json,"
        " scope, frame_index, status, created_at, result_path) VALUES ('run_export_frame', 'ds_export', 'SOAP', 'test', '{}',"
        " 'frame', 7, 'COMPLETED', '2026-01-01T00:00:03+00:00', ?)",
        (str(result_dir),),
    )

    class _Adapter:
        def __len__(self):
            return 8

    class _Datasets:
        def _adapter_for(self, _dataset):
            return _Adapter()

    service.datasets = _Datasets()
    run = db.query_one("SELECT * FROM descriptor_runs WHERE id = 'run_export_frame'")
    output = service._write_export(run, [0], "json", "structure", tmp_path / "export.json", _Context())
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert [record["frame"] for record in payload["records"]] == [7]
    db.close()


def test_structural_perturbation_service_recomputes_descriptor_sweep(tmp_path: Path) -> None:
    db = Database(tmp_path / "database.sqlite")
    source_path = tmp_path / "source.xyz"
    source_path.write_text("fixture", encoding="utf-8")
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, created_at)"
        " VALUES ('ds_perturb', 'perturb', 'extxyz', ?, 4, '[\"H\"]', '{}', '{\"isolated\": true}', 'fixture', '2026-01-01T00:00:00+00:00')",
        (str(source_path),),
    )

    frames = []
    baseline_values = []
    for index in range(4):
        positions = np.array(
            [[float(index), 0.1 * index, 0.2], [float(index) + 0.5, 0.1 * index + 0.2, 0.4]],
            dtype=np.float64,
        )
        frames.append(
            DatasetFrame(
                numbers=np.array([1, 1], dtype=np.int64),
                positions=positions,
                cell=np.zeros((3, 3), dtype=np.float64),
                pbc=np.zeros(3, dtype=bool),
                index=index,
                id=f"frame_{index}",
            )
        )
        baseline_values.append([positions[:, 0].mean(), positions[:, 1].mean() + 2.0 * positions[:, 2].mean()])
    baseline_values = np.asarray(baseline_values, dtype=np.float64)
    result_dir = tmp_path / "results" / "run_perturb"
    result_dir.mkdir(parents=True)
    np.save(result_dir / "values.npy", baseline_values)
    (result_dir / "metadata.json").write_text(
        json.dumps({"run_id": "run_perturb", "level": "structure", "row_semantics": "structure", "shape": list(baseline_values.shape)}),
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json, scope, status, created_at, result_path)"
        " VALUES ('run_perturb', 'ds_perturb', 'FAKE', 'test', '{}', 'dataset', 'COMPLETED', '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )

    class _SourceAdapter:
        def __len__(self):
            return len(frames)

        def get_frame(self, index):
            return frames[index]

    class _ComputeAdapter:
        def build(self, _name, parameters, device="cpu"):
            return parameters

        @staticmethod
        def make_control():
            return object()

        @staticmethod
        def to_structure_batch(batch):
            return batch

        @staticmethod
        def compute(_descriptor, batch, _control=None):
            values = [
                [frame.positions[:, 0].mean(), frame.positions[:, 1].mean() + 2.0 * frame.positions[:, 2].mean()]
                for frame in batch
            ]
            return SimpleNamespace(values=np.asarray(values, dtype=np.float64), row_offsets=None)

    source_adapter = _SourceAdapter()

    class _Datasets:
        adapter = _ComputeAdapter()

        @staticmethod
        def _adapter_for(_dataset):
            return source_adapter

    jobs = _InlineJobs(db)
    service = AnalysisService(db, jobs, ResultService(db), datasets=_Datasets(), data_dir=tmp_path)
    service._assert_dataset_current = lambda _row: None

    response = service.perturbation_sensitivity(
        {
            "run_id": "run_perturb",
            "perturbation": "jitter",
            "amplitudes": [0.0, 0.05, 0.1],
            "max_structures": 4,
            "seed": 42,
        }
    )
    assert response["job_id"] == "job_1"
    analysis = service.get({"analysis_id": response["analysis_id"]})
    assert analysis["status"] == "COMPLETED"
    assert analysis["preview"]["kind"] == "perturbation_sensitivity"
    assert analysis["preview"]["curve_count"] == 3
    curve = service.chunk({"analysis_id": response["analysis_id"], "array": "mean_response", "limit": 10})
    assert len(curve["data"]) == 3
    assert curve["data"][0] == pytest.approx(0.0)
    db.close()
