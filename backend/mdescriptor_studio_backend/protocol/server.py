"""stdio NDJSON server: concurrent requests (id-matched), events interleaved."""

from __future__ import annotations

import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from . import frames
from ..errors import INVALID_PARAMS, AppError

log = logging.getLogger(__name__)


class Server:
    """Reads request lines from stdin, dispatches on a thread pool, writes
    responses/events to stdout under a lock."""

    def __init__(self, methods: dict, on_stop=None):
        self.methods = methods
        self.on_stop = on_stop
        self._write_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rpc")
        self._closed = threading.Event()
        self._slots = threading.BoundedSemaphore(64)

    # -- output ------------------------------------------------------------
    def emit(self, name: str, data: dict) -> None:
        self._write(frames.event_frame(name, data))

    def _write(self, frame: dict) -> None:
        line = frames.encode(frame)
        with self._write_lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    # -- input loop ----------------------------------------------------------
    def serve_forever(self) -> None:
        log.info("protocol server started (v%s)", frames.PROTOCOL_VERSION)
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        while not self._closed.is_set():
            raw = stream.readline(frames.MAX_LINE_BYTES + 1)
            if not raw:
                break
            oversized = len(raw) > frames.MAX_LINE_BYTES
            newline = b"\n" if isinstance(raw, bytes) else "\n"
            while oversized and raw and not raw.endswith(newline):
                raw = stream.readline(frames.MAX_LINE_BYTES + 1)
            if oversized:
                error = AppError(
                    INVALID_PARAMS,
                    "frame exceeds 8 MB limit",
                    public_message="Request is too large.",
                )
                self._write(frames.response_err(None, error))
                continue
            if isinstance(raw, bytes):
                line = raw.decode("utf-8", errors="replace").strip()
            else:
                line = raw.strip()
            if not line:
                continue
            if not self._slots.acquire(blocking=False):
                try:
                    vid, _method, _params = frames.parse_request(line)
                    error = AppError("BUSY", "request queue is full", public_message="Backend is busy; try again shortly.")
                except AppError as exc:
                    vid, error = None, exc
                self._write(frames.response_err(vid, error))
                continue
            try:
                self._pool.submit(self._handle_with_slot, line)
            except RuntimeError:
                self._slots.release()
                break
        self.close()

    def _handle_with_slot(self, line: str) -> None:
        try:
            self._handle(line)
        finally:
            self._slots.release()

    def _handle(self, line: str) -> None:
        try:
            vid, method, params = frames.parse_request(line)
        except AppError as exc:
            self._write(frames.response_err(None, exc))
            return
        handler = self.methods.get(method)
        if handler is None:
            self._write(
                frames.response_err(vid, AppError(INVALID_PARAMS, f"unknown method {method!r}"))
            )
            return
        try:
            result = handler(params)
            self._write(frames.response_ok(vid, result))
        except AppError as exc:
            self._write(frames.response_err(vid, exc))
        except Exception as exc:  # noqa: BLE001 - top-level guard
            log.exception("unhandled error in %s", method)
            self._write(
                frames.response_err(vid, AppError("INTERNAL_ERROR", f"{type(exc).__name__}: {exc}"))
            )

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._pool.shutdown(wait=True, cancel_futures=True)
        if self.on_stop is not None:
            self.on_stop()
