"""Operator contract: one perturbation type, applied to a parent structure.

An operator never mutates its input frame; it returns a new one. Keeping
every operator behind this one protocol is what lets random search, the
future genetic algorithm and external generators share the same search
space without each re-implementing perturbation math.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np


class StructureOperator(Protocol):
    name: str

    def apply(self, parent, rng: np.random.Generator, params: dict):
        """Return a new frame/candidate derived from ``parent``.

        ``parent`` is a datasets.base.DatasetFrame in G0; generation
        candidates (generation.models.StructureCandidate) arrive with G1.
        ``params`` carries operator-specific bounds (e.g. max displacement).
        """
        ...
