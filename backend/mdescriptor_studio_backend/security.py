"""Small, dependency-free guards for paths and persisted artifacts.

The desktop backend is local software, but the renderer and the persisted
SQLite rows are still untrusted input.  Keep all filesystem policy here so
the services do not grow subtly different path checks.
"""

from __future__ import annotations

import ntpath
import os
import re
import shutil
from pathlib import Path
from typing import TextIO


class UnsafePathError(ValueError):
    """A path is not safe for a local user-selected operation."""


_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")
_DRIVE_ONLY_RE = re.compile(r"^[A-Za-z]:$")
_RESERVED_RE = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE)


def _reject_windows_path_forms(raw: str, field: str) -> None:
    """Reject Windows namespace, UNC, ADS and ambiguous relative forms."""
    if any(ord(character) < 0x20 for character in raw):
        raise UnsafePathError(f"{field} contains a control character")
    normalized = raw.replace("/", "\\")
    if (
        normalized.startswith("\\\\")
        or normalized.startswith("\\\\?\\")
        or normalized.startswith("\\\\.\\")
    ):
        raise UnsafePathError(f"{field} must be a local path")
    drive, tail = ntpath.splitdrive(normalized)
    if drive and not _DRIVE_ONLY_RE.fullmatch(drive):
        raise UnsafePathError(f"{field} has an invalid drive prefix")
    if drive and not _DRIVE_RE.match(normalized):
        raise UnsafePathError(f"{field} must use an absolute drive path")
    if not drive and not Path(raw).is_absolute():
        raise UnsafePathError(f"{field} must be absolute")
    if ":" in tail:
        raise UnsafePathError(f"{field} cannot contain an alternate data stream")
    for component in tail.split("\\"):
        if not component:
            continue
        if component in (".", ".."):
            raise UnsafePathError(f"{field} cannot contain dot segments")
        if component.endswith((" ", ".")):
            raise UnsafePathError(f"{field} contains a Windows-ambiguous component")
        if _RESERVED_RE.fullmatch(component):
            raise UnsafePathError(f"{field} contains a reserved Windows name")


def is_reparse_point(path: Path) -> bool:
    """Return whether *path* is a symlink/junction/reparse point."""
    try:
        if path.is_symlink() or os.path.islink(os.fspath(path)):
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError as exc:
        raise UnsafePathError("cannot inspect a filesystem reparse point") from exc


def ensure_no_reparse_points(path: Path) -> None:
    """Check every existing component without following links."""
    path = Path(path)
    current = Path(path.anchor) if path.anchor else Path(".")
    for part in path.parts:
        if part == path.anchor:
            continue
        current = current / part
        junction = getattr(current, "is_junction", None)
        is_junction = bool(junction and junction())
        if (current.exists() or current.is_symlink() or is_junction) and is_reparse_point(current):
            raise UnsafePathError("path crosses a symlink or junction")


def validate_local_path(raw: object, *, field: str = "path") -> Path:
    """Validate and normalize an absolute local path.

    The target may not exist yet (export destinations are created later), but
    all existing ancestors must be ordinary directories/files, not reparse
    points.  The checks intentionally use Windows syntax even when a unit
    test constructs a Windows-looking input on another platform.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise UnsafePathError(f"{field} must be a non-empty string")
    value = raw.strip()
    _reject_windows_path_forms(value, field)
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise UnsafePathError(f"{field} must be absolute")
    try:
        ensure_no_reparse_points(path)
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise UnsafePathError(f"cannot resolve {field}") from exc


def same_lexical_path(left: Path, right: Path) -> bool:
    """Compare paths without resolving a final symlink/junction."""
    return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(
        os.path.abspath(os.fspath(right))
    )


def validate_managed_path(root: Path, stored: object, relative_name: str) -> Path:
    """Return a DB-stored artifact path only when it is the expected child."""
    if not isinstance(stored, str) or not stored.strip():
        raise UnsafePathError("stored artifact path is missing")
    expected = Path(root).resolve(strict=False) / relative_name
    actual = validate_local_path(stored, field="stored artifact path")
    if not same_lexical_path(actual, expected):
        raise UnsafePathError("stored artifact path is outside the managed directory")
    ensure_no_reparse_points(actual)
    return expected


def remove_managed_tree(path: Path) -> None:
    """Remove one already-validated managed path without following links."""
    path = Path(path)
    junction = getattr(path, "is_junction", None)
    is_junction = bool(junction and junction())
    if not path.exists() and not path.is_symlink() and not is_junction:
        return
    if is_reparse_point(path):
        path.unlink(missing_ok=True)
        return
    shutil.rmtree(path)


def open_text_for_write(path: Path, *, newline: str | None = None) -> TextIO:
    """Open a validated destination without following a final symlink."""
    path = Path(path)
    ensure_no_reparse_points(path.parent)
    if path.exists() or path.is_symlink():
        if is_reparse_point(path) or not path.is_file():
            raise UnsafePathError("output path is not a regular local file")
    flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow:
        flags |= no_follow
    binary = getattr(os, "O_BINARY", 0)
    if binary:
        flags |= binary
    fd = os.open(os.fspath(path), flags, 0o600)
    return os.fdopen(fd, "w", encoding="utf-8", newline=newline)


def escape_like(value: str) -> str:
    """Escape SQLite LIKE wildcards for literal identifier matching."""
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
