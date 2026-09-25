"""Immutable context handed to an optimizer exactly once, before round 1.

The optimizer owns *how* candidates are proposed; everything that defines the
proposal environment lives here so optimizers never need a back-reference to
the engine (Generator ≠ Objective ≠ Constraint ≠ Descriptor).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Budget


@dataclass(frozen=True)
class OptimizationContext:
    """The fixed proposal environment for one generation run."""

    # StructureCandidate seeds drawn from the source dataset.
    seed_pool: tuple
    # Seeds drawn per round (engine-level knob, not optimizer-specific).
    n_seeds: int
    # The run budget; optimizers that self-limit read it, the engine enforces it.
    budget: Budget = field(repr=False, default=None)
