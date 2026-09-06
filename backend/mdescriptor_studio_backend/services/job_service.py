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

# Pool sizes per job category. The engine pool is deliberately size 1:
# descriptor compute is the heaviest work in the process, and engine.update's
# pip install must never run while a compute holds the engine's native
# extensions open (Windows locks loaded DLLs) — sharing one single-worker
# pool serializes exactly those two without any extra locking.
_POOL_SIZES = {"engine": 1, "analysis": 2, "dataset": 2}


def _category(job_type: str) -> str:
    if job_type.startswith(("descriptor.", "engine.")):
        return "engine"
    if job_type.startswith("analysis."):
        return "analysis"
    return "dataset"


class JobContext:
    def __init__(self, service: "JobService", job_id: str):
        self.service = service
        self.job_id = job_id
        self.control = None  # engine ComputeControl, attached by compute jobs
        self._cancelled = threading.Event()
        # cancel() settles the job rows immediately; a runner stuck in a long
        # native call (t-SNE fit, SVD) keeps the thread alive until it returns,
        # so a detached context must not write progress or resurrect the rows.
        self.detached = False
        self._last_emit = 0.0
        self._last_fraction = -1.0
        self._last_message = None

    def detach(self) -> None:
        self.detached = True

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
        if self.detached:
            return
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
        self._pools = {
            name: ThreadPoolExecutor(max_workers=size, thread_name_prefix=f"job-{name}")
            for name, size in _POOL_SIZES.items()
        }
        self._contexts: dict[str, JobContext] = {}
        self._lock = threading.Lock()
        self._queue_slots = threading.BoundedSemaphore(64)
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
        if not self._queue_slots.acquire(blocking=False):
            raise AppError("BUSY", "job queue is full", public_message="Backend is busy; try again shortly.")
        inserted = False
        try:
            self.db.execute(
                "INSERT INTO jobs (id, job_type, dataset_id, descriptor_run_id, analysis_run_id, status, progress, created_at)"
                " VALUES (?, ?, ?, ?, ?, 'QUEUED', 0, ?)",
                (job_id, job_type, dataset_id, descriptor_run_id, analysis_run_id, _NOW()),
            )
            inserted = True
            with self._lock:
                self._contexts[job_id] = JobContext(self, job_id)
            self._pools[_category(job_type)].submit(self._run, job_id, job_type, runner)
            return job_id
        except Exception:
            with self._lock:
                self._contexts.pop(job_id, None)
            if inserted:
                self.db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            self._queue_slots.release()
            raise

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
        try:
            with self._lock:
                ctx = self._contexts.get(job_id)
            if ctx is None:
                # cancelled while queued: cancel() settled the rows already
                log.info("job %s (%s) skipped: cancelled before start", job_id, job_type)
                return
            if not (ctx.detached or ctx._cancelled.is_set()):
                # The status guard keeps a cancel() that landed between the
                # context lookup above and this write from being overwritten
                # (CANCELLED -> RUNNING resurrect + duplicate job.finished).
                self.db.execute(
                    "UPDATE jobs SET status = 'RUNNING', started_at = ? WHERE id = ? AND status = 'QUEUED'",
                    (_NOW(), job_id),
                )
                log.info("job %s (%s) started", job_id, job_type)
            ctx.check_cancelled()
            result = runner(ctx)
            self._finish(job_id, "COMPLETED", error=None, result=result)
        except AppError as exc:
            status = "CANCELLED" if exc.code == JOB_CANCELLED else "FAILED"
            self._finish(job_id, status, error={"code": exc.code, "message": exc.public_message, "error_id": exc.error_id})
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s crashed", job_id)
            self._finish(job_id, "FAILED", error={"code": "INTERNAL_ERROR", "message": "The backend failed to complete the job."})
        finally:
            self._queue_slots.release()

    def _finish(self, job_id: str, status: str, error, result=None) -> None:
        self._finalize(job_id, status, error, result)
        with self._lock:
            self._contexts.pop(job_id, None)

    def _finalize(self, job_id: str, status: str, error, result=None) -> bool:
        """Settle a job row exactly once — the WHERE guard makes the first
        finalization win. cancel() finalizes up front (the runner may be stuck
        in a native call with no checkpoint for minutes), so a detached
        runner's eventual COMPLETED/CANCELLED must neither overwrite nor
        re-emit the settled state."""
        changed = self.db.execute(
            "UPDATE jobs SET status = ?, finished_at = ?, error = ? WHERE id = ? AND status IN ('QUEUED', 'RUNNING')",
            (status, _NOW(), error["code"] if error else None, job_id),
        )
        if not changed:
            return False
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
        log.info("job %s -> %s", job_id, status)
        return True

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
        # Long native calls (t-SNE fit, SVD, umap) only hit the cooperative
        # checkpoint when they return, so waiting for the runner to notice the
        # flag looks like "stop does nothing" for minutes. Settle the job now:
        # rows flip to CANCELLED, job.finished is emitted, and the detached
        # runner's eventual output is discarded (progress no-ops, its next
        # check_cancelled raises before any commit).
        ctx.detach()
        with self._lock:
            self._contexts.pop(job_id, None)
        self._finalize(job_id, "CANCELLED", error={"code": JOB_CANCELLED, "message": "The job was cancelled."})
        return {"ok": True, "already_finished": False}

    def _queue_positions(self) -> dict[str, int]:
        """1-based position of every QUEUED job within its category pool.

        Pool assignment is derived from job_type, so the number reflects the
        pool the job is actually waiting on. Cheap enough for per-poll use:
        submit backpressure caps QUEUED jobs far below this table's size.
        """
        rows = self.db.query(
            # rowid = insertion order: created_at has second precision, so
            # same-second submissions would otherwise tie-break on the random
            # id instead of the actual pool queue order
            "SELECT id, job_type FROM jobs WHERE status = 'QUEUED' ORDER BY created_at, rowid"
        )
        per_category: dict[str, int] = {}
        positions: dict[str, int] = {}
        for row in rows:
            category = _category(row["job_type"])
            per_category[category] = per_category.get(category, 0) + 1
            positions[row["id"]] = per_category[category]
        return positions

    def get_job(self, job_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if row is not None and row["status"] == "QUEUED":
            row["queue_position"] = self._queue_positions().get(job_id)
        return row

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
        rows = self.db.query(sql, tuple(args))
        positions = self._queue_positions()
        for row in rows:
            if row["status"] == "QUEUED" and row["id"] in positions:
                row["queue_position"] = positions[row["id"]]
        return rows

    def shutdown(self, wait_seconds: float = 3.0) -> None:
        # Cooperatively cancel live jobs first: cancel() settles the job rows
        # AND its linked run rows, detaches the runner, and (for compute jobs)
        # cancels the engine ComputeControl so the native call unwinds at its
        # next checkpoint instead of blocking this shutdown until it returns.
        with self._lock:
            job_ids = list(self._contexts)
        for job_id in job_ids:
            try:
                self.cancel(job_id)
            except Exception:  # noqa: BLE001 - shutdown must settle everything
                log.exception("cancel during shutdown failed for job %s", job_id)
        for pool in self._pools.values():
            pool.shutdown(wait=False, cancel_futures=True)
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
