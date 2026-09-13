"""stdio NDJSON server: concurrent requests (id-matched), events interleaved."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import frames
from ..errors import INVALID_PARAMS, AppError

log = logging.getLogger(__name__)


class Server:
    """Reads request lines from stdin, dispatches on a thread pool, writes
    responses/events to stdout under a lock."""

    # Methods that must execute even when the general request queue is jammed:
    # during a long native compute the RPC workers crawl, the 64 general slots
    # fill with status polls, and without a reserved lane a job.cancel would
    # sit behind them — the user could not stop the very jobs causing the jam.
    # job.get/job.list belong here too: watchJob polls job.get every 500ms, so
    # a jam otherwise also freezes progress reporting and makes running jobs
    # look hung. All three are fast DB reads/row updates.
    CONTROL_METHODS = frozenset({"job.cancel", "job.get", "job.list"})

    def __init__(self, methods: dict):
        self.methods = methods
        # Set once the background warmup thread has finished its imports; the
        # polled input loop then hands over to the low-latency blocking loop.
        self.warmup_finished: threading.Event | None = None
        self._write_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rpc")
        self._closed = threading.Event()
        self._slots = threading.BoundedSemaphore(64)
        self._control_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="control")
        self._control_slots = threading.BoundedSemaphore(8)

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
        stdin_available = self._stdin_available_probe()
        if stdin_available is None:
            self._serve_blocking(stream)
            return
        mode = self._serve_polled(stdin_available)
        if mode == "warm" and not self._closed.is_set():
            # Warmup imports are done: no DLL-loading import can race a
            # blocking read anymore, so return to the historical loop for
            # zero-latency and zero idle CPU.
            self._serve_blocking(stream)
            return
        self.close()

    def _serve_blocking(self, stream) -> None:
        """Historical loop: one blocking readline per frame.

        Used on POSIX, for Windows console stdin, and after the background
        warmup has finished on Windows pipes.
        """
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
            if not self._consume_frame(raw):
                break
        self.close()

    def _serve_polled(self, stdin_available) -> str:
        """Polling loop for Windows pipe stdin: no ReadFile left in flight.

        While the background warmup imports the numeric stack, a blocking
        stdin read that is in flight deadlocks the Windows DLL loader (the
        importing thread hangs inside a C-extension create_module; see
        _stdin_available_probe). Frames are therefore assembled from raw
        os.read chunks sized by PeekNamedPipe, bypassing Python's buffered
        stdin so that prefetched lines can never strand behind an empty OS
        queue. Returns "eof" when stdin closed, "warm" once the warmup
        finished and blocking reads became safe again.
        """
        pending = b""
        while not self._closed.is_set():
            if self.warmup_finished is not None and self.warmup_finished.is_set() and not pending:
                return "warm"
            if not stdin_available():
                time.sleep(0.001)
                continue
            try:
                chunk = os.read(0, 65536)
            except OSError:
                return "eof"
            if not chunk:
                return "eof"
            pending += chunk
            while b"\n" in pending and not self._closed.is_set():
                raw, pending = pending.split(b"\n", 1)
                raw += b"\n"
                if not self._consume_frame(raw):
                    return "eof"
        return "eof"

    def _consume_frame(self, raw) -> bool:
        """Process one complete frame; False means the input loop must stop."""
        oversized = len(raw) - 1 > frames.MAX_LINE_BYTES
        if oversized:
            error = AppError(
                INVALID_PARAMS,
                "frame exceeds 8 MB limit",
                public_message="Request is too large.",
            )
            self._write(frames.response_err(None, error))
            return True
        line = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, bytes) else raw.strip()
        if not line:
            return True
        try:
            vid, method, params = frames.parse_request(line)
        except AppError as exc:
            self._write(frames.response_err(None, exc))
            return True
        if method in self.CONTROL_METHODS:
            return self._dispatch(vid, method, params, self._control_pool, self._control_slots)
        return self._dispatch(vid, method, params, self._pool, self._slots)

    def _dispatch(self, vid, method, params, pool: ThreadPoolExecutor, slots: threading.BoundedSemaphore) -> bool:
        if not slots.acquire(blocking=False):
            error = AppError("BUSY", "request queue is full", public_message="Backend is busy; try again shortly.")
            self._write(frames.response_err(vid, error))
            return True
        try:
            pool.submit(self._handle, vid, method, params, slots)
        except RuntimeError:
            slots.release()
            return False
        return True

    def _handle(self, vid, method: str, params, slots: threading.BoundedSemaphore) -> None:
        try:
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
        finally:
            slots.release()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._pool.shutdown(wait=True, cancel_futures=True)
        self._control_pool.shutdown(wait=True, cancel_futures=True)

    def _stdin_available_probe(self):
        """Non-blocking stdin availability probe for Windows pipe stdin, or None.

        On Windows, a blocking stdin ReadFile that is in flight while another
        thread imports the numeric stack (sklearn/hdbscan loading their DLLs
        after the engine's torch DLLs are resident) deadlocks the DLL loader:
        the importing thread hangs forever inside a C-extension create_module.
        The warmup thread therefore runs against a polling loop, which keeps
        no ReadFile in flight between frames. Returns None (blocking read
        fallback) for console stdin and non-Windows platforms, where the
        pathology does not apply.
        """
        if sys.platform != "win32":
            return None
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
            if not handle or handle == -1 or handle == 0xFFFFFFFFFFFFFFFF:
                return None
            peek = kernel32.PeekNamedPipe
            peek.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_void_p,
            ]
            peek.restype = ctypes.c_int

            def available() -> bool:
                total = ctypes.c_uint32(0)
                if not peek(handle, None, 0, None, ctypes.byref(total), None):
                    # Broken pipe: let os.read observe EOF.
                    return True
                return total.value > 0

            # Probe once so a non-pipe stdin (console) falls back to the
            # historical blocking read instead of spinning.
            total = ctypes.c_uint32(0)
            if not peek(handle, None, 0, None, ctypes.byref(total), None):
                return None
            return available
        except Exception:  # noqa: BLE001 - fall back to blocking read
            return None
