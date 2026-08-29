"""Streaming dataset statistics (design doc §14); histograms pre-binned so raw
arrays never cross the IPC boundary (Rule 6)."""

from __future__ import annotations

import hashlib
from collections import Counter

import numpy as np

from .base import DatasetAdapter, pbc_summary
from .deepmd_symbols import _Z_TO_SYMBOL as _Z_LOOKUP

BINS = 40
EXTREME_FORCE_EV_A = 50.0  # per-atom |F| above this flags the frame (health panel)
_CELL_DET_TOL = 1e-8


def _hist(values: list | np.ndarray) -> dict | None:
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return None
    counts, edges = np.histogram(arr, bins=BINS)
    return {
        "edges": [round(float(e), 6) for e in edges],
        "counts": [int(c) for c in counts],
    }


def _summary(values: list | np.ndarray) -> dict | None:
    arr = np.asarray(values, dtype=np.float64)
    if arr.size == 0:
        return None
    return {
        "min": round(float(arr.min()), 6),
        "max": round(float(arr.max()), 6),
        "mean": round(float(arr.mean()), 6),
        "median": round(float(np.median(arr)), 6),
    }


def _frame_hash(numbers: np.ndarray, positions: np.ndarray, cell: np.ndarray) -> str:
    """Content hash for exact-duplicate detection (structure only, no labels;
    byte order is native, hashes are only compared within one scan)."""
    h = hashlib.blake2b(digest_size=16)
    h.update(np.ascontiguousarray(numbers, dtype=np.int64).tobytes())
    h.update(np.ascontiguousarray(positions, dtype=np.float64).tobytes())
    h.update(np.ascontiguousarray(cell, dtype=np.float64).tobytes())
    return h.hexdigest()


def compute_statistics(adapter: DatasetAdapter) -> dict:
    natoms: list[float] = []
    energy_per_atom: list[float] = []
    force_magnitudes: list[np.ndarray] = []
    max_force: list[float] = []
    volumes: list[float] = []
    elements: Counter[str] = Counter()
    pbc_set: set[tuple[bool, bool, bool]] = set()
    props = {"energy": False, "forces": False, "virial": False}
    # health panel (single pass alongside the histograms)
    prop_missing: Counter[str] = Counter()
    present_bits: list[int] = []
    invalid_cell = 0
    extreme_force = 0
    frame_hashes: Counter[str] = Counter()

    for frame in adapter.iter_frames():
        n = int(frame.numbers.size)
        natoms.append(float(n))
        elements.update(s for s in (_Z_LOOKUP.get(int(z), f"Z{z}") for z in frame.numbers))
        pbc_set.add(tuple(bool(v) for v in frame.pbc))
        bits = 0
        if frame.energy is None:
            prop_missing["energy"] += 1
        else:
            bits |= 1
            props["energy"] = True
            energy_per_atom.append(float(frame.energy) / max(n, 1))
        if frame.forces is None:
            prop_missing["forces"] += 1
        else:
            bits |= 2
            props["forces"] = True
            mags = np.linalg.norm(frame.forces, axis=1)
            force_magnitudes.append(mags)
            max_force.append(float(mags.max()) if mags.size else 0.0)
            if mags.size and float(mags.max()) > EXTREME_FORCE_EV_A:
                extreme_force += 1
        if frame.virial is None:
            prop_missing["virial"] += 1
        else:
            bits |= 4
            props["virial"] = True
        present_bits.append(bits)
        cell = np.asarray(frame.cell, dtype=np.float64)
        finite = bool(np.isfinite(cell).all())
        det = abs(float(np.linalg.det(cell))) if finite else 0.0
        volumes.append(det)
        if any(bool(v) for v in frame.pbc) and (not finite or det <= _CELL_DET_TOL):
            invalid_cell += 1
        frame_hashes[_frame_hash(frame.numbers, frame.positions, cell)] += 1

    # a property counts as "declared" when any frame carries it; frames lacking
    # a declared property are the missing values (across all properties)
    declared_mask = 0
    for name, bit in (("energy", 1), ("forces", 2), ("virial", 4)):
        if len(natoms) - prop_missing[name] > 0:
            declared_mask |= bit
    missing_values = sum(1 for bits in present_bits if declared_mask & ~bits)
    duplicates = sum(c - 1 for c in frame_hashes.values() if c > 1)

    all_forces = (
        np.concatenate(force_magnitudes) if force_magnitudes else np.array([])
    )
    periodicity = pbc_summary(pbc_set)
    return {
        "structures": int(len(natoms)),
        "atoms_total": int(sum(natoms)),
        "elements": [
            {"symbol": s, "count": int(c)} for s, c in sorted(elements.items())
        ],
        "atoms_per_structure": _hist(natoms),
        "atoms_per_structure_summary": _summary(natoms),
        "energy_per_atom": _hist(energy_per_atom),
        "energy_per_atom_summary": _summary(energy_per_atom),
        "force_magnitude": _hist(all_forces),
        "force_magnitude_summary": _summary(all_forces),
        "max_force": _hist(max_force),
        "max_force_summary": _summary(max_force),
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
            "invalid_cell": invalid_cell,
            "duplicate_structures": duplicates,
            "extreme_force": extreme_force,
            "extreme_force_threshold": EXTREME_FORCE_EV_A,
        },
    }
