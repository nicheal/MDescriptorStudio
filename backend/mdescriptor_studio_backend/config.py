"""Data directory layout: %LOCALAPPDATA%\\MDescriptorStudio (design doc §46)."""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "MDescriptorStudio"
PROTOCOL_VERSION = 1


def data_dir() -> Path:
    override = os.environ.get("MDS_DATA_DIR")
    if override:
        root = Path(override)
    else:
        local = os.environ.get("LOCALAPPDATA")
        root = (Path(local) if local else Path.home()) / APP_DIR_NAME
    for sub in ("logs", "results", "analysis", "cache"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root
