"""Data directory layout: %LOCALAPPDATA%\\MDescriptorStudio (design doc §46)."""

from __future__ import annotations

import os
import logging
import stat
from pathlib import Path

from .security import UnsafePathError, ensure_no_reparse_points, validate_local_path

APP_DIR_NAME = "MDescriptorStudio"
PROTOCOL_VERSION = 1
log = logging.getLogger(__name__)


def _warn_if_shared(path: Path) -> None:
    # POSIX exposes this cheaply; Windows ACLs are intentionally not guessed
    # from mode bits. The warning keeps a shared override visible without
    # breaking legitimate installations that rely on inherited ACLs.
    if os.name != "nt":
        try:
            if path.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                log.warning("data directory is writable by another local account: %s", path)
        except OSError:
            pass


def data_dir() -> Path:
    override = os.environ.get("MDS_DATA_DIR")
    if override:
        raw_root = override
    else:
        local = os.environ.get("LOCALAPPDATA")
        raw_root = str((Path(local) if local else Path.home()) / APP_DIR_NAME)
    try:
        root = validate_local_path(raw_root, field="MDS_DATA_DIR")
    except UnsafePathError as exc:
        raise RuntimeError("MDS_DATA_DIR must be an absolute local directory") from exc
    root.mkdir(parents=True, exist_ok=True)
    ensure_no_reparse_points(root)
    _warn_if_shared(root)
    for sub in ("logs", "results", "analysis", "cache"):
        child = root / sub
        child.mkdir(parents=True, exist_ok=True)
        ensure_no_reparse_points(child)
        _warn_if_shared(child)
    return root
