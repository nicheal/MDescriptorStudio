"""Serializable metadata contracts for Analysis artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ArtifactManifest:
    analysis_id: str
    analysis_type: str
    input_run_ids: list[str]
    algorithm_version: str
    schema_version: int = 1
    completed: bool = False
    files: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "analysis_type": self.analysis_type,
            "input_run_ids": self.input_run_ids,
            "algorithm_version": self.algorithm_version,
            "schema_version": self.schema_version,
            "completed": self.completed,
            "files": self.files,
        }


@dataclass
class AnalysisResult:
    """The common result envelope persisted by the Studio backend.

    Numerical arrays are intentionally kept out of this object; they are
    named files described by the artifact manifest and addressed through the
    chunk API.
    """

    analysis_id: str
    analysis_type: str
    input_run_ids: list[str]
    parameters: dict[str, Any]
    algorithm_version: str
    warnings: list[str] = field(default_factory=list)
    preview: dict[str, Any] = field(default_factory=dict)
    artifact_manifest: ArtifactManifest | None = None

    def to_metadata(self) -> dict[str, Any]:
        return {
            "analysis_id": self.analysis_id,
            "analysis_type": self.analysis_type,
            "input_run_ids": self.input_run_ids,
            "parameters": self.parameters,
            "algorithm_version": self.algorithm_version,
            "warnings": self.warnings,
            "preview": self.preview,
        }
