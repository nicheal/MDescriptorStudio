"""Coverage-completion objective (G3): shrink the covering radius of a
*known* reference domain.

Novelty expansion asks "how far is this candidate from everything we have";
coverage completion asks "how much does this candidate reduce the worst
distance any reference point is from the archive". For a reference pool R
and archive A:

    R_cov(A)  = max_{x in R} min_{a in A} ||x - a||
    gain(X)   = R_cov(A) - R_cov(A ∪ {X})   ≥ 0

With an empty archive there is no radius to shrink; the first pick is ranked
by ``-R_cov({X})`` so the archive is seeded with the max-min centre of the
reference domain (the candidate whose addition leaves the smallest radius).

Unlike novelty, the signal is sparse: only candidates near the current
farthest reference points score. That is exactly the behaviour wanted when
completing coverage of an existing domain (e.g. labelling 10k structures out
of a 1M-frame trajectory), and exactly why it is a separate objective from
novelty expansion rather than a mode of it.
"""

from __future__ import annotations

import numpy as np

from ...analysis.sampling import apply_scaling
from .._distance import sqdist_to_point
from .base import ObjectiveBatchResult


class CoverageGainObjective:
    name = "coverage"
    needs_atomic = False
    # Coverage never reads per-atom rows and never emits environment counts,
    # so the engine must not run the local-environment discovery-rate stop.
    produces_novel_environment_count = False

    def __init__(self, **_ignored) -> None:
        # The covering radius is fully determined by the archive; there are no
        # tunable weights. Extra payload keys are ignored so forward-compatible
        # clients do not break.
        pass

    def evaluate_batch(self, structure_values, atomic_values, row_offsets, structure_archive, local_archive, penalties):
        del atomic_values, row_offsets, local_archive
        values = np.atleast_2d(np.asarray(structure_values, dtype=np.float64))
        scaled = apply_scaling(structure_archive.scaling, values)
        reference = structure_archive.reference

        base_d2 = structure_archive.nearest_accepted_sq(reference)
        if np.isfinite(base_d2).all():
            radius_now = float(np.sqrt(np.clip(base_d2, 0.0, None).max()))
        else:
            radius_now = np.inf  # empty archive: nothing covers the domain yet

        gain = np.zeros(values.shape[0], dtype=np.float64)
        for index in range(values.shape[0]):
            d2 = np.minimum(base_d2, sqdist_to_point(reference, scaled[index]))
            radius_new = float(np.sqrt(np.clip(d2, 0.0, None).max()))
            if np.isfinite(radius_now):
                gain[index] = max(radius_now - radius_new, 0.0)
            else:
                # Empty archive: there is no radius to shrink yet, and the
                # greedy coverage step must seed the archive with the
                # candidate that leaves the *smallest* covering radius
                # behind — argmin R({X}), the max-min centre of the domain.
                # Ranking by radius_new itself would pick the farthest
                # outlier first and actively worsen coverage.
                gain[index] = -radius_new
        novelty = structure_archive.nearest(values)
        fitness = gain - np.asarray(penalties, dtype=np.float64)
        return ObjectiveBatchResult(
            novelty=novelty,
            local_diversity=None,
            novel_environment_count=None,
            fitness=fitness,
            components={"coverage_gain": gain, "novelty": novelty},
        )
