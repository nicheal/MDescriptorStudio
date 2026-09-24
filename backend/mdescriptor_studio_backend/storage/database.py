"""SQLite storage: WAL, single-writer lock, versioned migrations (design doc §42/§43)."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE datasets (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        format TEXT NOT NULL,
        source_path TEXT NOT NULL UNIQUE,
        number_of_frames INTEGER NOT NULL,
        elements TEXT NOT NULL,
        properties TEXT NOT NULL,
        periodicity TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        file_size INTEGER,
        created_at TEXT NOT NULL,
        last_scan_at TEXT
    );
    CREATE TABLE dataset_statistics (
        dataset_id TEXT PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE,
        fingerprint TEXT NOT NULL,
        stats_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        job_type TEXT NOT NULL,
        dataset_id TEXT,
        descriptor_run_id TEXT,
        status TEXT NOT NULL,
        progress REAL NOT NULL DEFAULT 0,
        completed INTEGER,
        total INTEGER,
        message TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT
    );
    CREATE TABLE descriptor_runs (
        id TEXT PRIMARY KEY,
        dataset_id TEXT NOT NULL,
        descriptor_name TEXT NOT NULL,
        descriptor_version TEXT,
        engine_version TEXT NOT NULL,
        parameters_json TEXT NOT NULL,
        scope TEXT NOT NULL,
        frame_index INTEGER,
        output_dtype TEXT,
        cache_key TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        result_path TEXT,
        error_message TEXT
    );
    CREATE TABLE analysis_runs (
        id TEXT PRIMARY KEY,
        descriptor_run_id TEXT NOT NULL,
        analysis_type TEXT NOT NULL,
        params_json TEXT,
        status TEXT NOT NULL,
        result_path TEXT,
        created_at TEXT NOT NULL,
        finished_at TEXT
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    2: """
    ALTER TABLE jobs ADD COLUMN analysis_run_id TEXT;
    """,
    3: """
    ALTER TABLE analysis_runs ADD COLUMN input_run_ids_json TEXT;
    ALTER TABLE analysis_runs ADD COLUMN dataset_ids_json TEXT;
    ALTER TABLE analysis_runs ADD COLUMN cache_key TEXT;
    ALTER TABLE analysis_runs ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE analysis_runs ADD COLUMN algorithm_version TEXT;
    ALTER TABLE analysis_runs ADD COLUMN preprocessing_json TEXT;
    ALTER TABLE analysis_runs ADD COLUMN warnings_json TEXT;
    ALTER TABLE analysis_runs ADD COLUMN artifact_manifest_json TEXT;
    ALTER TABLE analysis_runs ADD COLUMN preview_json TEXT;
    ALTER TABLE analysis_runs ADD COLUMN stale_reason TEXT;
    ALTER TABLE analysis_runs ADD COLUMN updated_at TEXT;
    CREATE INDEX IF NOT EXISTS idx_analysis_cache_key ON analysis_runs(cache_key);
    CREATE INDEX IF NOT EXISTS idx_analysis_input_run ON analysis_runs(descriptor_run_id, analysis_type, status);
    """,
    4: """
    ALTER TABLE descriptor_runs ADD COLUMN memory_peak_bytes INTEGER;
    """,
    5: """
    ALTER TABLE descriptor_runs ADD COLUMN device TEXT;
    """,
    6: """
    CREATE TABLE dataset_excluded_frames (
        dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
        frame_index INTEGER NOT NULL,
        reason TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (dataset_id, frame_index)
    );
    """,
    7: """
    CREATE TABLE IF NOT EXISTS dataset_views (
        id TEXT PRIMARY KEY,
        dataset_id TEXT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        role TEXT,
        filter_json TEXT NOT NULL,
        frame_indices_json TEXT NOT NULL,
        selection_hash TEXT NOT NULL,
        dataset_fingerprint TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(dataset_id, name)
    );
    CREATE INDEX IF NOT EXISTS idx_dataset_views_dataset ON dataset_views(dataset_id, created_at);
    CREATE TABLE IF NOT EXISTS dataset_lineage (
        child_dataset_id TEXT PRIMARY KEY REFERENCES datasets(id) ON DELETE CASCADE,
        parent_dataset_id TEXT REFERENCES datasets(id) ON DELETE SET NULL,
        source_view_id TEXT,
        operation TEXT NOT NULL,
        selection_hash TEXT,
        created_at TEXT NOT NULL
    );
    """,
    8: """
    ALTER TABLE jobs ADD COLUMN result_json TEXT;
    """,
    9: """
    DROP TABLE IF EXISTS dataset_excluded_frames;
    """,
    10: """
    CREATE INDEX IF NOT EXISTS idx_descriptor_cache_key ON descriptor_runs(cache_key, status);
    CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
    """,
    11: """
    ALTER TABLE descriptor_runs ADD COLUMN result_shape_json TEXT;
    ALTER TABLE descriptor_runs ADD COLUMN feature_count INTEGER;
    ALTER TABLE descriptor_runs ADD COLUMN row_semantics TEXT;
    """,
    12: """
    CREATE TABLE generation_runs (
        id TEXT PRIMARY KEY,
        dataset_id TEXT NOT NULL,
        descriptor_run_id TEXT,
        optimizer TEXT NOT NULL,
        objective TEXT NOT NULL,
        params_json TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        evaluations INTEGER DEFAULT 0,
        accepted_count INTEGER DEFAULT 0,
        result_path TEXT,
        artifact_manifest_json TEXT,
        preview_json TEXT,
        warnings_json TEXT,
        cache_key TEXT,
        stale_reason TEXT,
        updated_at TEXT
    );
    CREATE INDEX idx_generation_dataset ON generation_runs(dataset_id);
    CREATE INDEX idx_generation_status ON generation_runs(status, created_at);
    ALTER TABLE jobs ADD COLUMN generation_run_id TEXT;
    """,
    13: """
    UPDATE descriptor_runs SET device = 'imported' WHERE device = 'external';
    """,
}


class Database:
    def __init__(self, path: Path):
        self.path = Path(path).resolve(strict=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._write_lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._write_lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            row = self._conn.execute("SELECT MAX(version) v FROM schema_version").fetchone()
            current = row["v"] or 0
            for version in sorted(MIGRATIONS):
                if version > current:
                    log.info("applying migration %d -> %d", current, version)
                    script = "\n".join(
                        (
                            "BEGIN IMMEDIATE;",
                            MIGRATIONS[version],
                            "DELETE FROM schema_version;",
                            f"INSERT INTO schema_version VALUES ({version});",
                            "COMMIT;",
                        )
                    )
                    try:
                        self._conn.executescript(script)
                    except BaseException:
                        # executescript() does not rollback an explicit
                        # transaction when a statement fails or is
                        # interrupted.  Roll back the whole migration so it
                        # can be retried against the same database.
                        self._conn.rollback()
                        raise

    def execute(self, sql: str, params: tuple = ()) -> int:
        """Execute one statement and return its number of affected rows."""
        with self._write_lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount

    @contextmanager
    def transaction(self):
        """Run a small group of writes atomically under the database lock."""
        with self._write_lock:
            try:
                self._conn.execute("BEGIN")
                yield self._conn
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._write_lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        with self._write_lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def get_setting(self, key: str) -> str | None:
        row = self.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def close(self) -> None:
        with self._write_lock:
            self._conn.close()
