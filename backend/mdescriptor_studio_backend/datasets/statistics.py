"""Streaming dataset statistics (design doc §14); histograms pre-binned so raw
arrays never cross the IPC boundary (Rule 6)."""

from __future__ import annotations

import hashlib
from collections import Counter
from itertools import product

import numpy as np

from . import native as _native
from .base import DatasetAdapter, pbc_summary
from .covalent_radii import _ARRAY as _RADII_TABLE, radii_for
from .deepmd_symbols import _Z_TO_SYMBOL as _Z_LOOKUP

if not _native.native_available():
    # Without the compiled kernel the scan below runs the scipy reference
    # implementation inside a job thread.  scipy must then be imported up
    # front (main thread, before the deferred engine warmup starts): a first
    # scipy import concurrent with warmup's native-kernel compute deadlocks
    # extension-module initialization on Windows.
    from scipy.spatial import cKDTree  # noqa: F401

BINS = 40
# Cached stats are refused when this differs, so it moves with anything a
# stored payload would now state wrongly. 6 = force magnitudes are counted
# through a streaming grid instead of held for an exact median (see
# _ForceMagnitudeCounts): the min/max/mean are unchanged, the median and the
# 40-bin counts are resolved to within one bin.
STATS_VERSION = 6
EXTREME_FORCE_EV_A = 50.0  # per-atom |F| above this flags the frame (health panel)
# ‖ΣF‖ above this flags the frame (force-balance check; NepTrainKit's default
# force_balance_threshold — DFT labels should be translationally balanced)
NET_FORCE_EV_A = 1e-3
# pair distance < coefficient × (covalent radius sum) flags the frame
# (non-physical structure check; NepTrainKit's radius_coefficient)
SHORT_CONTACT_COEFFICIENT = 0.7
_CELL_DET_TOL = 1e-8
# lattice images per periodic axis when hunting the minimum distance; closest
# pairs needing more only occur in extremely skewed cells (angles below ~25°)
_MIN_DISTANCE_IMAGE_LIMIT = 2
# Rows per image-shift query: atoms x images x 3 is the temporary this hunt
# materialises, and a million-atom frame with a 2-image stencil is gigabytes on
# one shot. 250k rows is a few MB per query.
_MIN_DISTANCE_QUERY_ROWS = 250_000
# short-contact scan batching: query atoms per ball-query call and the pair
# count that forces a vectorized scan flush (bounds memory on pathological
# frames; the scan stops at the first violating pair)
_SHORT_CONTACT_ATOM_CHUNK = 4096
_SHORT_CONTACT_PAIR_BATCH = 1_000_000
# per-check frame-index lists returned alongside the health counts; capped so
# a pathological dataset cannot blow up the stats JSON (counts stay exact)
HEALTH_FINDINGS_CAP = 5000
# Force magnitudes are the one statistic every atom in the dataset contributes
# to.  Holding them all for an exact median made the scan's live memory grow
# with the dataset (250k structures x 100 atoms is ~200 MB, and the final
# `np.concatenate` asked for that again), so past a fixed budget they are counted
# into a fixed-width grid as they stream past instead: a few hundred KB whatever
# the size, with min/max/mean exact and the median and the 40-bin chart resolved
# to within one grid step.  Below the budget the values are kept verbatim and
# both stay exact - a small dataset's median interpolates across gaps the grid
# cannot see.
FORCE_MAGNITUDE_BIN = 1e-3  # eV/Å
# Grid covers [0, ~4194 eV/Å); a magnitude above it is counted in the last bin.
_FORCE_MAGNITUDE_BINS = 1 << 22
# Magnitudes kept verbatim before the grid takes over: 8 MiB of float64.
_FORCE_MAGNITUDE_VALUES_KEPT = 1 << 20
# Binning scales by the reciprocal of the step rather than dividing by it.
_FORCE_MAGNITUDE_PER_BIN = 1.0 / FORCE_MAGNITUDE_BIN


