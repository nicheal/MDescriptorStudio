"""Physical constraints: cheap geometry filters that run before descriptor evaluation."""

from .base import GeometryConstraint
from .geometry import GeometryConstraints, build_constraints

__all__ = ["GeometryConstraint", "GeometryConstraints", "build_constraints"]
