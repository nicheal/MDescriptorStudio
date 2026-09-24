"""Composite objective: local-environment novelty + structure novelty.

    F = w_l · F_local + w_s · N_structure − penalty

Default preset weights (local 0.7 / structure 0.3) reflect that for ML
potential datasets the local environment is where generalisation fails;
they are a preset, not the only scientific choice — both weights are
user parameters.
"""

from __future__ import annotations

import numpy as np

from .base import ObjectiveBatchResult
from .local_diversity import LocalEnvironmentNoveltyObjective
from .novelty import NoveltyObjective


class CompositeObjective:
    name = "composite"
    needs_atomic = True

    def __init__(
        self,
        structure_weight: float = 0.3,
        local_weight: float = 0.7,
        **local_params,
    ) -> None:
        if structure_weight < 0 or local_weight < 0 or structure_weight + local_weight <= 0:
            raise ValueError("composite weights must be non-negative with a positive sum")
        self.structure_weight = float(structure_weight)
        self.local_weight = float(local_weight)
        self._structure = NoveltyObjective()
        self._local = LocalEnvironmentNoveltyObjective(**local_params)

    def evaluate_batch(self, structure_values, atomic_values, row_offsets, structure_archive, local_archive, penalties):
        structure = self._structure.evaluate_batch(
            structure_values, atomic_values, row_offsets, structure_archive, local_archive, penalties
        )
        local = self._local.evaluate_batch(
            structure_values, atomic_values, row_offsets, structure_archive, local_archive, penalties
        )
        penalty = np.asarray(penalties, dtype=np.float64)
        fitness = (
            self.structure_weight * structure.novelty
            + self.local_weight * local.local_diversity
            - penalty
        )
        components = {
            "novelty": structure.novelty,
            "local_diversity": local.local_diversity,
            "novel_fraction": local.components.get("novel_fraction"),
            "structure_weight": np.full_like(fitness, self.structure_weight),
            "local_weight": np.full_like(fitness, self.local_weight),
        }
        return ObjectiveBatchResult(
            novelty=structure.novelty,
            local_diversity=local.local_diversity,
            novel_environment_count=local.novel_environment_count,
            fitness=fitness,
            components=components,
        )
