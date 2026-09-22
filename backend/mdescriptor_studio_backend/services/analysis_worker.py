"""Process entry point for analysis calls that cannot poll cancellation.

The worker deliberately receives already-loaded descriptor matrices. Database
access, artifact writes, and job state stay in the parent process; terminating
this process therefore cannot leave a half-written analysis artifact behind.
"""

from __future__ import annotations

import pickle
import sys

from ..analysis import ANALYSIS_REGISTRY
from ..errors import AppError


def run_analysis_subprocess() -> None:
    """Read one pickled request from stdin and write one result to stdout.

    This entry point intentionally does not import or execute the backend
    protocol server. The parent can terminate the interpreter directly even
    when the backend already has warmup threads and native libraries loaded.
    """
    try:
        analysis_type, samples, params, thread_limit = pickle.load(sys.stdin.buffer)
        try:
            if thread_limit:
                from threadpoolctl import threadpool_limits

                with threadpool_limits(limits=thread_limit):
                    result = ANALYSIS_REGISTRY.run(analysis_type, samples, params, None)
            else:
                result = ANALYSIS_REGISTRY.run(analysis_type, samples, params, None)
            packet = ("ok", result)
        except AppError as exc:
            packet = ("app_error", exc.code, exc.message, exc.details, exc.public_message)
        except BaseException as exc:  # noqa: BLE001 - return through the worker boundary
            packet = ("error", type(exc).__name__, str(exc))
    except BaseException as exc:  # noqa: BLE001 - malformed worker input
        packet = ("error", type(exc).__name__, str(exc))
    pickle.dump(packet, sys.stdout.buffer, protocol=pickle.HIGHEST_PROTOCOL)
    sys.stdout.buffer.flush()


__all__ = ["run_analysis_subprocess"]
