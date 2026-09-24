"""Structure-level novelty: distance to the nearest archived structure.

    N(X) = min_{Y in A} ||z̃(X) - z̃(Y)||_2

Distances are measured in the archive's frozen scaled space (robust scaling
by default, fitted once on the reference set), so fitness scores stay
comparable across generations.
"""

from __future__ import annotations

import numpy as np

from .base import ObjectiveBatchResult


class NoveltyObjective:
    name = "novelty"
    needs_atomic = False

    def evaluate_batch(self, structure_values, atomic_values, row_offsets, structure_archive, local_archive, penalties):
        values = np.asarray(structure_values, dtype=np.float64)
        novelty = structure_archive.nearest(values)
        fitness = novelty - np.asarray(penalties, dtype=np.float64)
        return ObjectiveBatchResult(
            novelty=novelty,
            local_diversity=None,
            novel_environment_count=None,
            fitness=fitness,
            components={"novelty": novelty},
        )
