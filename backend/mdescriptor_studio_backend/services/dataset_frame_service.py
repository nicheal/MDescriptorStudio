"""DatasetFrameService: per-frame reads shaped for the Explore viewer.

Split out of DatasetService (design doc Epic-001): reading one frame, building
its XYZ text, atom table and boundary ghost images is a presentation concern and
needs nothing from the dataset lifecycle beyond a row and an adapter.
"""

from __future__ import annotations

import numpy as np

from ..datasets.ghosts import DEFAULT_BOND_CUTOFF, periodic_boundary_ghosts
from ..datasets.statistics import finite_or_none, frame_energy_per_atom, frame_force_max
from ..errors import AppError, INVALID_DATASET, INVALID_PARAMS
from .dataset_service import formula_of, symbol_of

MIN_BOND_CUTOFF = 0.1
MAX_BOND_CUTOFF = 10.0
MAX_INLINE_ATOMS = 20_000


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


def _atom_window(params: dict, total: int) -> tuple[int, int]:
    raw_offset = params.get("atom_offset", 0)
    raw_limit = params.get("atom_limit", MAX_INLINE_ATOMS)
    if (
        not isinstance(raw_offset, int)
        or isinstance(raw_offset, bool)
        or raw_offset < 0
        or not isinstance(raw_limit, int)
        or isinstance(raw_limit, bool)
        or raw_limit <= 0
    ):
        raise AppError(INVALID_PARAMS, "'atom_offset' and 'atom_limit' must be positive integers")
    offset = min(raw_offset, total)
    return offset, min(offset + min(raw_limit, MAX_INLINE_ATOMS), total)


def _atom_rows(symbols: list[str], positions: np.ndarray, forces, start: int, stop: int) -> list[dict]:
    rows = []
    for i in range(start, stop):
        pos = positions[i]
        entry = {
            "i": i,
            "el": symbols[i],
            "x": round(float(pos[0]), 5),
            "y": round(float(pos[1]), 5),
            "z": round(float(pos[2]), 5),
            "fx": None,
            "fy": None,
            "fz": None,
            "f": None,
        }
        if forces is not None:
            # A frame whose model returned NaN forces is still a structure
            # worth drawing: report the components as absent rather than
            # failing the whole read at `frames.encode(allow_nan=False)`.
            fx, fy, fz = (finite_or_none(round(float(v), 5)) for v in forces[i])
            magnitude = None if fx is None or fy is None or fz is None else round((fx * fx + fy * fy + fz * fz) ** 0.5, 5)
            entry.update(fx=fx, fy=fy, fz=fz, f=magnitude)
        rows.append(entry)
    return rows


