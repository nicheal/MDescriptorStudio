"""Shared analysis-service declarations and small numeric helpers.

Keeping these in a leaf module avoids import cycles between the service
facade, the data loader, preview/export writers and the job runner.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import numpy as np

from ..errors import ANALYSIS_INPUT_INVALID, INVALID_PARAMS, AppError

_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731
# An id that names a managed run directory. Storage wrote these prefixes, so a
# value that does not match is either corrupt or hostile - both callers pair it
# with the prefix they require (run_/ana_) before touching the filesystem.
MANAGED_ID_RE = re.compile(r"^(?:run|ana)_[A-Za-z0-9_-]{1,64}$")
# The analysis_runs.schema_version column (row layout, migration-gated, and
# nothing compares it). Three things in this codebase are called schema_version:
# this one, the per-analysis revision an algorithm stamps on its preview
# (correlation.py, metrics), and the descriptor configuration schema the engine
# reports. Only the middle one invalidates cached results - and it does so by
# being written into the params keys feature_*_schema, not by this column.
_ANALYSIS_SCHEMA_VERSION = 1
# Bumped whenever a stored result would be wrong under the new code, because it
# is part of every analysis cache key (see _analysis_cache_key): the 2026-09-20
# science-semantics batch (cross-dataset scale, zero-variance tolerance,
# coordination vs max_neighbors, affine strain) invalidated every run at once;
# studio-analysis-6 is the pass-4 semantics batch (one owner for "this feature
# carries nothing", the acquisition diversity ruler, the Mahalanobis rank guard),
# which change which samples and features a stored result is *about* rather than
# how it is laid out - so the per-analysis schema_version numbers below stay.
# studio-analysis-7 is the two remaining pass-4 semantics findings: cluster
# sampling now picks its representatives in the scaled space FPS uses, so a
# stored selection is about a different geometry than the new code returns.
# studio-analysis-8 is the two follow-ups from pass 5: a comparison's kNN overlap
# stops counting a duplicated row as its own neighbour, and a sampling artifact's
# `selection_distances` is stored in the order its `selected_indices` are.
# studio-analysis-10 restores exact chunked UMAP neighbours for every sample
# count; approximate projected candidates could silently change the graph.
# studio-analysis-11 makes sampling, constant-feature handling, and effective
# parameters explicit contracts instead of silently producing different results.
ANALYSIS_ALGORITHM_VERSION = "studio-analysis-11"
_MAX_PREVIEW_POINTS = 20_000
# Written into the canonical parameters of the two feature analyses, so bumping
# one invalidates that analysis' stored results without touching the others.
# frontend/src/preview.tsx copies both for the dev mock, and
# test_mock_backend_vocabulary.py binds the copies.
FEATURE_VARIANCE_SCHEMA = 2
FEATURE_CORRELATION_SCHEMA = 3
# Values one analysis.chunk reply may carry. A float64's shortest repr is at
# most 24 characters (1.7976931348623157e+308), so this stays under the 8 MiB
# frame cap with room for the row brackets and separators.
_MAX_CHUNK_VALUES = 262_144

COMPOSITE_BLOCKS = (
    "descriptor",
    "descriptor_summary",
    "lattice",
    "composition",
    "energy",
    "force",
)
PHYSICAL_BLOCKS = ("lattice", "composition", "energy", "force")

# Analyses that take a reference run and a query run, and therefore preview and
# page the *query* side.  Three call sites used to spell this set out by hand
# and one of them had already drifted.
CROSS_DATASET_TYPES = ("coverage", "overlap", "acquisition", "drift")

_ANALYSIS_ALIASES = {
    "hierarchical": "agglomerative",
    "isolation-forest": "isolation_forest",
    "iforest": "isolation_forest",
    "mahalanobis_distance": "mahalanobis",
    "k-nearest-neighbor": "knn",
    "cluster": "cluster_representative",
    "element": "per_element",
}


def canonical_analysis_request(analysis_type: str, params: dict | None = None) -> tuple[str, dict]:
    """Collapse legacy algorithm spellings before cache/history identity is built."""
    normalized = dict(params or {})
    requested = str(analysis_type or "").strip().lower()
    if requested in {"cluster", "clusters"}:
        requested = str(normalized.get("algorithm") or normalized.get("method") or "kmeans").strip().lower()
    elif requested in {"outlier", "outliers"}:
        requested = str(normalized.get("algorithm") or normalized.get("method") or "lof").strip().lower()
    elif requested == "sampling":
        requested = str(normalized.get("algorithm") or normalized.get("method") or "random").strip().lower()
    canonical = _ANALYSIS_ALIASES.get(requested, requested)
    if canonical in {
        "kmeans", "dbscan", "hdbscan", "agglomerative",
        "knn", "lof", "isolation_forest", "mahalanobis",
        "fps", "random", "stratified", "cluster_representative", "per_element",
    }:
        normalized["algorithm"] = canonical
        normalized.pop("method", None)
    return canonical, normalized

# Per-sample array keys the preview builder maps onto points/rows.
_PREVIEW_ARRAY_KEYS = (
    "labels", "scores", "distances", "cluster_labels", "elements",
    "coordination", "novelty", "uncertainty", "diversity",
)

def _block_names(params: dict) -> list[str]:
    """Validated composite block list from the request (empty = plain descriptor)."""
    raw = params.get("blocks")
    if raw in (None, "", []):
        return []
    if not isinstance(raw, list):
        raise AppError(ANALYSIS_INPUT_INVALID, "blocks must be a list of block names")
    names: list[str] = []
    for value in raw:
        name = str(value)
        if name not in COMPOSITE_BLOCKS:
            raise AppError(ANALYSIS_INPUT_INVALID, f"unknown sampling block {name!r}", {"blocks": list(COMPOSITE_BLOCKS)})
        if name not in names:
            names.append(name)
    return names

def _view_id(params: dict) -> str | None:
    """The optional dataset-view scope, validated the same way on every path.

    A view slices the run's samples, so it changes what sample index *N* means:
    a writer that ignored it exported different frames than the analysis it came
    from, and a cache identity that left it out reused a result whose scope had
    been edited away.  ``None`` covers both "absent" and "explicitly the whole
    dataset", so those two spellings keep sharing one cache entry.
    """
    value = params.get("view_id")
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not value.strip():
        raise AppError(INVALID_PARAMS, "view_id must be a non-empty string")
    return value

def _cell_parameters(cell: np.ndarray) -> np.ndarray:
    """a, b, c, α, β, γ of a 3×3 lattice matrix (angles in degrees)."""
    vectors = np.asarray(cell, dtype=np.float64)
    lengths = np.linalg.norm(vectors, axis=1)
    angles: list[float] = []
    for i, j in ((1, 2), (0, 2), (0, 1)):
        denominator = lengths[i] * lengths[j]
        cosine = float(vectors[i] @ vectors[j]) / denominator if denominator > 0 else 1.0
        angles.append(float(np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))))
    return np.asarray([*lengths.tolist(), *angles], dtype=np.float64)

def _descriptor_summary(values: np.ndarray) -> np.ndarray:
    """Per-structure descriptor mean, std, and tail magnitudes.

    A compact companion block: it lets a composite space react to where a
    structure sits in descriptor-summary space even when its raw vector is
    dominated by a few large components.
    """
    x = np.asarray(values, dtype=np.float64)
    magnitude = np.linalg.norm(x, axis=1)
    return np.column_stack([x.mean(axis=1), x.std(axis=1), magnitude])

def _composition_matrix(atom_numbers: list[list[int]], element_list: list[str]) -> np.ndarray:
    """Element fractions per structure, laid out along a shared element list."""
    from ..datasets.deepmd_symbols import _Z_TO_SYMBOL

    index = {symbol: position for position, symbol in enumerate(element_list)}
    fractions = np.zeros((len(atom_numbers), len(element_list)), dtype=np.float64)
    for row, numbers in enumerate(atom_numbers):
        total = max(len(numbers), 1)
        for z in numbers:
            symbol = _Z_TO_SYMBOL.get(int(z), f"Z{int(z)}")
            position = index.get(symbol)
            if position is not None:
                fractions[row, position] += 1.0
        fractions[row] /= total
    return fractions

def _require_finite(values: np.ndarray, name: str, description: str) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if not bool(np.isfinite(array).all()):
        missing = int((~np.isfinite(array)).sum())
        raise AppError(
            ANALYSIS_INPUT_INVALID,
            f"the {name} block is incomplete: {missing} sample(s) lack {description}",
            {"block": name, "missing": missing},
        )
    return array

def _pool_rows(values: np.ndarray, offsets: np.ndarray) -> np.ndarray:
    """Mean-pool atom/pair rows per structure in one vectorized pass.

    Equivalent to the historical per-structure Python loop (which held the GIL
    for seconds on large atom-level runs), but each pool step also runs with
    the GIL released. Empty structures pool to zero; np.add.reduceat needs two
    quirks handled explicitly: a start index equal to len(values) (trailing
    empty structures) is out of bounds, and repeated indices (empty groups)
    return a single element instead of zero — both are masked below.
    """
    values = np.asarray(values, dtype=np.float64)
    counts = np.diff(offsets).astype(np.int64)
    pooled = np.zeros((counts.size, values.shape[1]), dtype=np.float64)
    starts = offsets[:-1]
    # offsets are monotonic and end at len(values), so starts >= len(values)
    # can only be a run of trailing empty structures — trim them.
    k = int(np.searchsorted(starts, values.shape[0], side="left"))
    if k > 0:
        sums = np.add.reduceat(values, starts[:k], axis=0)
        pooled[:k] = sums / np.maximum(counts[:k], 1)[:, None]
    pooled[counts == 0] = 0.0
    return pooled
