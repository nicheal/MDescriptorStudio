"""DeepMD reader registration."""

from __future__ import annotations

from pathlib import Path

from ..deepmd import DeepMDAdapter


def create(path: Path) -> DeepMDAdapter:
    return DeepMDAdapter(path)


__all__ = ["DeepMDAdapter", "create"]