class DatasetFrameService:
    def __init__(self, datasets):
        self.datasets = datasets

    def _load_frame(self, params: dict):
        ds_id, index = params.get("id"), params.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise AppError(INVALID_PARAMS, "'index' (int) is required")
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
        return row, f, symbols, positions

    def _frame_details(self, params: dict):
        row, f, symbols, positions = self._load_frame(params)
        bond_cutoff = _bond_cutoff(params.get("bond_cutoff"))
        cell = np.asarray(f.cell)
        det = abs(float(np.linalg.det(cell)))
        pbc = np.asarray(f.pbc, dtype=bool).reshape(3)
        periodic = bool(pbc.any() and det > 1e-8)
        volume = det if det > 1e-8 else None
        return row, f, symbols, positions, bond_cutoff, cell, pbc, periodic, volume

    @staticmethod
    def _summary_payload(row, f, symbols, cell, pbc, periodic, volume, bond_cutoff) -> dict:
        virial = None
        if f.virial is not None:
            values = np.asarray(f.virial, dtype=np.float64).reshape(-1)
            if np.isfinite(values).all():
                virial = [round(float(value), 6) for value in values]
        energy = finite_or_none(f.energy)
        return {
            "index": f.index,
            "natoms": len(symbols),
            "formula": formula_of(symbols),
            "energy": energy,
            "energy_per_atom": frame_energy_per_atom(energy, len(symbols)),
            "force_max": frame_force_max(f.forces) if f.forces is not None else None,
            "virial_present": virial is not None,
            "virial": virial,
            "volume": round(volume, 4) if volume else None,
            "pbc": "".join("XYZ"[i] for i, value in enumerate(pbc) if value) or "—",
            "cell": cell.reshape(-1).tolist() if periodic else None,
            "atom_total": len(symbols),
            "atom_offset": 0,
            "atom_page_size": 0,
            "atom_rows_complete": False,
            "bond_cutoff": bond_cutoff,
        }

    @staticmethod
    def _geometry_payload(row, f, symbols, positions, cell, pbc, periodic, bond_cutoff) -> dict:
        display_count = min(len(symbols), MAX_INLINE_ATOMS)
        header = f"frame {f.index} of dataset {row['name']}"
        if periodic:
            lat = " ".join(f"{value:.6f}" for value in cell.reshape(-1))
            flags = " ".join("T" if value else "F" for value in pbc)
            header += f' Lattice="{lat}" pbc="{flags}"'
        # Do not allocate a periodic-image stencil for a capped geometry. The
        # viewer can render the bounded real-atom geometry; full ghost padding
        # is only safe when the whole frame is in this response.
        ghosts = (
            periodic_boundary_ghosts(symbols, positions, cell, cutoff=bond_cutoff, pbc=pbc)
            if periodic and display_count == len(symbols) else []
        )
        display_symbols = symbols[:display_count] + [ghost[0] for ghost in ghosts]
        display_positions = (
            np.vstack([positions[:display_count], np.array([ghost[1] for ghost in ghosts])])
            if ghosts else positions[:display_count]
        )
        if ghosts:
            header += f" +{len(ghosts)} periodic images"
        xyz_lines = [str(len(display_symbols)), header]
        for symbol, position in zip(display_symbols, display_positions):
            xyz_lines.append(f"{symbol} {position[0]:.6f} {position[1]:.6f} {position[2]:.6f}")
        return {
            "xyz": "\n".join(xyz_lines),
            "ghost_count": len(ghosts),
            "ghost_parents": [ghost[2] for ghost in ghosts],
            "bond_cutoff": bond_cutoff,
            "geometry_atom_count": display_count,
            "geometry_complete": display_count == len(symbols),
        }

    def frame(self, params: dict) -> dict:
        row, f, symbols, positions, bond_cutoff, cell, pbc, periodic, volume = self._frame_details(params)
        atom_offset, atom_end = _atom_window(params, len(symbols))
        rows = _atom_rows(symbols, positions, f.forces, atom_offset, atom_end)
        return {
            **self._summary_payload(row, f, symbols, cell, pbc, periodic, volume, bond_cutoff),
            **self._geometry_payload(row, f, symbols, positions, cell, pbc, periodic, bond_cutoff),
            "atom_rows": rows,
            "atom_offset": atom_offset,
            "atom_page_size": atom_end - atom_offset,
            "atom_total": len(symbols),
            "atom_rows_complete": atom_offset == 0 and atom_end == len(symbols),
        }

    def frame_summary(self, params: dict) -> dict:
        """Return frame metadata without geometry or atom-table payloads."""
        row, f, symbols, _positions, bond_cutoff, cell, pbc, periodic, volume = self._frame_details(params)
        return self._summary_payload(row, f, symbols, cell, pbc, periodic, volume, bond_cutoff)

    def frame_geometry(self, params: dict) -> dict:
        """Return bounded viewer geometry separately from frame metadata."""
        _row, f, symbols, positions, bond_cutoff, cell, pbc, periodic, _volume = self._frame_details(params)
        return {"index": f.index, **self._geometry_payload(_row, f, symbols, positions, cell, pbc, periodic, bond_cutoff)}

    def frame_atoms(self, params: dict) -> dict:
        """Return one bounded atom-table page without rebuilding an XYZ payload."""
        _row, f, symbols, positions = self._load_frame(params)
        start, stop = _atom_window(params, len(symbols))
        return {
            "index": f.index,
            "atom_offset": start,
            "atom_page_size": stop - start,
            "atom_total": len(symbols),
            "atom_rows_complete": start == 0 and stop == len(symbols),
            "atom_rows": _atom_rows(symbols, positions, f.forces, start, stop),
        }
