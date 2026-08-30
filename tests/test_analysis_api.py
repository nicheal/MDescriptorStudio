"""Generic Analysis API storage, cache, and artifact lifecycle tests."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from mdescriptor_studio_backend.analysis import AnalysisEngine, SampleMatrix
from mdescriptor_studio_backend.errors import ANALYSIS_STALE, AppError
from mdescriptor_studio_backend.services.analysis_service import AnalysisService
from mdescriptor_studio_backend.services.result_service import ResultService
from mdescriptor_studio_backend.storage.database import Database


class _Context:
    def check_cancelled(self):
        return None

    def progress(self, *args, **kwargs):
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


def test_stale_artifacts_remain_auditable_but_cannot_feed_new_work(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    created = service.cluster({"run_id": "run_1", "algorithm": "kmeans", "n_clusters": 2, "seed": 42})
    assert jobs.calls == 1

    service._mark_stale("ds_1", "fixture source changed")
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
