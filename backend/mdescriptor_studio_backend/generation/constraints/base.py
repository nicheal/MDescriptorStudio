"""Constraint contract.

A constraint never mutates the candidate; it returns a verdict. All G1
constraints are hard filters (penalty stays 0) — soft penalties are reserved
for energetic screening in a later phase.
"""

from __future__ import annotations

from typing import Protocol

from ..models import ConstraintResult


class GeometryConstraint(Protocol):
    def validate(self, candidate) -> ConstraintResult:
        """Cheap checks that must run before any descriptor compute."""
        ...
