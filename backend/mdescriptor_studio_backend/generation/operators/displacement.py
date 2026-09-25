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
    """Random per-atom Gaussian jitter with an optional hard cutoff.

    Two parameters with deliberately different meanings (G3.5 A11):

    * ``max_sigma`` — the largest per-structure Gaussian σ (each application
      draws σ uniformly from (0.05, 1.0]·max_sigma). It bounds the
      *distribution*, never any single displacement: the Gaussian tail can
      and does produce larger excursions.
    * ``hard_cutoff`` — the hard bound in Å. Any atom whose displacement
      norm exceeds it is rescaled onto the cutoff sphere, direction
      preserved. ``None`` (the default) keeps the pure Gaussian.

    The realised per-atom displacement is recorded as the true norm
    ``max_i ||Δr_i||`` for the geometry filter — the old maximum Cartesian
    component understated the displacement by up to √3.
    """

    name = "atomic_displacement"

    def __init__(self, max_sigma: float = 0.15, hard_cutoff: float | None = None) -> None:
        self.max_sigma = float(max_sigma)
        self.hard_cutoff = None if hard_cutoff is None else float(hard_cutoff)

    def apply(self, parent: StructureCandidate, rng: np.random.Generator, params: dict) -> StructureCandidate:
        max_sigma = float(params.get("max_sigma", self.max_sigma))
        if not 0.0 < max_sigma <= 5.0:
            raise ValueError("max_sigma must be in (0, 5] Å")
        hard_cutoff = params.get("hard_cutoff", self.hard_cutoff)
        if hard_cutoff is not None:
            hard_cutoff = float(hard_cutoff)
            if not 0.0 < hard_cutoff <= 20.0:
                raise ValueError("hard_cutoff must be in (0, 20] Å")
        sigma = max_sigma * float(rng.uniform(0.05, 1.0))
        base = np.asarray(parent.positions, dtype=np.float64)
        delta = rng.normal(0.0, sigma, size=base.shape)
        norms = np.linalg.norm(delta, axis=1) if delta.size else np.zeros(0)
        if hard_cutoff is not None and norms.size and norms.max() > hard_cutoff:
            scale = np.where(norms > hard_cutoff, hard_cutoff / np.maximum(norms, 1e-300), 1.0)
            delta = delta * scale[:, None]
            norms = np.minimum(norms, hard_cutoff)
        recorded_params = {"max_sigma": max_sigma, "sigma": sigma}
        if hard_cutoff is not None:
            recorded_params["hard_cutoff"] = hard_cutoff
        return parent.child(
            candidate_id=f"{parent.candidate_id}_d{rng.integers(0, 2**31)}",
            positions=base + delta,
            operator=self.name,
            operator_params=recorded_params,
            metadata={"displacement_max": float(norms.max()) if norms.size else 0.0},
        )
