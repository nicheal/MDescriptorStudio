"""Database migration transaction regressions."""

import sqlite3
from pathlib import Path

import pytest

from mdescriptor_studio_backend.storage import database as database_module
from mdescriptor_studio_backend.storage.database import Database


def _table_exists(path: Path, table: str) -> bool:
    conn = sqlite3.connect(path)
    try:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            is not None
        )
    finally:
        conn.close()


# The version assertions read max(MIGRATIONS) rather than a literal: a new
# migration is not a reason to edit this file, but a migration that fails to
# apply still is.
LATEST = max(database_module.MIGRATIONS)


def test_fresh_database_is_at_schema_v9_without_legacy_exclusions(tmp_path: Path) -> None:
    db_path = tmp_path / "database.sqlite"
    db = Database(db_path)
    try:
        assert db.query_one("SELECT MAX(version) AS version FROM schema_version")["version"] == LATEST
    finally:
        db.close()

    assert not _table_exists(db_path, "dataset_excluded_frames")


def test_execute_returns_affected_row_count(tmp_path: Path) -> None:
    db = Database(tmp_path / "database.sqlite")
    try:
        db.execute(
            "INSERT INTO jobs (id, job_type, status, created_at)"
            " VALUES ('job_probe', 'test', 'RUNNING', '2026-01-01T00:00:00+00:00')"
        )
        assert db.execute(
            "UPDATE jobs SET message = 'changed' WHERE id = 'job_probe'"
        ) == 1
        assert db.execute(
            "UPDATE jobs SET message = 'missing' WHERE id = 'job_missing'"
        ) == 0
        assert db.execute("DELETE FROM jobs WHERE id = 'job_missing'") == 0
        assert db.execute("DELETE FROM jobs WHERE id = 'job_probe'") == 1
    finally:
        db.close()


def test_schema_v8_upgrade_drops_legacy_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "database.sqlite"
    # Hold back migration 9 *and* everything after it: a v8 database is one
    # where nothing from 9 on has run, and restoring them has to restore all of
    # them for the second open to reach the current schema.
    held = {key: script for key, script in sorted(database_module.MIGRATIONS.items()) if key >= 9}
    for key in held:
        monkeypatch.delitem(database_module.MIGRATIONS, key)
    db = Database(db_path)
    try:
        assert db.query_one("SELECT MAX(version) AS version FROM schema_version")["version"] == 8
    finally:
        db.close()
    assert _table_exists(db_path, "dataset_excluded_frames")

    for key, script in held.items():
        monkeypatch.setitem(database_module.MIGRATIONS, key, script)
    db = Database(db_path)
    try:
        assert db.query_one("SELECT MAX(version) AS version FROM schema_version")["version"] == LATEST
    finally:
        db.close()

    assert not _table_exists(db_path, "dataset_excluded_frames")


def test_migration_12_adds_generation_runs(tmp_path: Path) -> None:
    db = Database(tmp_path / "database.sqlite")
    try:
        row = db.query_one(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'generation_runs'"
        )
        assert row == {"1": 1}
        columns = {r["name"] for r in db.query("PRAGMA table_info(generation_runs)")}
        assert {
            "id",
            "dataset_id",
            "descriptor_run_id",
            "optimizer",
            "objective",
            "params_json",
            "status",
            "evaluations",
            "accepted_count",
            "result_path",
            "artifact_manifest_json",
            "preview_json",
            "warnings_json",
            "cache_key",
            "stale_reason",
            "error_message",
        } <= columns
        job_columns = {r["name"] for r in db.query("PRAGMA table_info(jobs)")}
        assert "generation_run_id" in job_columns
        indexes = {
            r["name"]
            for r in db.query(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'generation_runs'"
            )
        }
        assert {"idx_generation_dataset", "idx_generation_status"} <= indexes
        # A run row round-trips with the columns the service writes.
        db.execute(
            "INSERT INTO generation_runs (id, dataset_id, descriptor_run_id, optimizer, objective,"
            " params_json, status, created_at) VALUES ('gen_x', 'ds_x', 'run_x', 'random', 'novelty',"
            " '{}', 'QUEUED', '2026-01-01T00:00:00+00:00')"
        )
        assert db.query_one("SELECT status FROM generation_runs WHERE id = 'gen_x'")["status"] == "QUEUED"
    finally:
        db.close()


def test_failed_migration_rolls_back_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "database.sqlite"
    db = Database(db_path)
    db.close()

    probe = LATEST + 1
    monkeypatch.setitem(
        database_module.MIGRATIONS,
        probe,
        """
        CREATE TABLE migration_probe (id INTEGER);
        CREATE TABLE migration_probe (id INTEGER);
        """,
    )
    with pytest.raises(sqlite3.OperationalError):
        Database(db_path)

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == LATEST
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'migration_probe'"
            ).fetchone()
            is None
        )
    finally:
        conn.close()

    monkeypatch.setitem(
        database_module.MIGRATIONS,
        probe,
        "CREATE TABLE migration_probe (id INTEGER);",
    )
    db = Database(db_path)
    try:
        assert db.query_one("SELECT MAX(version) AS version FROM schema_version")["version"] == probe
        assert db.query_one(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'migration_probe'"
        ) == {"1": 1}
    finally:
        db.close()
