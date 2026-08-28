"""Streaming dataset statistics (design doc §14); histograms pre-binned so raw
arrays never cross the IPC boundary (Rule 6)."""

from __future__ import annotations

from collections import Counter

import numpy as np

from .base import DatasetAdapter, pbc_summary
from .deepmd import _Z_TO_SYMBOL as _Z_LOOKUP

BINS = 40


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


def compute_statistics(adapter: DatasetAdapter) -> dict:
    natoms: list[float] = []
    energy_per_atom: list[float] = []
    force_magnitudes: list[np.ndarray] = []
    max_force: list[float] = []
    volumes: list[float] = []
    elements: Counter[str] = Counter()
    pbc_set: set[tuple[bool, bool, bool]] = set()
    props = {"energy": False, "forces": False, "virial": False}

    for frame in adapter.iter_frames():
        n = int(frame.numbers.size)
        natoms.append(float(n))
        elements.update(s for s in (_Z_LOOKUP.get(int(z), f"Z{z}") for z in frame.numbers))
        pbc_set.add(tuple(bool(v) for v in frame.pbc))
        if frame.energy is not None:
            props["energy"] = True
            energy_per_atom.append(float(frame.energy) / max(n, 1))
        if frame.forces is not None:
            props["forces"] = True
            mags = np.linalg.norm(frame.forces, axis=1)
            force_magnitudes.append(mags)
            max_force.append(float(mags.max()) if mags.size else 0.0)
        if frame.virial is not None:
            props["virial"] = True
        det = abs(float(np.linalg.det(np.asarray(frame.cell))))
        volumes.append(det)

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
    }
