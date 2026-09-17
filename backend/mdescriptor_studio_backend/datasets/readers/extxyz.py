"""extxyz reader registration."""

from __future__ import annotations

from pathlib import Path

from ..extxyz import ExtXYZAdapter


def create(path: Path) -> ExtXYZAdapter:
    return ExtXYZAdapter(path)


__all__ = ["ExtXYZAdapter", "create"]
