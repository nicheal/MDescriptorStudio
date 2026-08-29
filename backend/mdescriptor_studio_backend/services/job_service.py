"""JobService: all slow operations run here (design doc Rule 9, ADR-8/16).

Minimal M1 core (queue + progress + persistence); cooperative cancellation
via engine ComputeControl wired in M4 (ADR-13 arrives at the same code path).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..errors import AppError, INVALID_PARAMS, JOB_CANCELLED, JOB_NOT_FOUND
from ..storage.database import Database

log = logging.getLogger(__name__)

_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731


class JobContext:
    def __init__(self, service: "JobService", job_id: str):
        self.service = service
        self.job_id = job_id
        self.control = None  # engine ComputeControl, attached by compute jobs
        self._cancelled = threading.Event()
        self._last_emit = 0.0
        self._last_fraction = -1.0
        self._last_message = None

    def attach_control(self, control) -> None:
        self.control = control
        if self._cancelled.is_set():
            control.cancel()

    def cancel(self) -> None:
        self._cancelled.set()
        if self.control is not None:
            try:
                self.control.cancel()
            except Exception:  # noqa: BLE001 - cancel must never crash the worker
                log.exception("engine control.cancel failed")

    def check_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise AppError(JOB_CANCELLED, f"job {self.job_id} cancelled")

    def progress(
        self,
        completed: int | None,
        total: int | None,
        message: str | None = None,
        *,
        fraction: float | None = None,
    ) -> None:
        """Report progress. `fraction` (0-1) overrides completed/total on the
        bar — pass it to weight sub-phases onto one bar; completed/total may
        then be None so the UI shows only the message (no stale counters)."""
        if fraction is None:
            fraction = (completed / total) if total else 0.0
        now = time.monotonic()
        # throttle: >=1% jump, every 200ms, or a phase change (message change
        # or fraction decrease) — without the phase-change clause a downward
        # reset is always "less than 1% progress" and gets swallowed, leaving
        # the previous phase's 100% on screen for the whole next phase
        if (
            fraction >= self._last_fraction
            and fraction - self._last_fraction < 0.01
            and message == self._last_message
            and now - self._last_emit < 0.2
        ):
            return
        self._last_fraction = fraction
        self._last_message = message
        self._last_emit = now
        self.service._update_progress(self.job_id, fraction, completed, total, message)


class JobService:
    def __init__(self, db: Database, emit):
        self.db = db
        self.emit = emit
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")
        self._contexts: dict[str, JobContext] = {}
        self._lock = threading.Lock()
        # jobs left non-terminal by a previous session can never finish: close them.
        # Their run rows are zombies too — nothing will ever settle them.
        self._sweep_zombie_runs("backend_restart")
        self.db.execute(
            "UPDATE jobs SET status = 'CANCELLED', finished_at = ?, error = 'backend_restart'"
            " WHERE status IN ('QUEUED', 'RUNNING')",
            (_NOW(),),
        )

    # -- submit / run -----------------------------------------------------
    def submit(
        self,
        job_type: str,
        runner,
        dataset_id: str | None = None,
        descriptor_run_id: str | None = None,
        analysis_run_id: str | None = None,
    ) -> str:
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        self.db.execute(
            "INSERT INTO jobs (id, job_type, dataset_id, descriptor_run_id, analysis_run_id, status, progress, created_at)"
            " VALUES (?, ?, ?, ?, ?, 'QUEUED', 0, ?)",
            (job_id, job_type, dataset_id, descriptor_run_id, analysis_run_id, _NOW()),
        )
        with self._lock:
            self._contexts[job_id] = JobContext(self, job_id)
        self._executor.submit(self._run, job_id, job_type, runner)
        return job_id

    def _sweep_zombie_runs(self, reason: str) -> None:
        """Settle run rows abandoned non-terminal (crash/restart/shutdown)."""
        self.db.execute(
            "UPDATE descriptor_runs SET status = 'CANCELLED', finished_at = ?, error_message = ?"
            " WHERE status IN ('QUEUED', 'RUNNING')",
            (_NOW(), reason),
        )
        self.db.execute(
            "UPDATE analysis_runs SET status = 'CANCELLED', finished_at = ?"
            " WHERE status IN ('QUEUED', 'RUNNING')",
            (_NOW(),),
        )

    def _run(self, job_id: str, job_type: str, runner) -> None:
        with self._lock:
            ctx = self._contexts[job_id]
        self.db.execute(
            "UPDATE jobs SET status = 'RUNNING', started_at = ? WHERE id = ?", (_NOW(), job_id)
        )
        log.info("job %s (%s) started", job_id, job_type)
        try:
            result = runner(ctx)
            self._finish(job_id, "COMPLETED", error=None, result=result)
        except AppError as exc:
            status = "CANCELLED" if exc.code == JOB_CANCELLED else "FAILED"
            self._finish(job_id, status, error={"code": exc.code, "message": exc.message})
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s crashed", job_id)
            self._finish(job_id, "FAILED", error={"code": "INTERNAL_ERROR", "message": str(exc)})

    def _finish(self, job_id: str, status: str, error, result=None) -> None:
        self.db.execute(
            "UPDATE jobs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
            (status, _NOW(), error["code"] if error else None, job_id),
        )
        if status in ("FAILED", "CANCELLED"):
            self._settle_linked_runs(job_id, status, error)
        self.emit(
            "job.finished",
            {
                "job_id": job_id,
                "status": status,
                "result": result,
                "error": error,
            },
        )
        with self._lock:
            self._contexts.pop(job_id, None)
        log.info("job %s -> %s", job_id, status)

    def _settle_linked_runs(self, job_id: str, status: str, error) -> None:
        """A failed/cancelled job must settle its run rows: the COMPLETED path is
        the only place runners update them, so without this they stay RUNNING
        forever (Jobs page FAILED, Results page RUNNING)."""
        row = self.db.query_one(
            "SELECT descriptor_run_id, analysis_run_id FROM jobs WHERE id = ?", (job_id,)
        )
        if row is None:
            return
        message = (error or {}).get("message") or status
        if row["descriptor_run_id"]:
            self.db.execute(
                "UPDATE descriptor_runs SET status = ?, finished_at = ?, error_message = ?"
                " WHERE id = ? AND status IN ('QUEUED', 'RUNNING')",
                (status, _NOW(), message, row["descriptor_run_id"]),
            )
        if row["analysis_run_id"]:
            self.db.execute(
                "UPDATE analysis_runs SET status = ?, finished_at = ?"
                " WHERE id = ? AND status IN ('QUEUED', 'RUNNING')",
                (status, _NOW(), row["analysis_run_id"]),
            )

    def _update_progress(self, job_id, fraction, completed, total, message) -> None:
        self.db.execute(
            "UPDATE jobs SET progress = ?, completed = ?, total = ?, message = ? WHERE id = ?",
            (fraction, completed, total, message, job_id),
        )
        self.emit(
            "job.progress",
            {
                "job_id": job_id,
                "progress": round(fraction, 4),
                "completed": completed,
                "total": total,
                "message": message,
            },
        )

    # -- queries / cancel ----------------------------------------------------
    def cancel(self, job_id: str) -> dict:
        with self._lock:
            ctx = self._contexts.get(job_id)
        row = self.get_job(job_id)
        if row is None:
            raise AppError(JOB_NOT_FOUND, f"job {job_id} does not exist")
        if row["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return {"ok": True, "already_finished": True}
        if ctx is None:
            raise AppError(INVALID_PARAMS, f"job {job_id} is queued but has no context yet")
        ctx.cancel()
        return {"ok": True, "already_finished": False}

    def get_job(self, job_id: str) -> dict | None:
        return self.db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))

    def list_jobs(self, params: dict) -> list[dict]:
        sql = "SELECT * FROM jobs"
        cond, args = [], []
        if params.get("dataset_id"):
            cond.append("dataset_id = ?")
            args.append(params["dataset_id"])
        if params.get("status"):
            cond.append("status = ?")
            args.append(params["status"])
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY created_at DESC LIMIT 200"
        return self.db.query(sql, tuple(args))

    def shutdown(self, wait_seconds: float = 3.0) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            with self._lock:
                if not self._contexts:
                    break
            time.sleep(0.05)
        # anything still non-terminal can no longer reach the about-to-close db:
        # close it out here so the table never keeps zombie RUNNING rows
        self._sweep_zombie_runs("backend_shutdown")
        self.db.execute(
            "UPDATE jobs SET status = 'CANCELLED', finished_at = ?, error = 'backend_shutdown'"
            " WHERE status IN ('QUEUED', 'RUNNING')",
            (_NOW(),),
        )
