"""Result history exposes computed descriptor shapes without loading arrays."""

from pathlib import Path

from mdescriptor_studio_backend.services.result_service import ResultService
from mdescriptor_studio_backend.storage.database import Database


def test_list_includes_shape_from_result_metadata(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements,"
        " properties, periodicity, fingerprint, file_size, created_at, last_scan_at)"
        " VALUES ('ds_1', 'd', 'deepmd', ?, 3, '[]', '{}', ?, 'fp', 0,"
        " '2026-01-01T00:00:00+00:00', NULL)",
        (str(tmp_path / "source.xyz"), '{"isolated": true, "fully_periodic": false}'),
    )
    run_dir = tmp_path / "results" / "run_1"
    run_dir.mkdir(parents=True)
    (run_dir / "metadata.json").write_text(
        '{"shape": [12, 64], "feature_count": 64, "row_semantics": "structure"}',
        encoding="utf-8",
    )
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
        " parameters_json, scope, device, status, created_at, result_path)"
        " VALUES ('run_1', 'ds_1', 'ACE', 'test', '{}', 'dataset', 'cuda', 'COMPLETED',"
        " '2026-01-01T00:00:00+00:00', ?)",
        (str(run_dir),),
    )

    rows = ResultService(db).list({"dataset_id": "ds_1"})

    assert rows[0]["shape"] == "[12, 64]"
    assert rows[0]["device"] == "cuda"
    assert rows[0]["feature_count"] == 64
    assert rows[0]["row_semantics"] == "structure"
    assert isinstance(rows[0]["feature_space_signature"], str)
    assert len(rows[0]["feature_space_signature"]) == 64


def test_list_orders_runs_submitted_in_the_same_second(tmp_path: Path) -> None:
    # created_at only has second resolution (analysis_helpers._NOW), so a batch
    # of runs submitted inside one second had an arbitrary order and could
    # reshuffle between refreshes. job_service.list_jobs already breaks the tie
    # with rowid; this is the same rule.
    db = Database(tmp_path / "db.sqlite3")
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements,"
        " properties, periodicity, fingerprint, file_size, created_at, last_scan_at)"
        " VALUES ('ds_1', 'd', 'deepmd', ?, 3, '[]', '{}', ?, 'fp', 0,"
        " '2026-01-01T00:00:00+00:00', NULL)",
        (str(tmp_path / "source.xyz"), '{"isolated": true, "fully_periodic": false}'),
    )
    for index in range(4):
        run_dir = tmp_path / "results" / f"run_{index}"
        run_dir.mkdir(parents=True)
        (run_dir / "metadata.json").write_text('{"shape": [2, 2]}', encoding="utf-8")
        db.execute(
            "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
            " parameters_json, scope, status, created_at, result_path)"
            " VALUES (?, 'ds_1', 'ACE', 'test', '{}', 'dataset', 'COMPLETED',"
            " '2026-01-01T00:00:00+00:00', ?)",
            (f"run_{index}", str(run_dir)),
        )

    listed = [row["id"] for row in ResultService(db).list({"dataset_id": "ds_1"})]

    assert listed == ["run_3", "run_2", "run_1", "run_0"], listed
    db.close()


def _insert_run(db: Database, run_id: str, run_dir: Path, columns: dict | None, metadata: str | None) -> None:
    if metadata is not None:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "metadata.json").write_text(metadata, encoding="utf-8")
    elif not run_dir.exists():
        run_dir.mkdir(parents=True)
    columns = columns or {}
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
        " parameters_json, scope, status, created_at, result_path, result_shape_json,"
        " feature_count, row_semantics) VALUES (?, 'ds_1', 'ACE', 'test', '{}', 'dataset',"
        " 'COMPLETED', '2026-01-01T00:00:00+00:00', ?, ?, ?, ?)",
        (run_id, str(run_dir), columns.get("shape"), columns.get("feature_count"), columns.get("row_semantics")),
    )


def test_list_prefers_columns_over_reading_metadata(tmp_path: Path) -> None:
    # A page of 500 runs used to mean 500 read_text + json.loads of a file that
    # also carries the per-structure provenance. Unparseable metadata now has to
    # be irrelevant for a row that reported its shape when it completed.
    db = Database(tmp_path / "db.sqlite3")
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements,"
        " properties, periodicity, fingerprint, file_size, created_at, last_scan_at)"
        " VALUES ('ds_1', 'd', 'deepmd', ?, 3, '[]', '{}', '[]', 'fp', 0,"
        " '2026-01-01T00:00:00+00:00', NULL)",
        (str(tmp_path / "source.xyz"),),
    )
    _insert_run(
        db,
        "run_cols",
        tmp_path / "results" / "run_cols",
        {"shape": "[12, 64]", "feature_count": 64, "row_semantics": "structure"},
        "{ this is not json",
    )

    row = ResultService(db).list({"dataset_id": "ds_1"})[0]
    assert row["shape"] == "[12, 64]"
    assert row["feature_count"] == 64
    assert row["row_semantics"] == "structure"
    assert "result_shape_json" not in row
    assert isinstance(row["feature_space_signature"], str)


def test_signature_is_the_same_from_columns_or_from_metadata(tmp_path: Path) -> None:
    # The hash gates cross-run analysis compatibility, so copying the fields into
    # columns may not change it for an identical run.
    db = Database(tmp_path / "db.sqlite3")
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements,"
        " properties, periodicity, fingerprint, file_size, created_at, last_scan_at)"
        " VALUES ('ds_1', 'd', 'deepmd', ?, 3, '[]', '{}', '[]', 'fp', 0,"
        " '2026-01-01T00:00:00+00:00', NULL)",
        (str(tmp_path / "source.xyz"),),
    )
    _insert_run(
        db,
        "run_file",
        tmp_path / "results" / "run_file",
        None,
        '{"shape": [12, 64], "feature_count": 64, "row_semantics": "structure", "level": "none"}',
    )
    _insert_run(
        db,
        "run_columns",
        tmp_path / "results" / "run_columns",
        {"shape": "[12, 64]", "feature_count": 64, "row_semantics": "structure"},
        None,
    )

    rows = {row["id"]: row for row in ResultService(db).list({"dataset_id": "ds_1"})}
    assert rows["run_file"]["feature_space_signature"] == rows["run_columns"]["feature_space_signature"]
    assert rows["run_file"]["shape"] == rows["run_columns"]["shape"] == "[12, 64]"
    db.close()
