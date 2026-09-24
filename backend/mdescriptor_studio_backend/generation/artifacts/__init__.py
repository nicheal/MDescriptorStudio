"""Generation artifact contract: on-disk layout, atomic publish, reading."""

from .reader import GenerationArtifactReader
from .writer import GenerationArtifactWriter

__all__ = ["GenerationArtifactReader", "GenerationArtifactWriter"]
