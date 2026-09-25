"""Descriptor archives: the frozen memory every generation round scores against.

Two layers, by design:

* :class:`DescriptorArchive` — one row per *structure*; drives structure-level
  novelty and duplicate detection.
* :class:`LocalEnvironmentArchive` — one row per *atom environment*; drives
  local-environment novelty (G2). The pooled structure descriptor is never
  the only copy — the per-atom rows stay available un-averaged.

Lifecycle contract (what makes results reproducible):

* The reference set is fitted once — descriptors arrive raw, the archive
  owns the (frozen) scaling transform, so fitness scores stay comparable
  across generations.
* Scoring inside one round runs against a *frozen* archive; accepted
  candidates enter the archive exactly once per round, after selection.
  Never per-candidate.
* Distances go through ``generation._distance`` blocked kernels, so exact
  novelty on 100k-reference sets stays memory-bounded.
"""

from __future__ import annotations

import numpy as np

from ..analysis.sampling import FeatureScaling, apply_scaling
from ._distance import min_distance_to_reference, min_sqdist_to_set, sqdist_to_point
from .models import ArchiveEntry


class _ArchiveBase:
    def __init__(self, reference_values: np.ndarray, scaling: FeatureScaling, *, workers: int = 1) -> None:
        reference = np.asarray(reference_values, dtype=np.float64)
        if reference.ndim != 2 or reference.shape[0] == 0:
            raise ValueError("archive reference must be a non-empty 2D matrix")
        self.scaling = scaling
        self.workers = max(1, int(workers))
        # Raw mode is the identity transform. Reuse the reference matrix rather
        # than materializing another full float64 copy (large atom archives
        # can exceed several hundred MiB).
        self._reference = reference if scaling.mode == "raw" else apply_scaling(scaling, reference)
        self._accepted: list[np.ndarray] = []
        self.entries: list[ArchiveEntry] = []

    # -- introspection -----------------------------------------------------
    @property
    def size(self) -> int:
        """Number of accepted entries (the reference set is not counted)."""
        return len(self.entries)

    @property
    def reference_size(self) -> int:
        return int(self._reference.shape[0])

    @property
    def reference(self) -> np.ndarray:
        """Scaled reference rows — the fixed domain coverage is measured on."""
        return self._reference

    @property
    def feature_count(self) -> int:
        return int(self._reference.shape[1])

    @property
    def accepted_matrix(self) -> np.ndarray | None:
        """Scaled rows of everything accepted so far (None when empty)."""
        if not self._accepted:
            return None
        return np.concatenate(self._accepted)

    # -- queries -----------------------------------------------------------
    def _nearest_sq(self, x_scaled: np.ndarray) -> np.ndarray:
        d2 = min_sqdist_to_set(x_scaled, self._reference, workers=self.workers)
        if self._accepted:
            np.minimum(d2, self.nearest_accepted_sq(x_scaled), out=d2)
        return d2

    def nearest_accepted_sq(self, x_scaled: np.ndarray) -> np.ndarray:
        """Squared distance to accepted rows, without joining archive blocks."""
        x_scaled = np.asarray(x_scaled, dtype=np.float64)
        d2 = np.full(x_scaled.shape[0], np.inf, dtype=np.float64)
        for accepted in self._accepted:
            np.minimum(d2, min_sqdist_to_set(x_scaled, accepted, workers=self.workers), out=d2)
        return d2

    def nearest(self, values: np.ndarray) -> np.ndarray:
        """Distance of every raw row to the nearest archived point."""
        x = apply_scaling(self.scaling, np.atleast_2d(np.asarray(values, dtype=np.float64)))
        return np.sqrt(np.clip(self._nearest_sq(x), 0.0, None))

    def contains_near(self, values: np.ndarray, threshold: float) -> np.ndarray:
        """Boolean mask: raw rows sitting closer than ``threshold`` to the archive."""
        return self.nearest(values) < float(threshold)

    def coverage_radius(self, query: np.ndarray | None = None) -> float:
        """Covering radius R = max_i min_{a in A} d(x_i, a).

        Over ``query`` (raw rows) when given, else over the reference set
        itself — how well the archive covers its own domain.
        """
        # The reference matrix is itself part of this archive, so its exact
        # covering radius is always zero. Scanning it against itself would be
        # an O(N²) no-op at the end of every generation round.
        if query is None:
            return 0.0
        target = apply_scaling(self.scaling, np.atleast_2d(np.asarray(query, dtype=np.float64)))
        return float(np.sqrt(max(self._nearest_sq(target).max(), 0.0)))

    def accepted_coverage_radius(self, query: np.ndarray | None = None) -> float | None:
        """Covering radius of the *accepted* set alone over ``query``.

        R_acc = max_{x in query} min_{a in accepted} d(x, a), with ``query``
        defaulting to the reference domain. Unlike :meth:`coverage_radius`
        this deliberately ignores the reference rows, so it measures how well
        the generated structures cover the domain — the metric that makes
        "Random vs GA: Coverage Radius ↓" meaningful. Returns None until the
        first accept, and is non-increasing as accepts accumulate.
        """
        if not self._accepted:
            return None
        if query is None:
            target = self._reference
        else:
            target = apply_scaling(self.scaling, np.atleast_2d(np.asarray(query, dtype=np.float64)))
        return float(np.sqrt(max(self.nearest_accepted_sq(target).max(), 0.0)))

    # -- mutation ----------------------------------------------------------
    def add(self, values: np.ndarray, entries: list[ArchiveEntry]) -> None:
        """Accept raw rows; called once per round with the selected batch."""
        x = apply_scaling(self.scaling, np.asarray(values, dtype=np.float64))
        if x.ndim != 2 or x.shape[0] != len(entries):
            raise ValueError("accepted values must match the number of entries")
        self._accepted.append(np.ascontiguousarray(x))
        self.entries.extend(entries)

    def pair_distances_to(self, values: np.ndarray, index: int) -> np.ndarray:
        """Distance of every raw row to one already-scaled accepted row.
        """
        accepted = self.accepted_matrix
        if accepted is None or index >= accepted.shape[0]:
            raise IndexError(index)
        x = apply_scaling(self.scaling, np.atleast_2d(np.asarray(values, dtype=np.float64)))
        return np.sqrt(np.clip(sqdist_to_point(x, accepted[index]), 0.0, None))


