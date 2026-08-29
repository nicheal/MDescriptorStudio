"""DatasetService: registry, scan+statistics job, cached statistics, frames."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ..datasets import (
    compute_fingerprint,
    compute_statistics,
    create_adapter,
    detect_format,
)
from ..errors import (
    AppError,
    DATASET_CHANGED,
    DATASET_NOT_FOUND,
    INVALID_DATASET,
    INVALID_PARAMS,
)
from ..mdescriptor_adapter import EngineAdapter
from ..storage.database import Database
from .job_service import JobService

log = logging.getLogger(__name__)

_NOW = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731


def _symbol(z: int) -> str:
    from ..datasets.deepmd import _Z_TO_SYMBOL

    return _Z_TO_SYMBOL.get(int(z), f"Z{z}")


def formula_of(symbols: list[str]) -> str:
    counts: dict[str, int] = {}
    for s in symbols:
        counts[s] = counts.get(s, 0) + 1
    return "".join(
        f"{sym}{n}" for sym, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )


class DatasetService:
    def __init__(self, db: Database, adapter: EngineAdapter, jobs: JobService):
        self.db = db
        self.adapter = adapter
        self.jobs = jobs
        self._adapters: dict[str, object] = {}

    # -- helpers -----------------------------------------------------------
    def _row(self, dataset_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (dataset_id,))
        if row is None:
            raise AppError(DATASET_NOT_FOUND, f"dataset {dataset_id} does not exist")
        return row

    def _meta(self, row: dict, fingerprint_valid: bool | None = None) -> dict:
        current = compute_fingerprint(Path(row["source_path"]), row["number_of_frames"])
        return {
            "id": row["id"],
            "name": row["name"],
            "format": row["format"],
            "source_path": row["source_path"],
            "number_of_frames": row["number_of_frames"],
            "elements": json.loads(row["elements"]),
            "properties": json.loads(row["properties"]),
            "periodicity": json.loads(row["periodicity"]),
            "fingerprint": row["fingerprint"],
            "file_size": row["file_size"],
            "created_at": row["created_at"],
            "cache_valid": fingerprint_valid if fingerprint_valid is not None else current == row["fingerprint"],
        }

    def _adapter_for(self, row: dict):
        cached = self._adapters.get(row["id"])
        if cached is None:
            cached = create_adapter(Path(row["source_path"]), row["format"])
            self._adapters[row["id"]] = cached
        return cached

    # -- IPC methods -----------------------------------------------------------
    def list(self, params: dict) -> list[dict]:
        rows = self.db.query("SELECT * FROM datasets ORDER BY created_at")
        return [self._meta(r) for r in rows]

    def register(self, params: dict) -> dict:
        raw_path = params.get("path")
        if not raw_path or not isinstance(raw_path, str):
            raise AppError(INVALID_PARAMS, "'path' (string) is required")
        path = Path(raw_path)
        if not path.exists():
            raise AppError(INVALID_DATASET, f"path does not exist: {path}")
        try:
            fmt = params.get("format") or detect_format(path)
        except AppError:
            raise
        name = params.get("name") or path.stem or path.name
        dup = self.db.query_one("SELECT id FROM datasets WHERE source_path = ?", (str(path),))
        if dup:
            raise AppError(INVALID_DATASET, f"already registered: {path}", {"dataset_id": dup["id"]})

        def runner(ctx):
            ctx.progress(0, 1, "scanning dataset")
            adapter = create_adapter(path, fmt)
            scan = adapter.scan()
            total = max(scan.number_of_frames, 1)
            # statistics pass drives progress
            frames_seen = 0

            def counting_iter():
                nonlocal frames_seen
                for frame in adapter.iter_frames():
                    frames_seen += 1
                    if frames_seen % 250 == 0 or frames_seen == total:
                        ctx.progress(frames_seen, total, "computing statistics")
                    yield frame

            class _CountingAdapter:
                format_name = adapter.format_name
                source_path = adapter.source_path

                def __len__(self):
                    return len(adapter)

                def get_frame(self, index):
                    return adapter.get_frame(index)

                def iter_frames(self):
                    return counting_iter()

            stats = compute_statistics(_CountingAdapter())
            ctx.check_cancelled()
            fingerprint = compute_fingerprint(path, scan.number_of_frames)
            ds_id = f"ds_{uuid.uuid4().hex[:12]}"
            if not stats["elements"] and scan.elements:
                elements = scan.elements
            elif scan.elements:
                elements = sorted(set(scan.elements) | {e["symbol"] for e in stats["elements"]})
            else:
                elements = [e["symbol"] for e in stats["elements"]]
            periodicity = scan.periodicity if scan.periodicity["flags"] else stats["periodicity"]
            self.db.execute(
                "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements,"
                " properties, periodicity, fingerprint, file_size, created_at, last_scan_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ds_id,
                    name,
                    fmt,
                    str(path),
                    scan.number_of_frames,
                    json.dumps(elements),
                    json.dumps(stats["properties"]),
                    json.dumps(periodicity),
                    fingerprint,
                    scan.file_size,
                    _NOW(),
                    _NOW(),
                ),
            )
            self.db.execute(
                "INSERT INTO dataset_statistics (dataset_id, fingerprint, stats_json, created_at)"
                " VALUES (?, ?, ?, ?)",
                (ds_id, fingerprint, json.dumps(stats), _NOW()),
            )
            ctx.progress(total, total, "done")
            return {"dataset_id": ds_id, "number_of_frames": scan.number_of_frames}

        job_id = self.jobs.submit("dataset.register", runner)
        return {"job_id": job_id}

    def remove(self, params: dict) -> dict:
        ds_id = params.get("id")
        self._row(ds_id)
        self.db.execute("DELETE FROM datasets WHERE id = ?", (ds_id,))
        self._adapters.pop(ds_id, None)
        return {"ok": True}

    def get(self, params: dict) -> dict:
        row = self._row(params.get("id"))
        meta = self._meta(row)
        stats_row = self.db.query_one(
            "SELECT stats_json FROM dataset_statistics WHERE dataset_id = ?", (row["id"],)
        )
        meta["stats"] = json.loads(stats_row["stats_json"]) if stats_row else None
        return meta

    def statistics(self, params: dict) -> dict:
        row = self._row(params.get("id"))
        current = compute_fingerprint(Path(row["source_path"]), row["number_of_frames"])
        stats_row = self.db.query_one(
            "SELECT fingerprint, stats_json FROM dataset_statistics WHERE dataset_id = ?",
            (row["id"],),
        )
        if stats_row and stats_row["fingerprint"] == current:
            return {"recalculating": False, "job_id": None, "stats": json.loads(stats_row["stats_json"])}
        job_id = self._recompute(row["id"])
        return {"recalculating": True, "job_id": job_id, "stats": None}

    def _recompute(self, ds_id: str) -> str:
        row = self._row(ds_id)

        def runner(ctx):
            adapter = self._adapter_for(row)
            total = max(len(adapter), 1)
            count = 0

            def counting_iter():
                nonlocal count
                for frame in adapter.iter_frames():
                    count += 1
                    if count % 250 == 0 or count == total:
                        ctx.progress(count, total, "computing statistics")
                    yield frame

            class _CountingAdapter:
                format_name = adapter.format_name
                source_path = adapter.source_path

                def __len__(self):
                    return len(adapter)

                def get_frame(self, index):
                    return adapter.get_frame(index)

                def iter_frames(self):
                    return counting_iter()

            stats = compute_statistics(_CountingAdapter())
            fingerprint = compute_fingerprint(Path(row["source_path"]), len(adapter))
            self.db.execute(
                "INSERT INTO dataset_statistics (dataset_id, fingerprint, stats_json, created_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(dataset_id) DO UPDATE SET fingerprint = excluded.fingerprint,"
                " stats_json = excluded.stats_json, created_at = excluded.created_at",
                (ds_id, fingerprint, json.dumps(stats), _NOW()),
            )
            self.db.execute(
                "UPDATE datasets SET number_of_frames = ?, last_scan_at = ? WHERE id = ?",
                (len(adapter), _NOW(), ds_id),
            )
            return {"dataset_id": ds_id}

        return self.jobs.submit("dataset.statistics", runner, dataset_id=ds_id)

    def refresh_if_changed(self, row: dict) -> None:
        current = compute_fingerprint(Path(row["source_path"]), row["number_of_frames"])
        if current != row["fingerprint"]:
            raise AppError(
                DATASET_CHANGED,
                f"dataset changed on disk: {row['source_path']}",
                {"dataset_id": row["id"]},
            )

    # -- frame access (M2) --------------------------------------------------
    def frame(self, params: dict) -> dict:
        ds_id, index = params.get("id"), params.get("index")
        if not isinstance(index, int):
            raise AppError(INVALID_PARAMS, "'index' (int) is required")
        row = self._row(ds_id)
        adapter = self._adapter_for(row)
        f = adapter.get_frame(index)
        symbols = [_symbol(z) for z in f.numbers.tolist()]
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
        periodic = bool(np.all(np.asarray(f.pbc)) and np.abs(cell).sum() > 1e-8)
        volume = abs(float(np.linalg.det(cell))) if np.abs(cell).sum() > 1e-8 else None
        header = f"frame {index} of dataset {row['name']}"
        if periodic:
            lat = " ".join(f"{v:.6f}" for v in cell.reshape(-1))
            header += f' Lattice="{lat}"'
        # periodic images appended to the SAME model so cross-boundary bonds form
        ghosts = periodic_boundary_ghosts(symbols, positions, cell) if periodic else []
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
        force_max = None
        if f.forces is not None and len(rows):
            vals = [r["f"] for r in rows if r["f"] is not None]
            force_max = round(max(vals), 5) if vals else None
        return {
            "index": index,
            "natoms": len(symbols),
            "formula": formula_of(symbols),
            "xyz": "\n".join(xyz_lines),
            "atom_rows": rows,
            "energy": f.energy,
            "energy_per_atom": energy_per_atom,
            "force_max": force_max,
            "volume": round(volume, 4) if volume else None,
            "pbc": "".join("XYZ"[i] for i, v in enumerate(f.pbc) if v) or "—",
            "cell": cell.reshape(-1).tolist() if periodic else None,
            "ghost_count": len(ghosts),
        }


def periodic_boundary_ghosts(
    symbols: list[str], positions: np.ndarray, cell: np.ndarray,
    cutoff: float = 2.4, max_ghosts: int = 3000,
) -> list[tuple[str, np.ndarray]]:
    """Periodic-image atoms near cell faces so cross-boundary bonds are complete.

    An atom contributes an image shifted by n (per axis: -1/0/+1) when its
    perpendicular distance to that cell face is below `cutoff` (VESTA-style
    boundary padding). Returns (element, position) pairs, capped at max_ghosts.
    """
    a_inv = np.linalg.inv(cell)
    frac = positions @ a_inv
    spacing = 1.0 / np.linalg.norm(a_inv, axis=0)  # interplanar distance per axis
    out: list[tuple[str, np.ndarray]] = []
    for i in range(len(symbols)):
        if len(out) > max_ghosts:
            break
        opts: list[list[int]] = []
        for ax in range(3):
            axis_opts = [0]
            if frac[i, ax] * spacing[ax] < cutoff:
                axis_opts.append(-1)
            if (1.0 - frac[i, ax]) * spacing[ax] < cutoff:
                axis_opts.append(1)
            opts.append(axis_opts)
        for dx in opts[0]:
            for dy in opts[1]:
                for dz in opts[2]:
                    if dx == dy == dz == 0:
                        continue
                    pos = positions[i] + np.array([dx, dy, dz], dtype=np.float64) @ cell
                    out.append((symbols[i], pos))
    return out[:max_ghosts]
