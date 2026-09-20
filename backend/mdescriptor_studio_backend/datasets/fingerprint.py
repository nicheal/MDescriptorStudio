"""Dataset fingerprints with bounded content sampling and legacy migration."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path

from ..security import ensure_no_reparse_points, is_reparse_point

# Bumped when reader semantics change in a way that alters stored results, so
# every existing fingerprint mismatches and its runs go STALE. v3: extxyz stops
# inventing periodicity for a Lattice that declares pbc="F F F". v4: extxyz reads
# every true/false spelling a writer emits instead of only a bare "T", so a file
# that said pbc="True True True" stops being reinterpreted as an isolated cluster
# with a zeroed cell - the stored statistics, health findings and descriptors for
# such sources were computed on the wrong structure.
FINGERPRINT_VERSION = "v4"
MAX_FINGERPRINT_FILES = 100_000
MAX_FINGERPRINT_BYTES = 4 * 1024 * 1024 * 1024
SAMPLE_CHUNK_BYTES = 1 * 1024 * 1024
SAMPLE_BUDGET_BYTES = 32 * 1024 * 1024
FINGERPRINT_CACHE_TTL_SECONDS = 2.0

_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[str, int, int, int | None], tuple[float, str]] = {}


def _cache_key(path: Path, number_of_frames: int | None) -> tuple[str, int, int, int | None]:
    resolved = path.resolve(strict=False)
    stat = resolved.stat()
    return (
        str(resolved),
        int(stat.st_mtime_ns),
        int(stat.st_size),
        number_of_frames,
    )


def _files(path: Path) -> list[Path]:
    if not path.is_dir():
        if is_reparse_point(path) or not path.is_file():
            raise ValueError("dataset fingerprint requires a regular local file")
        return [path]

    # Walk incrementally. ``sorted(path.rglob('*'))`` materializes every entry
    # before the file-count cap can run, allowing a directory full of junk
    # entries to exhaust memory before fingerprinting starts.
    def raise_walk_error(error: OSError) -> None:
        raise ValueError("dataset directory cannot be enumerated safely") from error

    files: list[Path] = []
    total = 0
    for directory, dirnames, filenames in os.walk(
        path,
        topdown=True,
        onerror=raise_walk_error,
        followlinks=False,
    ):
        dirnames.sort()
        filenames.sort()
        directory_path = Path(directory)
        for dirname in dirnames:
            child = directory_path / dirname
            if is_reparse_point(child):
                raise ValueError("dataset fingerprint cannot cross a symbolic link or junction")
        for filename in filenames:
            item = directory_path / filename
            if is_reparse_point(item) or not item.is_file():
                raise ValueError("dataset fingerprint requires regular local files")
            files.append(item)
            if len(files) > MAX_FINGERPRINT_FILES:
                raise ValueError("dataset contains too many files to fingerprint")
            total += item.stat().st_size
            if total > MAX_FINGERPRINT_BYTES:
                raise ValueError("dataset is too large to fingerprint safely")
    return files


def _sample_digest(path: Path, size: int, budget: list[int]) -> bytes:
    if budget[0] <= 0:
        return b"sample-skipped"
    with path.open("rb") as stream:
        if size <= SAMPLE_CHUNK_BYTES * 3 and size <= budget[0]:
            data = stream.read(size)
            budget[0] -= len(data)
            return hashlib.sha256(b"full\0" + data).digest()
        offsets = sorted({0, max(0, size // 2 - SAMPLE_CHUNK_BYTES // 2), max(0, size - SAMPLE_CHUNK_BYTES)})
        chunks = []
        for offset in offsets:
            if budget[0] <= 0:
                break
            stream.seek(offset, os.SEEK_SET)
            data = stream.read(min(SAMPLE_CHUNK_BYTES, budget[0]))
            budget[0] -= len(data)
            chunks.append(offset.to_bytes(8, "little") + len(data).to_bytes(8, "little") + data)
        return hashlib.sha256(b"sample\0" + b"".join(chunks)).digest()


def compute_legacy_fingerprint(source_path: Path, number_of_frames: int | None = None) -> str:
    """Reproduce the pre-versioning metadata-only fingerprint for migration."""
    path = Path(source_path)
    ensure_no_reparse_points(path)
    h = hashlib.sha256()
    h.update(str(path).encode("utf-8"))
    for item in _files(path):
        stat = item.stat()
        h.update(item.relative_to(path if path.is_dir() else item.parent).as_posix().encode())
        h.update(str(stat.st_size).encode())
        h.update(str(stat.st_mtime_ns).encode())
    if number_of_frames is not None:
        h.update(str(number_of_frames).encode())
    return h.hexdigest()


def is_versioned_fingerprint(value: object) -> bool:
    """True for any content-derived fingerprint (``v<digits>:<digest>``).

    The pre-versioning legacy form is a bare hexdigest, so an *older* version
    still counts: its rows go stale on the fingerprint comparison rather than
    through the migration path, which cannot recognise them.
    """
    if not isinstance(value, str):
        return False
    head, sep, _ = value.partition(":")
    return bool(sep) and head.startswith("v") and head[1:].isdigit()


def compute_fingerprint(
    source_path: Path,
    number_of_frames: int | None = None,
    *,
    use_cache: bool = True,
) -> str:
    path = Path(source_path)
    ensure_no_reparse_points(path)
    key = _cache_key(path, number_of_frames)
    now = time.monotonic()
    if use_cache:
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached is not None and now - cached[0] < FINGERPRINT_CACHE_TTL_SECONDS:
                return cached[1]

    h = hashlib.sha256()
    h.update(FINGERPRINT_VERSION.encode("ascii") + b"\0")
    h.update(str(path.resolve(strict=False)).encode("utf-8"))
    budget = [SAMPLE_BUDGET_BYTES]
    base = path if path.is_dir() else path.parent
    for item in _files(path):
        stat = item.stat()
        relative = item.relative_to(base).as_posix()
        h.update(relative.encode("utf-8") + b"\0")
        h.update(str(stat.st_size).encode("ascii") + b"\0")
        h.update(str(stat.st_mtime_ns).encode("ascii") + b"\0")
        h.update(_sample_digest(item, stat.st_size, budget))
    if number_of_frames is not None:
        h.update(b"frames\0" + str(number_of_frames).encode("ascii"))
    fingerprint = FINGERPRINT_VERSION + ":" + h.hexdigest()
    if use_cache:
        with _CACHE_LOCK:
            _CACHE[key] = (now, fingerprint)
            # Keep the cache bounded even when a long-lived process scans many
            # user-selected sources.
            if len(_CACHE) > 256:
                oldest = min(_CACHE, key=lambda item: _CACHE[item][0])
                _CACHE.pop(oldest, None)
    return fingerprint