class DescriptorArchive(_ArchiveBase):
    """Structure-level archive: one descriptor row per structure."""


class LocalEnvironmentArchive(_ArchiveBase):
    """Atom-environment archive: one descriptor row per local environment.

    ``add`` differs from the structure archive: one accepted structure
    contributes many atom rows, so the row count intentionally does not
    match the number of entries (one entry per accepted structure).
    """

    def add(self, values: np.ndarray, entries: list[ArchiveEntry]) -> None:
        x = apply_scaling(self.scaling, np.asarray(values, dtype=np.float64))
        if x.ndim != 2 or x.shape[0] == 0:
            raise ValueError("accepted local-environment values must be a non-empty 2D matrix")
        self._accepted.append(np.ascontiguousarray(x))
        self.entries.extend(entries)

    @property
    def atom_row_count(self) -> int:
        return sum(int(block.shape[0]) for block in self._accepted)

    def novel_count(self, values: np.ndarray, threshold: float) -> np.ndarray:
        """Per-row boolean-count: how many raw rows sit farther than ``threshold``."""
        return (self.nearest(values) > float(threshold)).astype(np.int64)

    def nearest_per_row(self, values: np.ndarray) -> np.ndarray:
        """Per-row nearest-archive distance (unaggregated — aggregation is
        the objective's job: mean / top-Q mean / quantile / max)."""
        return self.nearest(values)
