"""DatasetFrameService: per-frame reads shaped for the Explore viewer.

Split out of DatasetService (design doc Epic-001): reading one frame, building
its XYZ text, atom table and boundary ghost images is a presentation concern and
needs nothing from the dataset lifecycle beyond a row and an adapter.
"""

from __future__ import annotations

import numpy as np

from ..datasets.ghosts import DEFAULT_BOND_CUTOFF, periodic_boundary_ghosts
from ..datasets.statistics import frame_force_max
from ..errors import AppError, INVALID_DATASET, INVALID_PARAMS
from .dataset_service import formula_of, symbol_of

MIN_BOND_CUTOFF = 0.1
MAX_BOND_CUTOFF = 10.0


def _bond_cutoff(value: object) -> float:
    """Validate the optional display-bond cutoff sent by Explore."""
    if value is None:
        return DEFAULT_BOND_CUTOFF
    if isinstance(value, bool):
        raise AppError(INVALID_PARAMS, "'bond_cutoff' must be a finite number")
    try:
        cutoff = float(value)
    except (TypeError, ValueError) as exc:
        raise AppError(INVALID_PARAMS, "'bond_cutoff' must be a finite number") from exc
    if not np.isfinite(cutoff) or not MIN_BOND_CUTOFF <= cutoff <= MAX_BOND_CUTOFF:
        raise AppError(
            INVALID_PARAMS,
            f"'bond_cutoff' must be between {MIN_BOND_CUTOFF} and {MAX_BOND_CUTOFF} Å",
        )
    return cutoff


class DatasetFrameService:
    def __init__(self, datasets):
        self.datasets = datasets

    def frame(self, params: dict) -> dict:
        ds_id, index = params.get("id"), params.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise AppError(INVALID_PARAMS, "'index' (int) is required")
        bond_cutoff = _bond_cutoff(params.get("bond_cutoff"))
        row = self.datasets.row_or_raise(ds_id)
        adapter = self.datasets.adapter_for(row)
        try:
            f = adapter.get_frame(index)
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001 - parse/data errors are dataset faults
            raise AppError(INVALID_DATASET, f"cannot read frame {index}: {exc}") from exc
        symbols = [symbol_of(z) for z in f.numbers.tolist()]
        positions = np.asarray(f.positions)
        rows = []
        for i, (s, pos) in enumerate(zip(symbols, positions)):
            entry = {
                "i": i,
                "el": s,
                "x": round(float(pos[0]), 5),
                "y": round(float(pos[1]), 5),
                "z": round(float(pos[2]), 5),
                "fx": None,
                "fy": None,
                "fz": None,
                "f": None,
            }
            if f.forces is not None:
                fx, fy, fz = (float(v) for v in f.forces[i])
                entry.update(
                    fx=round(fx, 5), fy=round(fy, 5), fz=round(fz, 5),
                    f=round((fx * fx + fy * fy + fz * fz) ** 0.5, 5),
                )
            rows.append(entry)
        cell = np.asarray(f.cell)
        det = abs(float(np.linalg.det(cell)))
        periodic = bool(np.all(np.asarray(f.pbc)) and det > 1e-8)
        volume = det if det > 1e-8 else None
        header = f"frame {index} of dataset {row['name']}"
        if periodic:
            lat = " ".join(f"{v:.6f}" for v in cell.reshape(-1))
            header += f' Lattice="{lat}"'
        # periodic images appended to the SAME model so cross-boundary bonds form
        ghosts = periodic_boundary_ghosts(symbols, positions, cell, cutoff=bond_cutoff) if periodic else []
        display_symbols = symbols + [g[0] for g in ghosts]
        display_positions = (
            np.vstack([positions, np.array([g[1] for g in ghosts])]) if ghosts else positions
        )
        if ghosts:
            header += f" +{len(ghosts)} periodic images"
        xyz_lines = [str(len(display_symbols)), header]
        for s, pos in zip(display_symbols, display_positions):
            xyz_lines.append(f"{s} {pos[0]:.6f} {pos[1]:.6f} {pos[2]:.6f}")
        energy_per_atom = None
        if f.energy is not None and symbols:
            energy_per_atom = round(float(f.energy) / len(symbols), 6)
        force_max = frame_force_max(f.forces) if f.forces is not None else None
        # row-major 3×3 as stored by the source (extxyz comment / deepmd set);
        # sign conventions differ between ecosystems, so the GUI shows it raw
        virial = None
        if f.virial is not None:
            virial = [round(float(v), 6) for v in np.asarray(f.virial, dtype=np.float64).reshape(-1)]
        return {
            "index": index,
            "natoms": len(symbols),
            "formula": formula_of(symbols),
            "xyz": "\n".join(xyz_lines),
            "atom_rows": rows,
            "energy": f.energy,
            "energy_per_atom": energy_per_atom,
            "force_max": force_max,
            "virial_present": f.virial is not None,
            "virial": virial,
            "volume": round(volume, 4) if volume else None,
            "pbc": "".join("XYZ"[i] for i, v in enumerate(f.pbc) if v) or "—",
            "cell": cell.reshape(-1).tolist() if periodic else None,
            "ghost_count": len(ghosts),
            "ghost_parents": [g[2] for g in ghosts],
            "bond_cutoff": bond_cutoff,
        }
