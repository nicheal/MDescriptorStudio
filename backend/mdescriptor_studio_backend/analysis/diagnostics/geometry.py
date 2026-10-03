"""Geometry helpers for descriptor diagnostics.

Diagnostics are the only analysis family that must look back at the raw
structures behind a descriptor matrix, so the structural side lives here:
SE(3)/reflection transforms, minimum-image pair distances and the
permutation-invariant structural fingerprints that "descriptor distance vs
structure distance" comparisons are built on.

Everything in this package is engine-free: frames enter as duck-typed objects
with ``numbers``/``positions``/``cell``/``pbc`` (``datasets.base.DatasetFrame``
satisfies this) and descriptors enter as numpy arrays or recompute callables.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DescriptorRecompute:
    """One recompute result, aligned both ways (mirrors the engine contract).

    ``structure_values`` always has exactly ``n_frames`` rows (atom/pair rows
    mean-pooled per frame).  ``atomic_values``/``row_offsets`` are present only
    when the descriptor reports per-atom rows.
    """

    structure_values: np.ndarray  # (n_frames, n_features)
    atomic_values: np.ndarray | None = None  # (n_rows_total, n_features)
    row_offsets: np.ndarray | None = None  # (n_frames + 1,)

    def atomic_rows(self, frame_index: int) -> np.ndarray:
        """Per-atom rows of one frame; raises when rows were not reported."""
        if self.atomic_values is None or self.row_offsets is None:
            raise ValueError("descriptor recompute did not report per-atom rows")
        start = int(self.row_offsets[frame_index])
        end = int(self.row_offsets[frame_index + 1])
        return self.atomic_values[start:end]


def random_rotation(rng: np.random.Generator) -> np.ndarray:
    """Uniform random SO(3) matrix (QR on a Gaussian matrix, determinant +1)."""
    q, r = np.linalg.qr(rng.normal(size=(3, 3)))
    q *= np.sign(np.diag(r))
    if np.linalg.det(q) < 0:
        q[:, 0] = -q[:, 0]
    return q


def reflection_matrix(rng: np.random.Generator) -> np.ndarray:
    """Reflection through a random plane through the origin (Householder, det -1)."""
    normal = rng.normal(size=3)
    norm = np.linalg.norm(normal)
    if norm < 1e-12:
        normal = np.array([1.0, 0.0, 0.0])
    else:
        normal = normal / norm
    return np.eye(3) - 2.0 * np.outer(normal, normal)


def transform_frame(frame, linear: np.ndarray | None = None, translation: np.ndarray | None = None):
    """Apply R @ x + t to positions and R @ cell, returning a plain frame copy."""
    positions = np.asarray(frame.positions, dtype=np.float64)
    cell = np.asarray(frame.cell, dtype=np.float64)
    if linear is not None:
        positions = positions @ np.asarray(linear, dtype=np.float64).T
        cell = cell @ np.asarray(linear, dtype=np.float64).T
    if translation is not None:
        positions = positions + np.asarray(translation, dtype=np.float64)
    return replace_geometry(frame, positions=positions, cell=cell)


def permute_frame(frame, permutation: np.ndarray):
    """Reindex atoms: new atom k carries the species/position of old atom permutation[k]."""
    permutation = np.asarray(permutation, dtype=np.int64)
    numbers = np.asarray(frame.numbers)[permutation]
    positions = np.asarray(frame.positions, dtype=np.float64)[permutation]
    return replace_geometry(frame, numbers=numbers, positions=positions)


def replace_geometry(frame, *, numbers=None, positions=None, cell=None):
    """Rebuild a frame of the same duck type with replaced arrays.

    DatasetFrame is a dataclass, so field replacement by constructor covers the
    reader types without importing the datasets layer from analysis.
    """
    import dataclasses

    if dataclasses.is_dataclass(frame):
        fields = {}
        for f in dataclasses.fields(frame):
            value = getattr(frame, f.name)
            if f.name == "numbers" and numbers is not None:
                value = numbers
            elif f.name == "positions" and positions is not None:
                value = positions
            elif f.name == "cell" and cell is not None:
                value = cell
            fields[f.name] = value
        return type(frame)(**fields)
    raise TypeError("diagnostic transforms require dataclass frames (datasets.base.DatasetFrame)")


def minimum_image(deltas: np.ndarray, cell: np.ndarray, pbc: np.ndarray) -> np.ndarray:
    """Wrap displacement vectors into the primitive cell where PBC is active.

    ``deltas``: (..., 3); ``cell`` rows are lattice vectors.  Uses one fractional
    round, which is exact for orthorhombic cells and the standard approximation
    for triclinic ones - the same convention the rest of the backend assumes for
    quick geometry screens.
    """
    deltas = np.asarray(deltas, dtype=np.float64)
    cell = np.asarray(cell, dtype=np.float64)
    pbc = np.asarray(pbc, dtype=bool)
    if not pbc.any() or not np.any(np.abs(cell) > 1e-12):
        return deltas
    inverse = np.linalg.inv(cell)
    # Wrap only the periodic axes; a fractional coordinate on a non-periodic
    # axis is meaningless and must stay untouched.
    wrap_fractional = np.where(pbc, np.round(deltas @ inverse), 0.0)
    return deltas - (wrap_fractional @ cell)


def pairwise_minimum_image_distances(positions: np.ndarray, cell: np.ndarray, pbc: np.ndarray) -> np.ndarray:
    """Full (n, n) minimum-image distance matrix of one frame."""
    positions = np.asarray(positions, dtype=np.float64)
    deltas = positions[:, None, :] - positions[None, :, :]
    if np.asarray(pbc).any():
        inverse = np.linalg.inv(np.asarray(cell, dtype=np.float64))
        fractional = deltas @ inverse
        wrapped = np.where(np.asarray(pbc, dtype=bool), np.round(fractional), 0.0)
        deltas = deltas - wrapped @ np.asarray(cell, dtype=np.float64)
    return np.linalg.norm(deltas, axis=-1)


def structure_fingerprint(frame) -> np.ndarray:
    """Permutation-invariant structural fingerprint: sorted min-image pair distances.

    Two structures with the same atom count compare by L2 on this vector; a
    different atom count makes the comparison undefined and callers must treat
    the pair as structurally far instead of truncating to a shared prefix.
    """
    distances = pairwise_minimum_image_distances(
        np.asarray(frame.positions, dtype=np.float64),
        np.asarray(frame.cell, dtype=np.float64),
        np.asarray(frame.pbc, dtype=bool),
    )
    upper = distances[np.triu_indices_from(distances, k=1)]
    return np.sort(upper)


def structure_distance(frame_a, frame_b) -> float:
    """L2 between structural fingerprints; ``inf`` when atom counts differ.

    An ``inf`` is the honest answer for a different composition size: such a
    pair is structurally far by construction, and a descriptor that maps it to
    a nearby point is exactly the degeneracy the search exists to surface.
    """
    if len(np.asarray(frame_a.numbers)) != len(np.asarray(frame_b.numbers)):
        return float("inf")
    a = structure_fingerprint(frame_a)
    b = structure_fingerprint(frame_b)
    return float(np.linalg.norm(a - b))


def composition_counts(numbers: np.ndarray) -> dict[int, int]:
    """Species code -> count for one frame or environment."""
    values, counts = np.unique(np.asarray(numbers), return_counts=True)
    return {int(k): int(v) for k, v in zip(values, counts)}


def composition_distance(numbers_a: np.ndarray, numbers_b: np.ndarray) -> float:
    """L1 distance between normalized species-count vectors (0 for identical)."""
    counts_a = composition_counts(numbers_a)
    counts_b = composition_counts(numbers_b)
    universe = sorted(set(counts_a) | set(counts_b))
    total_a = sum(counts_a.values()) or 1
    total_b = sum(counts_b.values()) or 1
    distance = 0.0
    for species in universe:
        distance += abs(counts_a.get(species, 0) / total_a - counts_b.get(species, 0) / total_b)
    return float(distance / 2.0)  # L1 of fractions, in [0, 1]


def environment_fingerprint(numbers: np.ndarray, positions: np.ndarray, cell, pbc, center: int, cutoff: float, max_neighbors: int) -> np.ndarray:
    """Permutation-invariant local-environment fingerprint, fixed length.

    Layout: ``[sorted neighbor distances (padded with 0), sorted neighbor
    species codes (padded with 0)]``.  The center species is *not* included;
    callers that want it compare it separately (``environment_distance``).
    """
    numbers = np.asarray(numbers)
    positions = np.asarray(positions, dtype=np.float64)
    deltas = positions - positions[center]
    if np.asarray(pbc).any() and np.any(np.abs(np.asarray(cell)) > 1e-12):
        inverse = np.linalg.inv(np.asarray(cell, dtype=np.float64))
        wrapped = np.where(np.asarray(pbc, dtype=bool), np.round(deltas @ inverse), 0.0)
        deltas = deltas - wrapped @ np.asarray(cell, dtype=np.float64)
    distances = np.linalg.norm(deltas, axis=1)
    mask = (distances <= cutoff) & (np.arange(len(numbers)) != center)
    neighbor_distances = np.sort(distances[mask])[:max_neighbors]
    neighbor_species = np.sort(numbers[mask])[:max_neighbors]
    padded_distances = np.zeros(max_neighbors, dtype=np.float64)
    padded_species = np.zeros(max_neighbors, dtype=np.int64)
    n = neighbor_distances.size
    padded_distances[:n] = neighbor_distances
    padded_species[:n] = neighbor_species
    return np.concatenate([padded_distances, padded_species.astype(np.float64)])


def environment_distance(
    numbers_a: np.ndarray,
    positions_a: np.ndarray,
    center_a: int,
    numbers_b: np.ndarray,
    positions_b: np.ndarray,
    center_b: int,
    cell_a,
    pbc_a,
    cell_b,
    pbc_b,
    cutoff: float,
    max_neighbors: int,
) -> tuple[float, float]:
    """Geometric + species distance between two local environments.

    Returns ``(d_geometry, d_species)``: L2 between the distance fingerprints
    and an L1 species-mismatch term in [0, 2] (center species mismatch counts
    as 1, neighbor species-fraction L1 fills the rest).  Callers combine them
    with explicit weights so the geometric component stays interpretable.
    """
    fingerprint_a = environment_fingerprint(numbers_a, positions_a, cell_a, pbc_a, center_a, cutoff, max_neighbors)
    fingerprint_b = environment_fingerprint(numbers_b, positions_b, cell_b, pbc_b, center_b, cutoff, max_neighbors)
    half = fingerprint_a.size // 2
    d_geometry = float(np.linalg.norm(fingerprint_a[:half] - fingerprint_b[:half]))
    center_mismatch = 0.0 if int(numbers_a[center_a]) == int(numbers_b[center_b]) else 1.0
    d_species = center_mismatch + composition_distance(numbers_a, numbers_b)
    return d_geometry, d_species


__all__ = [
    "DescriptorRecompute",
    "composition_counts",
    "composition_distance",
    "environment_distance",
    "environment_fingerprint",
    "minimum_image",
    "pairwise_minimum_image_distances",
    "permute_frame",
    "random_rotation",
    "reflection_matrix",
    "replace_geometry",
    "structure_distance",
    "structure_fingerprint",
    "transform_frame",
]
