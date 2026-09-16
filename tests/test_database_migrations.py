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


def test_fresh_database_is_at_schema_v9_without_legacy_exclusions(tmp_path: Path) -> None:
    db_path = tmp_path / "database.sqlite"
    db = Database(db_path)
    try:
        assert db.query_one("SELECT MAX(version) AS version FROM schema_version")["version"] == 9
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
    migration9 = database_module.MIGRATIONS[9]
    monkeypatch.delitem(database_module.MIGRATIONS, 9)
    db = Database(db_path)
    try:
        assert db.query_one("SELECT MAX(version) AS version FROM schema_version")["version"] == 8
    finally:
        db.close()
    assert _table_exists(db_path, "dataset_excluded_frames")

    monkeypatch.setitem(database_module.MIGRATIONS, 9, migration9)
    db = Database(db_path)
    try:
        assert db.query_one("SELECT MAX(version) AS version FROM schema_version")["version"] == 9
    finally:
        db.close()

    assert not _table_exists(db_path, "dataset_excluded_frames")


def test_failed_migration_rolls_back_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "database.sqlite"
    db = Database(db_path)
    db.close()

    monkeypatch.setitem(
        database_module.MIGRATIONS,
        10,
        """
        CREATE TABLE migration_probe (id INTEGER);
        CREATE TABLE migration_probe (id INTEGER);
        """,
    )
    with pytest.raises(sqlite3.OperationalError):
        Database(db_path)

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 9
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
        10,
        "CREATE TABLE migration_probe (id INTEGER);",
    )
    db = Database(db_path)
    try:
        assert db.query_one("SELECT MAX(version) AS version FROM schema_version")["version"] == 10
        assert db.query_one(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'migration_probe'"
        ) == {"1": 1}
    finally:
        db.close()
