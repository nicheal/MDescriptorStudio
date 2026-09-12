"""Persistent PCA cache and duplicate-submission behavior."""

from pathlib import Path

from mdescriptor_studio_backend.services.analysis_service import AnalysisService
from mdescriptor_studio_backend.services.result_service import ResultService
from mdescriptor_studio_backend.storage.database import Database


class _Jobs:
    def __init__(self, db: Database):
        self.db = db
        self.calls = []

    def submit(self, job_type, runner, *, dataset_id=None, analysis_run_id=None):
        job_id = f"job_{len(self.calls) + 1}"
        self.calls.append((job_type, runner, dataset_id, analysis_run_id))
        self.db.execute(
            "INSERT INTO jobs (id, job_type, dataset_id, analysis_run_id, status, progress, created_at)"
            " VALUES (?, ?, ?, ?, 'QUEUED', 0, '2026-01-01T00:00:00+00:00')",
            (job_id, job_type, dataset_id, analysis_run_id),
        )
        return job_id


def _service(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    result_dir = tmp_path / "results" / "run_1"
    result_dir.mkdir(parents=True)
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version,"
        " parameters_json, scope, status, created_at, result_path)"
        " VALUES ('run_1', 'ds_1', 'ACE', 'test', '{}', 'dataset', 'COMPLETED',"
        " '2026-01-01T00:00:00+00:00', ?)",
        (str(result_dir),),
    )
    jobs = _Jobs(db)
    service = AnalysisService(db, jobs, ResultService(db), datasets=None, data_dir=tmp_path)
    return db, jobs, service


def _complete(db: Database, analysis_id: str, tmp_path: Path) -> None:
    analysis_dir = tmp_path / "analysis" / analysis_id
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "pca.json").write_text(
        '{"analysis_id":"%s","points":[],"explained_variance":[],"x_label":"","y_label":""}'
        % analysis_id,
        encoding="utf-8",
    )
    db.execute(
        "UPDATE analysis_runs SET status = 'COMPLETED', result_path = ? WHERE id = ?",
        (str(analysis_dir), analysis_id),
    )


def test_pca_reuses_running_and_completed_results(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)

    first = service.pca({"run_id": "run_1", "mode": "structure"})
    again_while_running = service.pca({"run_id": "run_1", "mode": "structure"})
    assert again_while_running["job_id"] == first["job_id"]
    assert again_while_running["analysis_id"] == first["analysis_id"]
    assert again_while_running["cache"]["status"] == "QUEUED"
    assert len(jobs.calls) == 1

    _complete(db, first["analysis_id"], tmp_path)
    cached = service.pca({"run_id": "run_1", "mode": "structure"})
    assert cached["job_id"] is None
    assert cached["analysis_id"] == first["analysis_id"]
    assert cached["cache"]["existing_analysis_id"] == first["analysis_id"]
    assert db.query("SELECT id FROM analysis_runs WHERE descriptor_run_id = 'run_1'") == [
        {"id": first["analysis_id"]}
    ]

    # The cache is on disk/SQLite, not only on the service instance.
    db.close()
    reopened = Database(tmp_path / "db.sqlite")
    restarted_jobs = _Jobs(reopened)
    restarted = AnalysisService(
        reopened, restarted_jobs, ResultService(reopened), datasets=None, data_dir=tmp_path
    ).pca({"run_id": "run_1", "mode": "structure"})
    assert restarted["job_id"] is None
    assert restarted["analysis_id"] == first["analysis_id"]
    assert not restarted_jobs.calls
    reopened.close()


def test_pca_mode_is_part_of_cache_key(tmp_path: Path) -> None:
    db, jobs, service = _service(tmp_path)
    structure = service.pca({"run_id": "run_1", "mode": "structure"})
    _complete(db, structure["analysis_id"], tmp_path)

    atom = service.pca({"run_id": "run_1", "mode": "atom"})
    assert atom["analysis_id"] != structure["analysis_id"]
    assert atom["job_id"] != structure["job_id"]
    assert len(jobs.calls) == 2
