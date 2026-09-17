"""Factory registry for dataset formats.

Factories receive a :class:`pathlib.Path` and return a reader. The registry is
the only format switch in the dataset layer; detection remains a separate
heuristic in :mod:`datasets.base`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..base import DatasetAdapter, detect_format
from ...errors import AppError, UNSUPPORTED_FORMAT

ReaderFactory = Callable[[Path], DatasetAdapter]

_READERS: dict[str, ReaderFactory] = {}


def register_reader(name: str, factory: ReaderFactory) -> None:
    key = str(name).strip().lower()
    if not key:
        raise ValueError("dataset reader name must be non-empty")
    if key in _READERS:
        raise ValueError(f"dataset reader {key!r} is already registered")
    _READERS[key] = factory


def reader_formats() -> tuple[str, ...]:
    return tuple(sorted(_READERS))


def create_reader(path: Path, fmt: str | None = None) -> DatasetAdapter:
    path = Path(path)
    key = str(fmt or detect_format(path)).strip().lower()
    factory = _READERS.get(key)
    if factory is None:
        raise AppError(UNSUPPORTED_FORMAT, f"unknown format {key!r}")
    return factory(path)


__all__ = ["ReaderFactory", "create_reader", "reader_formats", "register_reader"]
