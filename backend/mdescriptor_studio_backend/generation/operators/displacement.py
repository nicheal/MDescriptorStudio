"""Atomic displacement: add a Cartesian offset vector to every position."""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ..models import StructureCandidate


def displaced(frame, displacement: np.ndarray):
    """Return ``frame`` with ``displacement`` added to all positions.

    The perturbation-sensitivity runner pre-computes one jitter direction per
    source frame and scales it by the amplitude, so every amplitude on a
    frame shares the same direction and the response curve isolates magnitude.
    """
    positions = np.asarray(frame.positions, dtype=np.float64)
    displacement = np.asarray(displacement, dtype=np.float64)
    if displacement.shape != positions.shape:
        raise ValueError(
            f"displacement shape {displacement.shape} does not match positions shape {positions.shape}"
        )
    return replace(frame, positions=positions + displacement)


class AtomicDisplacement:
    """Random per-atom Gaussian jitter, magnitude drawn per application.

    The sigma is drawn uniformly from (0, max_sigma] so the run covers the
    whole allowed amplitude range instead of only its upper bound; the
    realised maximum displacement is recorded for the geometry filter.
    """

    name = "atomic_displacement"

    def __init__(self, max_sigma: float = 0.15) -> None:
        self.max_sigma = float(max_sigma)

    def apply(self, parent: StructureCandidate, rng: np.random.Generator, params: dict) -> StructureCandidate:
        max_sigma = float(params.get("max_sigma", self.max_sigma))
        if not 0.0 < max_sigma <= 5.0:
            raise ValueError("max_sigma must be in (0, 5] Å")
        sigma = max_sigma * float(rng.uniform(0.05, 1.0))
        base = np.asarray(parent.positions, dtype=np.float64)
        delta = rng.normal(0.0, sigma, size=base.shape)
        return parent.child(
            candidate_id=f"{parent.candidate_id}_d{rng.integers(0, 2**31)}",
            positions=base + delta,
            operator=self.name,
            operator_params={"max_sigma": max_sigma, "sigma": sigma},
            metadata={"displacement_max": float(np.abs(delta).max())},
        )
