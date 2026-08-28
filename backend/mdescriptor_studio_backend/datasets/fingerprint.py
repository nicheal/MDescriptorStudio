"""Dataset fingerprint: SHA-256 over path, file list, sizes, mtimes, frames (§13)."""

from __future__ import annotations

import hashlib
from pathlib import Path


def compute_fingerprint(source_path: Path, number_of_frames: int | None = None) -> str:
    path = Path(source_path)
    h = hashlib.sha256()
    h.update(str(path).encode("utf-8"))
    files = sorted(path.rglob("*")) if path.is_dir() else [path]
    for f in files:
        if f.is_file():
            stat = f.stat()
            h.update(f.relative_to(path if path.is_dir() else f.parent).as_posix().encode())
            h.update(str(stat.st_size).encode())
            h.update(str(stat.st_mtime_ns).encode())
    if number_of_frames is not None:
        h.update(str(number_of_frames).encode())
    return h.hexdigest()
