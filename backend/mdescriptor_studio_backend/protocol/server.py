"""stdio NDJSON server: concurrent requests (id-matched), events interleaved."""

from __future__ import annotations

import logging
import os
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
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            if self._closed.is_set():
                break
            self._pool.submit(self._handle, line)
        self.close()

    def _handle(self, line: str) -> None:
        try:
            vid, method, params = frames.parse_request(line)
        except AppError as exc:
            self._write(frames.response_err(None, exc))
            if exc.code == "PROTOCOL_VERSION_MISMATCH":
                # docs/plan/02 §3: incompatible client -> error frame + exit 2.
                # os._exit: sys.exit is a no-op inside a pool worker thread.
                log.error("protocol version mismatch, exiting")
                os._exit(2)
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