class _ForceMagnitudeCounts:
    """Per-atom force magnitudes, kept exact while they fit and counted after.

    Feeds the same two payloads the whole-array code did - `force_magnitude`
    (the 40-bin chart) and `force_magnitude_summary` - through `_hist` and
    `_summary` while the dataset's magnitudes fit the budget, and from the grid
    once they do not.
    """

    def __init__(self) -> None:
        self.counts: np.ndarray | None = None
        self.kept: list[np.ndarray] = []
        self.kept_values = 0
        self.count = 0
        self.total = 0.0
        self.lowest = float("inf")
        self.highest = float("-inf")

    def add(self, magnitudes: np.ndarray) -> None:
        values = magnitudes[np.isfinite(magnitudes)]
        if values.size == 0:
            return
        self.count += int(values.size)
        self.total += float(values.sum())
        self.lowest = min(self.lowest, float(values.min()))
        self.highest = max(self.highest, float(values.max()))
        if self.counts is None:
            self.kept.append(values)
            self.kept_values += int(values.size)
            if self.kept_values > _FORCE_MAGNITUDE_VALUES_KEPT:
                self.counts = np.zeros(1024, dtype=np.int64)
                for retained in self.kept:
                    self._count(retained)
                self.kept, self.kept_values = [], 0
        else:
            self._count(values)

    def _count(self, values: np.ndarray) -> None:
        bins = values * _FORCE_MAGNITUDE_PER_BIN
        needed = int(bins.max()) + 2
        counts = self.counts
        assert counts is not None  # only reached once add() has switched to the grid
        if needed > counts.size:
            size = counts.size
            while size < needed and size < _FORCE_MAGNITUDE_BINS:
                size *= 2
            grown = np.zeros(min(size, _FORCE_MAGNITUDE_BINS), dtype=np.int64)
            grown[: counts.size] = counts
            counts = self.counts = grown
        index = np.minimum(bins.astype(np.int64), counts.size - 1)
        counts += np.bincount(index, minlength=counts.size)

    def histogram(self) -> dict | None:
        if self.count == 0:
            return None
        if self.counts is None:
            return _hist(self._kept())
        low, high = self.lowest, self.highest
        if high == low:
            # np.histogram's own rule for a constant column.
            low, high = low - 0.5, high + 0.5
        width = (high - low) / BINS
        edges = np.linspace(low, high, BINS + 1)
        left = np.arange(self.counts.size, dtype=np.float64) * FORCE_MAGNITUDE_BIN
        # A grid bin overlaps one chart bin or two adjacent ones: split its count
        # by the overlap. `keep` is how much of the step still lies inside the
        # chart bin it starts in, so a bin that sits wholly inside contributes
        # all of its count and only the crossing part of a step is shared.
        position = np.clip((left - low) / width, 0.0, BINS - 1e-12)
        below = np.floor(position).astype(np.int64)
        above = np.minimum(below + 1, BINS - 1)
        keep = np.clip((1.0 - (position - below)) * width / FORCE_MAGNITUDE_BIN, 0.0, 1.0)
        weights = self.counts.astype(np.float64)
        counts = np.bincount(below, weights=weights * keep, minlength=BINS)
        counts += np.bincount(above, weights=weights * (1.0 - keep), minlength=BINS)
        return {
            "edges": [round(float(e), 6) for e in edges],
            "counts": [int(c) for c in np.rint(counts[:BINS])],
        }

    def summary(self) -> dict | None:
        if self.count == 0:
            return None
        if self.counts is None:
            return _summary(self._kept())
        # The rank np.median would take, located in the grid; the midpoint of the
        # part of that bin the data can actually occupy is the estimate, so a
        # distribution narrower than one step still reports inside its own range.
        rank = (self.count - 1) / 2.0
        index = int(np.flatnonzero(self.counts.cumsum() > rank)[0])
        low = max(index * FORCE_MAGNITUDE_BIN, self.lowest)
        high = min((index + 1) * FORCE_MAGNITUDE_BIN, self.highest)
        return {
            "min": round(self.lowest, 6),
            "max": round(self.highest, 6),
            "mean": round(self.total / self.count, 6),
            "median": round((low + high) / 2.0, 6),
        }

    def _kept(self) -> np.ndarray:
        return np.concatenate(self.kept) if self.kept else np.zeros(0)



