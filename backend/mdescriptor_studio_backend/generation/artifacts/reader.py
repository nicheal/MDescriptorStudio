"""Reading generation artifacts back (results view, export, materialize)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ...errors import ARTIFACT_INVALID, AppError
from ...security import ensure_no_reparse_points


class GenerationArtifactReader:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        if not self.root.is_dir():
            raise AppError(ARTIFACT_INVALID, "generation artifact is missing")
        ensure_no_reparse_points(self.root)

    def metadata(self) -> dict:
        path = self.root / "metadata.json"
        if not path.is_file():
            raise AppError(ARTIFACT_INVALID, "generation metadata is missing")
        return json.loads(path.read_text(encoding="utf-8"))

    def convergence(self) -> dict:
        path = self.root / "convergence.json"
        if not path.is_file():
            return {"rounds": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def accepted_extxyz(self) -> Path:
        path = self.root / "accepted.extxyz"
        if not path.is_file():
            raise AppError(ARTIFACT_INVALID, "accepted.extxyz is missing")
        return path

    def evaluated_extxyz(self) -> Path:
        path = self.root / "evaluated.extxyz"
        if not path.is_file():
            raise AppError(ARTIFACT_INVALID, "evaluated.extxyz is missing")
        return path

    def array(self, name: str, *, mmap: bool = False) -> np.ndarray:
        """One named array (fitness, novelty, structure_descriptors, ...)."""
        if not name.replace("_", "").isalnum():
            raise AppError(ARTIFACT_INVALID, f"invalid array name: {name}")
        path = self.root / f"{name}.npy"
        if not path.is_file():
            raise AppError(ARTIFACT_INVALID, f"generation array is missing: {name}")
        return np.load(path, allow_pickle=False, mmap_mode="r" if mmap else None)

    def candidates(self) -> list[dict]:
        path = self.root / "candidates.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
