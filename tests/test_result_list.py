"""Result history exposes computed descriptor shapes without loading arrays."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from mdescriptor_studio_backend.services.result_service import ResultService  # noqa: E402
from mdescriptor_studio_backend.storage.database import Database  # noqa: E402


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
    (run_dir / "metadata.json").write_text('{"shape": [12, 64]}', encoding="utf-8")
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
