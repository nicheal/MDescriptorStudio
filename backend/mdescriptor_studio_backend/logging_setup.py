"""Logging: backend.log under the data dir; stdout carries protocol frames only.

Anything written to stdout must be a protocol frame (docs/plan/02 §1), so the
StreamHandler goes to stderr at WARNING+.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import data_dir


def setup_logging() -> None:
    log_file = data_dir() / "logs" / "backend.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if root.handlers:  # idempotent for tests
        return
    fh = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.WARNING)
    sh.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root.addHandler(sh)