def _finite(values: list | np.ndarray) -> np.ndarray:
    """Drop NaN/Infinity before binning or summarising.

    np.histogram turns a single NaN into a ValueError that fails the whole
    statistics job, and a summary built from NaN publishes bounds the IPC
    frame cannot carry. Sources that store arrays (DeepMD, native) reach here
    without the finite check the text parsers apply.
    """
    arr = np.asarray(values, dtype=np.float64).reshape(-1)
    return arr[np.isfinite(arr)]


def _hist(values: list | np.ndarray) -> dict | None:
    arr = _finite(values)
    if arr.size == 0:
        return None
    counts, edges = np.histogram(arr, bins=BINS)
    return {
        "edges": [round(float(e), 6) for e in edges],
        "counts": [int(c) for c in counts],
    }


def _summary(values: list | np.ndarray) -> dict | None:
    arr = _finite(values)
    if arr.size == 0:
        return None
    return {
        "min": round(float(arr.min()), 6),
        "max": round(float(arr.max()), 6),
        "mean": round(float(arr.mean()), 6),
        "median": round(float(np.median(arr)), 6),
    }


def _prepared_points(
    positions: np.ndarray, cell: np.ndarray, pbc: np.ndarray
) -> tuple[np.ndarray | None, bool]:
    """Positions ready for neighbor queries: periodic axes are wrapped into
    the cell (minimum-image convention), non-periodic ones kept as-is.
    Returns (None, False) for empty or non-finite positions, otherwise
    (points, is_periodic)."""
    pos = np.asarray(positions, dtype=np.float64)
    if pos.ndim != 2 or pos.shape[0] == 0 or not np.isfinite(pos).all():
        return None, False
    cell = np.asarray(cell, dtype=np.float64)
    pbc = np.asarray(pbc, dtype=bool)
    periodic = (
        bool(pbc.any())
        and cell.shape == (3, 3)
        and bool(np.isfinite(cell).all())
        and abs(float(np.linalg.det(cell))) > _CELL_DET_TOL
    )
    if not periodic:
        return pos, False
    frac = pos @ np.linalg.inv(cell)
    frac[:, pbc] -= np.floor(frac[:, pbc])
    return frac @ cell, True


def _geometry_input(positions: np.ndarray, cell: np.ndarray, pbc: np.ndarray) -> tuple:
    """Wrapped coordinates, periodicity and the tree over them, built once.

    The reference (no-native-kernel) path used to prepare the same frame twice -
    once to hunt the minimum distance and again for the short-contact scan - so
    every frame paid two wraps, two inversions and two cKDTree builds. The
    compiled kernel makes this path rare; where it runs, at 2000 atoms the
    duplicate was 2% of the frame's geometry time.
    """
    pts, periodic = _prepared_points(positions, cell, pbc)
    if pts is None:
        return None, False, None
    from scipy.spatial import cKDTree  # pinned runtime dep; deferred like engine.py

    return pts, periodic, cKDTree(pts)


