"""Immutable context handed to an optimizer exactly once, before round 1.

The optimizer owns *how* candidates are proposed; everything that defines the
proposal environment lives here so optimizers never need a back-reference to
the engine (Generator ≠ Objective ≠ Constraint ≠ Descriptor).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..models import Budget


@dataclass(frozen=True)
class OptimizationContext:
    """The fixed proposal environment for one generation run.

    Descriptor rows are *scaled* structure descriptors (apply_scaling with the
    structure archive's transform) — the same space observations report, so
    optimizers can measure distances against them without reaching into the
    archive. They are free to hand out: every dataset frame's descriptor is
    already a row of the frozen reference the archive was built from.
    """

    # StructureCandidate seeds drawn from the source dataset.
    seed_pool: tuple
    # Seeds drawn per round (engine-level knob, not optimizer-specific).
    n_seeds: int
    # The run budget; optimizers that self-limit read it, the engine enforces it.
    budget: Budget = field(repr=False, default=None)
    # Scaled structure descriptor per seed_pool entry (aligned by index; None
    # where the seed's dataset frame has no reference row).
    seed_descriptors: tuple = ()
    # Scaled structure descriptors of user-designated anchor frames — the
    # centers of a target region. Empty when the user set no search target.
    anchor_descriptors: tuple = ()
    # Target-region width in robust-scaled descriptor units; meaningful only
    # together with anchor_descriptors.
    region_radius: float | None = None
