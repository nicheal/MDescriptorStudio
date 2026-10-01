"""Snapshot persistence primitives (audit R5.5).

Shared by the engine's snapshot commit protocol and the optimizers' strict
snapshot validation: durable file writes behind the manifest-last commit
order, and the required-field check that separates "field absent" (a
corrupt or hand-edited snapshot — always rejected) from "field null" (a
legal persisted value). Optimizer ``load_state`` must never fall back to
empty defaults for fields the engine always writes: a snapshot missing
``particles``/``pool``/``targeted_accepted`` would silently restore an
amnesiac optimizer whose trajectory diverges from the uninterrupted run.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


def require_keys(payload, keys: Iterable[str], what: str) -> None:
    """Reject a snapshot object that is missing required fields."""
    if not isinstance(payload, dict):
        raise ValueError(f"snapshot {what} must be a JSON object")
    missing = [key for key in keys if key not in payload]
    if missing:
        raise ValueError(f"snapshot {what} is missing required fields: {', '.join(missing)}")


def durable_write(path: Path, data: bytes) -> None:
    """Write bytes durably: temp file in the same directory, flush + fsync,
    then an atomic replace onto ``path``."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def fsync_directory(directory: Path) -> None:
    """Best-effort directory sync so a committed rename survives power loss.

    POSIX-only in practice; where the platform cannot open or fsync a
    directory (Windows), the commit stays ordered by the per-file fsyncs
    alone and this is a no-op.
    """
    try:
        handle = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)