def _frame_min_distance(
    positions: np.ndarray,
    cell: np.ndarray,
    pbc: np.ndarray,
    prepared: tuple | None = None,
) -> float | None:
    """Minimum interatomic distance within one structure.

    Periodic axes use the minimum-image convention (analysis neighbor-graph
    semantics): coordinates are wrapped into the cell and every atom is also
    queried against lattice-image shifts, self-image contacts included — a
    single-atom periodic cell therefore reports its nearest periodicity
    length.  The shift stencil follows the same conservative bound as
    engine.py (any displacement with norm <= best has lattice coefficients
    <= best * |inv col|, +1 for the wrap), capped per axis; cells so skewed
    that the cap binds report the closest distance found within it.  Returns
    None for empty or non-finite structures, and isolated structures with
    fewer than two atoms.
    """
    pts, periodic, tree = prepared if prepared is not None else _geometry_input(positions, cell, pbc)
    if pts is None:
        return None
    n = pts.shape[0]
    cell = np.asarray(cell, dtype=np.float64)
    pbc = np.asarray(pbc, dtype=bool)
    if n >= 2:
        best = float(tree.query(pts, k=2)[0][:, 1].min())
    elif not periodic:
        return None
    else:
        best = np.inf
    if periodic:
        inverse = np.linalg.inv(cell)
        block_shifts = max(1, _MIN_DISTANCE_QUERY_ROWS // max(n, 1))
        # query lattice-image shifts outward until the bound below proves the
        # stencil covers every pair that could still beat `best`: a pair at
        # distance <= best has |S_k| <= best * |inv col_k| + 1 per periodic
        # axis (the +1 absorbs the wrapped fractional spread)
        seen: set[tuple[int, ...]] = set()
        limits = [1 if pbc[axis] else 0 for axis in range(3)]
        while True:
            if np.isfinite(best):
                limits = [
                    max(
                        limits[axis],
                        min(
                            _MIN_DISTANCE_IMAGE_LIMIT,
                            int(best * np.linalg.norm(inverse[:, axis])) + 1,
                        ),
                    )
                    if pbc[axis]
                    else 0
                    for axis in range(3)
                ]
            tuples = [
                s
                for s in product(*[range(-l, l + 1) for l in limits])
                if any(s) and s not in seen
            ]
            if not tuples:
                break
            seen.update(tuples)
            shifts = np.asarray(tuples, dtype=np.float64)
            # Query a bounded slice of the stencil at a time. The whole
            # (shifts x atoms x 3) temporary is gigabytes on a million-atom
            # frame - which the pre-check in deepmd.py allows through, since it
            # bounds atoms per frame and not the product with the image count -
            # and the minimum over slices is the same number the one query gave.
            for start in range(0, shifts.shape[0], block_shifts):
                queries = (pts[None, :, :] + (shifts[start : start + block_shifts] @ cell)[:, None, :]).reshape(-1, 3)
                best = min(best, float(tree.query(queries, k=1)[0].min()))
    return float(best)


def _frame_short_contact(
    positions: np.ndarray,
    numbers: np.ndarray,
    cell: np.ndarray,
    pbc: np.ndarray,
    min_distance: float | None,
    prepared: tuple | None = None,
) -> bool:
    """True when any atom pair — periodic images included — sits closer than
    SHORT_CONTACT_COEFFICIENT × the sum of its covalent radii (NepTrainKit's
    "find non-physical structures" bond-length filter).

    Exact under the same per-axis stencil cap as _frame_min_distance (the
    fractional-coefficient bound here is Cauchy–Schwarz on the inverse-cell
    columns, as in _frame_min_distance).
    The scan only runs when the frame's minimum distance still leaves room
    for a pair to undercut its own threshold — every threshold is at most
    coefficient × 2 × the largest radius present — so healthy frames pay a
    single comparison.
    """
    if min_distance is None:
        return False  # no atom pairs at all
    radii = radii_for(numbers)
    t_max = SHORT_CONTACT_COEFFICIENT * 2.0 * float(radii.max())
    # The comparison above is the whole cost for a healthy frame; preparation and
    # the tree are only paid by one that can still hold a violating pair.
    if not t_max > 0.0 or min_distance >= t_max:
        return False
    pts, periodic, tree = prepared if prepared is not None else _geometry_input(positions, cell, pbc)
    if pts is None:
        return False
    n = pts.shape[0]
    cell = np.asarray(cell, dtype=np.float64)
    pbc = np.asarray(pbc, dtype=bool)
    if periodic:
        # every pair (i, j+s) closer than t_max satisfies |s_k| <= t_max *
        # ||inv column_k|| + 1 per periodic axis (|Δfrac_k| >= |s_k| - 1 for the
        # wrapped spread), so this stencil covers all candidates; the zero
        # shift (core-core pairs) is scanned first with exact self-pairs
        # dropped, while image shifts keep own-image contacts (i == j)
        inverse = np.linalg.inv(cell)
        limits = [
            min(
                _MIN_DISTANCE_IMAGE_LIMIT,
                int(t_max * np.linalg.norm(inverse[:, axis])) + 1,
            )
            if pbc[axis]
            else 0
            for axis in range(3)
        ]
        shifts = [
            np.asarray(s, dtype=np.float64)
            for s in product(*[range(-l, l + 1) for l in limits])
            if any(s)
        ]
    else:
        shifts = []

    batch_i: list[np.ndarray] = []
    batch_j: list[np.ndarray] = []
    batch_pairs = 0

    def _flush(centers: np.ndarray) -> bool:
        nonlocal batch_i, batch_j, batch_pairs
        query_atom = np.concatenate(batch_i)
        hit_atom = np.concatenate(batch_j)
        batch_i, batch_j, batch_pairs = [], [], 0
        diff = centers[query_atom] - pts[hit_atom]
        dist_sq = np.einsum("ij,ij->i", diff, diff)
        bound = SHORT_CONTACT_COEFFICIENT * (radii[query_atom] + radii[hit_atom])
        return bool((dist_sq < bound * bound).any())

    def _scan(centers: np.ndarray, drop_self: bool) -> bool:
        nonlocal batch_pairs
        for start in range(0, n, _SHORT_CONTACT_ATOM_CHUNK):
            balls = tree.query_ball_point(
                centers[start : start + _SHORT_CONTACT_ATOM_CHUNK],
                t_max,
                return_sorted=False,
            )
            for offset, hits in enumerate(balls):
                if not hits:
                    continue
                i = start + offset
                if drop_self:
                    hits = [j for j in hits if j != i]
                    if not hits:
                        continue
                batch_i.append(np.full(len(hits), i, dtype=np.int64))
                batch_j.append(np.asarray(hits, dtype=np.int64))
                batch_pairs += len(hits)
            if batch_pairs >= _SHORT_CONTACT_PAIR_BATCH and _flush(centers):
                return True
        return bool(batch_i) and _flush(centers)

    if _scan(pts, drop_self=True):  # zero shift: core-core pairs
        return True
    for shift in shifts:  # image pairs; i == j is a real own-image contact
        if _scan(pts + shift @ cell, drop_self=False):
            return True
    return False


def _frame_hash(numbers: np.ndarray, positions: np.ndarray, cell: np.ndarray) -> str:
    """Content hash for exact-duplicate detection (structure only, no labels;
    byte order is native, hashes are only compared within one scan)."""
    h = hashlib.blake2b(digest_size=16)
    h.update(np.ascontiguousarray(numbers, dtype=np.int64).tobytes())
    h.update(np.ascontiguousarray(positions, dtype=np.float64).tobytes())
    h.update(np.ascontiguousarray(cell, dtype=np.float64).tobytes())
    return h.hexdigest()


def _hill_order(counts: Counter[str]) -> list[str]:
    """Hill notation element order: C, H, then alphabetical; pure-alphabetical
    when no carbon."""
    if "C" in counts:
        return sorted(counts, key=lambda s: (s != "C", s != "H", s))
    return sorted(counts)


def _hill_formula(counts: Counter[str]) -> str:
    """Hill-notation formula from per-element atom counts. Actual atom counts —
    no ratio reduction, C2H4 and C4H8 are different species."""
    return "".join(f"{s}{counts[s]}" if counts[s] > 1 else s for s in _hill_order(counts))




def _int_hist(values: list[int]) -> dict | None:
    """Histogram with one bin per integer value.

    The atom-count chart labels each bin by its integer value, so falling back
    to a regular 40-bin histogram for a wide range would make a bin containing
    C64 appear as (for example) C59.  Keep these bins discrete even when the
    range is wide; the frontend can then map every bar back to the exact atom
    count.
    """
    arr = np.asarray(values, dtype=np.int64)
    if arr.size == 0:
        return None
    lo, hi = int(arr.min()), int(arr.max())
    counts = np.bincount(arr - lo, minlength=hi - lo + 1)
    edges = [float(lo - 0.5 + i) for i in range(hi - lo + 2)]
    return {"edges": [round(e, 6) for e in edges], "counts": [int(c) for c in counts]}


def finite_or_none(value: float | None) -> float | None:
    """A number that can be stored and sent, or None. The owner of that rule.

    `protocol.frames.encode` refuses a non-finite float rather than shipping a
    bare `NaN` token the renderer's JSON.parse rejects, so a producer that
    reaches the boundary with one has already lost the request. It is worse when
    the value is *persisted* first: `json.dumps` writes `NaN` happily, and every
    later read of that already-COMPLETED row then fails to encode - the result
    stays unreadable forever while the row keeps claiming success. Reporting the
    number as absent is the honest answer, and the UI already renders None as "—".
    """
    return None if value is not None and not bool(np.isfinite(value)) else value


def frame_force_max(forces: np.ndarray | None) -> float | None:
    """Largest force magnitude on a frame, or None when it has none.

    The health findings and the PCA colour-by both report this number, and
    rounding in only one of them made the two views disagree in the last digit.
    A frame whose forces are non-finite has no largest magnitude either: NaN
    propagates through `norm().max()`, and persisting that makes the row
    permanently unreadable.
    """
    if forces is None or not np.asarray(forces).size:
        return None
    maximum = np.linalg.norm(np.asarray(forces, dtype=np.float64), axis=1).max()
    return finite_or_none(round(float(maximum), 5))


def frame_energy_per_atom(energy: float | None, natoms: int) -> float | None:
    """Per-atom energy, or None when the frame has none or it is not a number.

    Same rule and same reason as `frame_force_max`: the colour-by merge and the
    frame inspector both derive this, and rounding in only one of them made those
    two views disagree in the last digit.
    """
    if energy is None or not natoms:
        return None
    return finite_or_none(round(float(energy) / natoms, 6))


def _frame_geometry(
    positions: np.ndarray, numbers: np.ndarray, cell: np.ndarray, pbc: np.ndarray
) -> tuple[float | None, bool]:
    """Minimum interatomic distance and short-contact flag in one pass.

    The compiled kernel (datasets/_native) fuses what used to be two cKDTree
    passes per frame — the dominant cost of the Overview/Health statistics —
    and falls back to the scipy reference implementation below when it is not
    available, so results never depend on the faster path being present.
    """
    fast = _native.frame_geometry(
        positions,
        numbers,
        cell,
        pbc,
        _RADII_TABLE,
        SHORT_CONTACT_COEFFICIENT,
        _CELL_DET_TOL,
        _MIN_DISTANCE_IMAGE_LIMIT,
    )
    if fast is not None:
        return fast
    prepared = _geometry_input(positions, cell, pbc)
    min_d = _frame_min_distance(positions, cell, pbc, prepared)
    return min_d, _frame_short_contact(positions, numbers, cell, pbc, min_d, prepared)


def compute_statistics(adapter: DatasetAdapter) -> dict:
    natoms: list[float] = []
    energy_per_atom: list[float] = []
    magnitudes = _ForceMagnitudeCounts()
    max_force: list[float] = []
    min_distance: list[float] = []
    volumes: list[float] = []
    elements: Counter[str] = Counter()
    # structures grouped by their exact element combination (unary/binary/…)
    compositions: Counter[tuple[str, ...]] = Counter()
    # structures grouped by their exact stoichiometry (Hill-notation formula)
    formulas: dict[str, tuple[Counter[str], int]] = {}
    # per-element atom counts per structure (marginal of the formula), held as
    # value -> occurrences: over 250k structures and 12 elements the flat list
    # shape costs 100 MB live against 9 MB for the counts, and the histogram
    # below only ever needs the latter.
    element_counts: dict[str, Counter[int]] = {}
    pbc_set: set[tuple[bool, bool, bool]] = set()
    props = {"energy": False, "forces": False, "virial": False}
    # health panel (single pass alongside the histograms)
    prop_missing: Counter[str] = Counter()
    # (frame index, declared-property bits) — indices feed health_findings
    present_bits: list[tuple[int, int]] = []
    invalid_cell = 0
    energy_anomaly = 0
    extreme_force = 0
    net_force = 0
    nonphysical = 0
    # parallel to findings["nonphysical_structures"], capped with it below
    nonphysical_distances: list[float] = []
    # frame indices flagged per health check (capped in the return payload)
    findings: dict[str, list[int]] = {
        "missing_values": [],
        "energy_anomaly": [],
        "invalid_cell": [],
        "duplicate_structures": [],
        # parallel to duplicate_structures: the first-occurrence frame each
        # flagged copy repeats (same order, same cap, so the alignment holds)
        "duplicate_structures_of": [],
        "extreme_force": [],
        "nonphysical_structures": [],
        "net_force": [],
    }
    first_seen_hash: dict[str, int] = {}

    for frame in adapter.iter_frames():
        pos = int(frame.index)  # original file position (adapters set it)
        n = int(frame.numbers.size)
        natoms.append(float(n))
        symbols = [_Z_LOOKUP.get(int(z), f"Z{z}") for z in frame.numbers]
        counts = Counter(symbols)
        elements.update(counts)
        compositions[tuple(sorted(set(symbols)))] += 1
        formula = _hill_formula(counts)
        if formula in formulas:
            prev_counts, prev_count = formulas[formula]
            formulas[formula] = (prev_counts, prev_count + 1)
        else:
            formulas[formula] = (counts, 1)
        for sym, c in counts.items():
            element_counts.setdefault(sym, Counter())[c] += 1
        pbc_set.add(tuple(bool(v) for v in frame.pbc))
        bits = 0
        if frame.energy is None:
            prop_missing["energy"] += 1
        else:
            bits |= 1
            props["energy"] = True
            frame_energy_per_atom = float(frame.energy) / max(n, 1)
            energy_per_atom.append(frame_energy_per_atom)
            if frame_energy_per_atom >= 0.0:
                energy_anomaly += 1
                findings["energy_anomaly"].append(pos)
        if frame.forces is None:
            prop_missing["forces"] += 1
        else:
            bits |= 2
            props["forces"] = True
            mags = np.linalg.norm(frame.forces, axis=1)
            peak = float(mags.max()) if mags.size else 0.0
            magnitudes.add(mags)
            max_force.append(peak)
            if peak > EXTREME_FORCE_EV_A:
                extreme_force += 1
                findings["extreme_force"].append(pos)
            # net force ‖ΣF‖: physically balanced labels sum to ~0 (NaN forces
            # yield NaN here and compare False, i.e. skipped — as in NepTrainKit)
            if float(np.linalg.norm(np.sum(frame.forces, axis=0))) > NET_FORCE_EV_A:
                net_force += 1
                findings["net_force"].append(pos)
        if frame.virial is None:
            prop_missing["virial"] += 1
        else:
            bits |= 4
            props["virial"] = True
        present_bits.append((pos, bits))
        cell = np.asarray(frame.cell, dtype=np.float64)
        finite = bool(np.isfinite(cell).all())
        det = abs(float(np.linalg.det(cell))) if finite else 0.0
        volumes.append(det)
        if any(bool(v) for v in frame.pbc) and (not finite or det <= _CELL_DET_TOL):
            invalid_cell += 1
            findings["invalid_cell"].append(pos)
        min_d, frame_nonphysical = _frame_geometry(
            frame.positions, frame.numbers, frame.cell, frame.pbc
        )
        if min_d is not None:
            min_distance.append(min_d)
        if frame_nonphysical:
            nonphysical += 1
            findings["nonphysical_structures"].append(pos)
            # The drawer shows the shortest distance of each flagged frame. It
            # used to run the neighbour search again per row on every open, for
            # what this pass already knows: a frame is only flagged from that
            # distance, so it is never None here. Rounded, because the fused
            # kernel and the scipy reference sum the squares in a different
            # order - tests/test_native_stats.py compares the two build's stats
            # for equality, and the drawer rounds to five digits anyway.
            nonphysical_distances.append(round(float(min_d), 6))
        content_hash = _frame_hash(frame.numbers, frame.positions, cell)
        if content_hash in first_seen_hash:
            # extra copy beyond the first occurrence
            findings["duplicate_structures"].append(pos)
            findings["duplicate_structures_of"].append(first_seen_hash[content_hash])
        else:
            first_seen_hash[content_hash] = pos

    # a property counts as "declared" when any frame carries it; frames lacking
    # a declared property are the missing values (across all properties)
    declared_mask = 0
    for name, bit in (("energy", 1), ("forces", 2), ("virial", 4)):
        if len(natoms) - prop_missing[name] > 0:
            declared_mask |= bit
    missing_indices = [pos for pos, bits in present_bits if declared_mask & ~bits]
    missing_values = len(missing_indices)
    findings["missing_values"] = missing_indices
    # frames missing each property the check watches (declared properties only;
    # undeclared ones are absent from every frame and never count as missing)
    missing_by_property = {
        name: int(prop_missing[name])
        for name, bit in (("energy", 1), ("forces", 2), ("virial", 4))
        if declared_mask & bit
    }
    duplicates = len(findings["duplicate_structures"])

    periodicity = pbc_summary(pbc_set)
    return {
        "stats_version": STATS_VERSION,
        "structures": int(len(natoms)),
        "atoms_total": int(sum(natoms)),
        "elements": [
            {"symbol": s, "count": int(c)} for s, c in sorted(elements.items())
        ],
        # sorted by arity, then count desc, so equal-arity entries are contiguous
        "compositions": [
            {"elements": list(elems), "count": int(c)}
            for elems, c in sorted(
                compositions.items(), key=lambda kv: (len(kv[0]), -kv[1], kv[0])
            )
        ],
        # exact stoichiometry, most common first
        "formulas": [
            {"formula": f, "elements": _hill_order(counts), "count": int(c)}
            for f, (counts, c) in sorted(
                formulas.items(), key=lambda kv: (-kv[1][1], kv[0])
            )
        ],
        # per-element atom-count distribution over structures; structures
        # lacking an element contribute a 0 bin
        "element_atom_counts": {
            # A structure without the element counts as a zero, so the padding
            # stays explicit; only the running accumulation changed shape.
            sym: _int_hist([*counter.elements(), *([0] * (len(natoms) - sum(counter.values())))])
            for sym, counter in sorted(element_counts.items())
        },
        "atoms_per_structure": _hist(natoms),
        "atoms_per_structure_summary": _summary(natoms),
        "energy_per_atom": _hist(energy_per_atom),
        "energy_per_atom_summary": _summary(energy_per_atom),
        "force_magnitude": magnitudes.histogram(),
        "force_magnitude_summary": magnitudes.summary(),
        "max_force": _hist(max_force),
        "max_force_summary": _summary(max_force),
        "min_distance": _hist(min_distance),
        "min_distance_summary": _summary(min_distance),
        "volume": _hist(volumes),
        "volume_summary": _summary(volumes),
        "properties": {
            "energy": {"per_structure": props["energy"], "per_atom": False},
            "forces": {"per_atom": props["forces"]},
            "virial": {"per_structure": props["virial"]},
        },
        "periodicity": periodicity,
        "health": {
            # all counts are frames; percentages are frontend-side / structures
            "missing_values": missing_values,
            # per-property breakdown of missing_values (declared properties only)
            "missing_by_property": missing_by_property,
            "energy_anomaly": energy_anomaly,
            "invalid_cell": invalid_cell,
            "duplicate_structures": duplicates,
            "extreme_force": extreme_force,
            "extreme_force_threshold": EXTREME_FORCE_EV_A,
            "nonphysical_structures": nonphysical,
            "short_contact_coefficient": SHORT_CONTACT_COEFFICIENT,
            "net_force": net_force,
            "net_force_threshold": NET_FORCE_EV_A,
        },
        # frame indices behind the counts above (original file positions,
        # capped per check; a shorter list than its count means truncation;
        # duplicate_structures_of runs parallel to duplicate_structures)
        "health_findings": {
            "cap": HEALTH_FINDINGS_CAP,
            **{k: v[:HEALTH_FINDINGS_CAP] for k, v in findings.items()},
            "nonphysical_distances": nonphysical_distances[:HEALTH_FINDINGS_CAP],
        },
    }
