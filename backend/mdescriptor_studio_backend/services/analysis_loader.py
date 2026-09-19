"""Loading descriptor results and building sample matrices for analysis."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..analysis import AtomDescriptorMatrix, DescriptorMatrix, StructureDescriptorMatrix
from ..analysis.sampling import FeatureBlock
from ..errors import (
    ANALYSIS_INPUT_INVALID,
    ANALYSIS_STALE,
    AppError,
    INVALID_PARAMS,
    RESULT_INCOMPATIBLE,
)
from ..datasets.statistics import frame_force_max
from ..security import UnsafePathError, ensure_no_reparse_points
from .analysis_helpers import (
    PHYSICAL_BLOCKS,
    _cell_parameters,
    _composition_matrix,
    _descriptor_summary,
    _pool_rows,
    _require_finite,
)


class AnalysisDataMixin:
    """Descriptor-result loading and sample-matrix construction."""
    _PHYSICAL_BLOCKS = PHYSICAL_BLOCKS

    def _result_root(self, row: dict) -> Path:
        try:
            root = self.results._managed_result_path(str(row.get("id") or ""), row.get("result_path"))
            if not root.is_dir():
                raise OSError("descriptor result directory is missing")
            ensure_no_reparse_points(root)
            return root
        except (OSError, TypeError, ValueError, UnsafePathError) as exc:
            raise AppError(RESULT_INCOMPATIBLE, "descriptor result artifact is unavailable") from exc

    def _row_offsets(self, run_row: dict) -> np.ndarray | None:
        """Row-offsets file of a descriptor result, if present."""
        path = self._result_root(run_row)
        offsets_file = path / "row_offsets.npy"
        ensure_no_reparse_points(offsets_file)
        return np.asarray(np.load(offsets_file, allow_pickle=False), dtype=np.int64) if offsets_file.is_file() else None

    @staticmethod
    def _run_frame_values(run_row: dict, count: int) -> np.ndarray:
        if run_row.get("scope") == "frame":
            return np.full(count, int(run_row.get("frame_index") or 0), dtype=np.int64)
        return np.arange(count, dtype=np.int64)

    def _frame_properties(self, run_row: dict, n_points: int) -> list[dict]:
        """Energy/force/volume per frame for color-by (aligned to frame index)."""
        frame_scope = run_row["scope"] == "frame"
        need = max(n_points, (run_row["frame_index"] + 1) if frame_scope else n_points)
        props: list[dict] = [{"energy_per_atom": None, "force_max": None, "volume": None} for _ in range(need)]
        dataset_row = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset_row is None:
            return props
        adapter = self.datasets._adapter_for(dataset_row)
        indices = [run_row["frame_index"]] if frame_scope else list(range(min(n_points, len(adapter))))
        for i in indices:
            try:
                f = adapter.get_frame(i)
            except AppError:
                continue
            natoms = len(f.numbers)
            entry = {
                "energy_per_atom": f.energy / natoms if f.energy is not None and natoms else None,
                "force_max": None,
                "volume": None,
            }
            entry["force_max"] = frame_force_max(f.forces)
            det = abs(float(np.linalg.det(np.asarray(f.cell))))
            if det > 1e-8:
                entry["volume"] = round(det, 4)
            props[i] = entry
        return props

    def _input_ids(self, analysis_type: str, params: dict) -> list[str]:
        if analysis_type in ("coverage", "overlap", "acquisition", "drift"):
            ids = [params.get("reference_run_id"), params.get("query_run_id")]
        elif analysis_type == "fps" and params.get("existing_run_id"):
            # Warm-start FPS: candidates plus the existing training set.
            ids = [params.get("run_id"), params.get("existing_run_id")]
        elif analysis_type in ("compare", "mantel"):
            ids = [params.get("left_run_id") or params.get("reference_run_id"), params.get("right_run_id") or params.get("query_run_id")]
        elif analysis_type == "sensitivity":
            ids = params.get("run_ids") or []
        else:
            ids = params.get("run_ids") or [params.get("run_id")]
        if isinstance(ids, (str, bytes)):
            ids = [ids]
        ids = [str(v) for v in ids if v]
        if not ids:
            raise AppError(INVALID_PARAMS, "at least one descriptor run id is required")
        if analysis_type in ("coverage", "overlap", "acquisition", "drift", "compare", "mantel") and len(ids) != 2:
            raise AppError(ANALYSIS_INPUT_INVALID, f"{analysis_type} requires reference and query run IDs")
        return ids

    def _element_group_labels(self, run_row: dict, samples: DescriptorMatrix, scope: tuple) -> np.ndarray:
        """One element-set label ("C", "C-O-Si", …) per sample, for grouped FPS.

        Atom-mode rows already carry their central element; structure-mode rows
        resolve their composition through the dataset adapter.  A failure here
        is fatal for grouped FPS — silently degrading the groups would change
        the scientific result without telling anyone.  ``scope`` identifies the
        sample set (view selection hash + size) for the label cache; completed
        runs are immutable, so cached labels cannot go stale.
        """
        cache_key = (str(run_row["id"]), "atom" if samples.elements is not None else "structure") + tuple(str(part) for part in scope)
        cached = self._group_labels_cache.get(cache_key)
        if cached is not None and len(cached) == samples.n_samples:
            return cached
        from ..datasets.deepmd_symbols import _Z_TO_SYMBOL

        if samples.elements is not None and len(samples.elements) == samples.n_samples:
            labels = np.asarray(
                [_Z_TO_SYMBOL.get(int(z), f"Z{int(z)}") for z in np.asarray(samples.elements).tolist()],
                dtype=object,
            )
        else:
            if self.datasets is None:
                raise AppError(ANALYSIS_INPUT_INVALID, "grouped FPS requires dataset access to read element metadata")
            dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
            if dataset is None:
                raise AppError(ANALYSIS_INPUT_INVALID, f"dataset {run_row['dataset_id']} does not exist")
            adapter = self.datasets._adapter_for(dataset)
            count = len(adapter)
            frame_cache: dict[int, str] = {}
            labels_list: list[str] = []
            for frame_index in np.asarray(samples.frame, dtype=np.int64).tolist():
                label = frame_cache.get(frame_index)
                if label is None:
                    if not 0 <= frame_index < count:
                        raise AppError(ANALYSIS_INPUT_INVALID, f"sample frame {frame_index} is outside dataset {dataset['id']}")
                    frame = adapter.get_frame(frame_index)
                    label = "-".join(sorted({_Z_TO_SYMBOL.get(int(z), f"Z{int(z)}") for z in np.asarray(frame.numbers).tolist()}))
                    frame_cache[frame_index] = label
                labels_list.append(label)
            labels = np.asarray(labels_list, dtype=object)
        if len(labels) != samples.n_samples:
            raise AppError(ANALYSIS_INPUT_INVALID, "element metadata does not align with the descriptor samples")
        self._group_labels_cache[cache_key] = labels
        while len(self._group_labels_cache) > 8:
            self._group_labels_cache.pop(next(iter(self._group_labels_cache)))
        return labels

    def _usable_run(self, run_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (run_id,))
        if row is None:
            raise AppError(INVALID_PARAMS, f"run {run_id} does not exist")
        if row["status"] == "STALE":
            raise AppError(ANALYSIS_STALE, f"run {run_id} is STALE and cannot feed new analysis", {"run_id": run_id})
        if row["status"] != "COMPLETED" or not row.get("result_path"):
            raise AppError(RESULT_INCOMPATIBLE, f"run {run_id} is {row['status']}")
        self._assert_dataset_current(row)
        return row

    def _assert_dataset_current(self, row: dict) -> None:
        if self.datasets is None:
            return
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (row["dataset_id"],))
        if dataset is not None:
            # DatasetService.refresh_if_changed owns the fingerprint comparison:
            # it also migrates v1 fingerprints and marks the linked runs stale,
            # so re-deriving it here only drifted from the real check.
            self.datasets.refresh_if_changed(dataset)

    def _usable_view(self, view_id: str, dataset_id: str) -> dict:
        view = self.db.query_one("SELECT * FROM dataset_views WHERE id = ?", (view_id,))
        if view is None:
            raise AppError(INVALID_PARAMS, f"dataset view {view_id} does not exist")
        if view["dataset_id"] != dataset_id:
            raise AppError(ANALYSIS_INPUT_INVALID, "dataset view does not belong to the descriptor run dataset")
        dataset = self.db.query_one("SELECT fingerprint FROM datasets WHERE id = ?", (dataset_id,))
        if dataset is None or view["dataset_fingerprint"] != dataset["fingerprint"]:
            raise AppError(ANALYSIS_STALE, f"dataset view {view_id} is stale")
        return view

    @staticmethod
    def _slice_samples_to_frames(samples: DescriptorMatrix, frame_indices: list[int]) -> DescriptorMatrix:
        selected = np.flatnonzero(np.isin(samples.frame, np.asarray(frame_indices, dtype=np.int64)))
        if selected.size == 0:
            raise AppError(ANALYSIS_INPUT_INVALID, "dataset view contains no descriptor samples")
        return samples.subset(selected)

    def _load_samples(self, run_row: dict, params: dict, analysis_type: str, check=None, view_id=None) -> DescriptorMatrix:
        # Cooperative-cancellation checkpoints between the load/pool stages:
        # without them a cancel during a multi-GB load waits for the whole
        # phase to finish before it takes effect.
        check = check or (lambda: None)
        values, row = self.results.load_values(run_row["id"])
        check()
        values = np.asarray(values, dtype=np.float64)
        if values.ndim > 2:
            values = values.reshape(values.shape[0], -1)
        if values.ndim == 1:
            values = values.reshape(-1, 1)
        if values.ndim != 2 or values.shape[0] == 0 or values.shape[1] == 0:
            raise AppError(ANALYSIS_INPUT_INVALID, "descriptor result is empty or not a 2D feature matrix")
        # Feature variance is a diagnostic: it keeps finite values per column
        # and reports invalid counts. Every other analysis remains strict so a
        # bad descriptor cannot be silently hidden by preprocessing.
        if analysis_type != "feature_variance" and not np.isfinite(values).all():
            raise AppError(ANALYSIS_INPUT_INVALID, "descriptor result contains NaN or Inf")
        check()
        offsets = self._row_offsets(row)
        meta = self._result_metadata(row)
        requested_mode = str(params.get("mode") or "structure")
        if requested_mode not in ("structure", "atom"):
            raise AppError(ANALYSIS_INPUT_INVALID, "mode must be structure or atom")
        valid_offsets = self._valid_offsets(offsets, values.shape[0])
        positions = None
        cells = None
        pbc = None
        level = str(meta.get("level") or "").lower()
        declared_atom = bool(meta.get("row_semantics") in ("atom", "local_environment", "pair") or any(token in level for token in ("atom", "local", "pair")))
        if requested_mode == "atom":
            if not valid_offsets:
                raise AppError(ANALYSIS_INPUT_INVALID, "atom/local-environment analysis requires verified row_offsets")
            if not declared_atom:
                raise AppError(ANALYSIS_INPUT_INVALID, "descriptor metadata does not declare atom/local-environment rows")
            check()
            local_frames = np.repeat(np.arange(len(offsets) - 1, dtype=np.int64), np.diff(offsets).astype(np.int64))
            frame_values = self._run_frame_values(row, len(offsets) - 1)
            frames = frame_values[local_frames]
            rows = np.arange(values.shape[0], dtype=np.int64) - offsets[local_frames]
            sample_ids = [f"frame:{int(f)}:row:{int(r)}" for f, r in zip(frames, rows)]
            used = values
            elements, positions, cells, pbc = self._atom_metadata(row, offsets, local_frames)
            mode = "atom"
        elif valid_offsets and declared_atom:
            used = _pool_rows(values, offsets)
            check()
            n_frames = used.shape[0]
            frame_value = int(row.get("frame_index") or 0) if row.get("scope") == "frame" else 0
            frames = np.arange(n_frames, dtype=np.int64) + frame_value if row.get("scope") == "frame" else np.arange(n_frames, dtype=np.int64)
            rows = None
            sample_ids = [f"frame:{int(f)}" for f in frames]
            elements = None
            mode = "structure"
        else:
            used = values
            if row.get("scope") == "frame":
                frames = np.arange(values.shape[0], dtype=np.int64) + int(row.get("frame_index") or 0)
            else:
                frames = np.arange(values.shape[0], dtype=np.int64)
            rows = None
            sample_ids = [f"frame:{int(f)}" for f in frames]
            elements = None
            mode = "structure"
        properties = self._sample_properties(row, frames, rows, mode) if analysis_type == "property_correlation" else {}
        matrix_type = AtomDescriptorMatrix if mode == "atom" else StructureDescriptorMatrix
        samples = matrix_type(
            values=used,
            frame=frames,
            row=rows,
            sample_ids=sample_ids,
            elements=elements,
            properties=properties,
            positions=positions,
            cells=cells,
            pbc=pbc,
        )
        if view_id:
            view = self._usable_view(str(view_id), run_row["dataset_id"])
            samples = self._slice_samples_to_frames(samples, json.loads(view["frame_indices_json"]))
        return samples

    @staticmethod
    def _valid_offsets(offsets, n_rows: int) -> bool:
        try:
            return bool(offsets is not None and offsets.ndim == 1 and offsets.size >= 2 and int(offsets[0]) == 0 and int(offsets[-1]) == n_rows and np.all(np.diff(offsets) >= 0))
        except (TypeError, ValueError):
            return False

    def _atom_metadata(self, run_row: dict, offsets, local_frames: np.ndarray) -> tuple[np.ndarray | None, np.ndarray | None, np.ndarray | None, np.ndarray | None]:
        """Element labels and per-atom geometry for atom/local-environment rows.

        Both are optional metadata and become None when the dataset adapter
        cannot verify them against the descriptor rows.
        """
        if self.datasets is None:
            return None, None, None, None
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset is None:
            return None, None, None, None
        try:
            adapter = self.datasets._adapter_for(dataset)
            frames = [adapter.get_frame(int(index)) for index in self._run_frame_values(run_row, len(offsets) - 1).tolist()]
        except Exception:  # optional metadata, not a reason to corrupt a run
            return None, None, None, None

        labels = [int(z) for frame in frames for z in frame.numbers.tolist()]
        elements = np.asarray(labels, dtype=np.int64) if len(labels) == len(local_frames) else None

        try:
            position_rows: list[np.ndarray] = []
            cell_rows: list[np.ndarray] = []
            pbc_rows: list[np.ndarray] = []
            for offset_index, frame in enumerate(frames):
                expected = int(offsets[offset_index + 1] - offsets[offset_index])
                frame_positions = np.asarray(frame.positions, dtype=np.float64)
                if frame_positions.shape != (expected, 3):
                    return elements, None, None, None
                position_rows.append(frame_positions)
                cell = np.asarray(frame.cell, dtype=np.float64)
                if cell.shape != (3, 3):
                    cell = np.zeros((3, 3), dtype=np.float64)
                cell_rows.append(np.repeat(cell[None, :, :], expected, axis=0))
                pbc_rows.append(np.repeat(np.asarray(frame.pbc, dtype=bool)[None, :], expected, axis=0))
            positions = np.concatenate(position_rows, axis=0) if position_rows else np.zeros((0, 3), dtype=np.float64)
            cells = np.concatenate(cell_rows, axis=0) if cell_rows else np.zeros((0, 3, 3), dtype=np.float64)
            pbc = np.concatenate(pbc_rows, axis=0) if pbc_rows else np.zeros((0, 3), dtype=bool)
        except Exception:  # geometry is optional metadata; preserve descriptor analysis if unavailable
            return elements, None, None, None
        return elements, positions, cells, pbc

    def _sample_properties(self, run_row: dict, frames: np.ndarray, rows: np.ndarray | None, mode: str) -> dict[str, np.ndarray]:
        """Load only the physical targets requested by property analysis."""
        if self.datasets is None:
            return {}
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset is None:
            return {}
        try:
            adapter = self.datasets._adapter_for(dataset)
            frame_cache = {int(index): adapter.get_frame(int(index)) for index in np.unique(frames).tolist()}
        except Exception:
            return {}

        names = ("energy", "energy_per_atom", "force_max", "force_magnitude", "volume")
        values = {name: np.full(frames.size, np.nan, dtype=np.float64) for name in names}
        # Compute frame-wide quantities once, then map them to atom rows.
        frame_properties = {}
        for frame_index, frame in frame_cache.items():
            magnitudes = np.linalg.norm(np.asarray(frame.forces, dtype=np.float64), axis=1) if frame.forces is not None and len(frame.forces) else None
            cell = np.asarray(frame.cell, dtype=np.float64)
            volume = abs(float(np.linalg.det(cell))) if cell.shape == (3, 3) else float("nan")
            frame_properties[frame_index] = (magnitudes, float(magnitudes.max()) if magnitudes is not None else None, volume)
        for sample_index, frame_index in enumerate(frames.tolist()):
            frame = frame_cache.get(int(frame_index))
            if frame is None:
                continue
            natoms = max(int(len(frame.numbers)), 1)
            if frame.energy is not None:
                values["energy"][sample_index] = float(frame.energy)
                values["energy_per_atom"][sample_index] = float(frame.energy) / natoms
            magnitudes, force_max, volume = frame_properties[int(frame_index)]
            if magnitudes is not None:
                values["force_max"][sample_index] = force_max
                if mode == "atom" and rows is not None:
                    atom = int(rows[sample_index])
                    if 0 <= atom < magnitudes.size:
                        values["force_magnitude"][sample_index] = float(magnitudes[atom])
            if np.isfinite(volume) and volume > 0:
                values["volume"][sample_index] = volume
        return {name: array for name, array in values.items() if bool(np.isfinite(array).any())}

    def _dataset_elements(self, run_row: dict) -> list[str]:
        """Chemical element symbols declared by the run's dataset (sorted)."""
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset is None:
            return []
        raw = dataset.get("elements")
        try:
            values = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return []
        if not isinstance(values, list):
            return []
        return sorted({str(value) for value in values if str(value)})

    def _sampling_blocks(self, run_row: dict, samples: DescriptorMatrix, block_names: list[str], params: dict, element_list: list[str] | None) -> list[FeatureBlock]:
        """Build the requested composite blocks for one sample set.

        Every block's raw (unscaled) values are returned; scaling and the 1/√D
        weight are applied by the sampling layer, on the reference set when
        warm-starting.
        """
        weights = params.get("block_weights") if isinstance(params.get("block_weights"), dict) else {}
        scaling = str(params.get("scaling") or "robust")
        physical = [name for name in block_names if name in self._PHYSICAL_BLOCKS]
        if physical and samples.row is not None:
            # Atom rows have no composition or per-structure energy; inventing
            # them would silently change what the sampling space means.
            raise AppError(ANALYSIS_INPUT_INVALID, "composite sampling blocks require structure granularity")
        signals = self._physical_signals(run_row, samples, physical) if physical else {}
        blocks: list[FeatureBlock] = []
        for name in block_names:
            if name == "descriptor":
                values = np.asarray(samples.values, dtype=np.float64)
            elif name == "descriptor_summary":
                values = _descriptor_summary(np.asarray(samples.values, dtype=np.float64))
            elif name == "lattice":
                values = signals["lattice"]
            elif name == "composition":
                if not element_list:
                    raise AppError(ANALYSIS_INPUT_INVALID, "composition sampling requires a known element list")
                values = _composition_matrix(signals["composition"], element_list)
            else:
                values = signals[name]
            try:
                weight = float(weights.get(name, 1.0))
            except (TypeError, ValueError) as exc:
                raise AppError(ANALYSIS_INPUT_INVALID, f"block weight for {name!r} must be a number") from exc
            blocks.append(FeatureBlock(name=name, values=values, weight=weight, scaling=scaling))
        return blocks

    def _physical_signals(self, run_row: dict, samples: DescriptorMatrix, names: list[str]) -> dict[str, np.ndarray]:
        """Read lattice/composition/energy/force signals aligned to the samples.

        Missing metadata is an error rather than a silently dropped block: a
        composite space the user did not ask for is worse than a clear failure.
        """
        if self.datasets is None:
            raise AppError(ANALYSIS_INPUT_INVALID, "composite sampling requires dataset access")
        from ..datasets.deepmd_symbols import _Z_TO_SYMBOL

        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (run_row["dataset_id"],))
        if dataset is None:
            raise AppError(ANALYSIS_INPUT_INVALID, f"dataset {run_row['dataset_id']} does not exist")
        adapter = self.datasets._adapter_for(dataset)
        count = len(adapter)
        n = samples.n_samples
        lattice = np.full((n, 6), np.nan, dtype=np.float64)
        energy = np.full(n, np.nan, dtype=np.float64)
        force = np.full((n, 3), np.nan, dtype=np.float64)
        elements: list[list[int]] = []
        frame_cache: dict[int, object] = {}
        wanted = set(names)
        for sample_index, frame_index in enumerate(np.asarray(samples.frame, dtype=np.int64).tolist()):
            frame = frame_cache.get(frame_index)
            if frame is None:
                if not 0 <= frame_index < count:
                    raise AppError(ANALYSIS_INPUT_INVALID, f"sample frame {frame_index} is outside dataset {dataset['id']}")
                frame = adapter.get_frame(frame_index)
                frame_cache[frame_index] = frame
            numbers = np.asarray(frame.numbers, dtype=np.int64)
            if "composition" in wanted:
                elements.append(numbers.tolist())
            if "lattice" in wanted:
                cell = np.asarray(frame.cell, dtype=np.float64)
                if cell.shape == (3, 3) and np.abs(cell).sum() > 1e-12:
                    lattice[sample_index] = _cell_parameters(cell)
            if "energy" in wanted and frame.energy is not None:
                energy[sample_index] = float(frame.energy) / max(int(numbers.size), 1)
            if "force" in wanted and frame.forces is not None and len(frame.forces):
                magnitudes = np.linalg.norm(np.asarray(frame.forces, dtype=np.float64), axis=1)
                force[sample_index] = (magnitudes.mean(), magnitudes.max(), magnitudes.std())
        signals: dict[str, np.ndarray] = {}
        for name in names:
            if name == "lattice":
                signals["lattice"] = _require_finite(lattice, "lattice", "lattice parameters (periodic cells)")
            elif name == "energy":
                signals["energy"] = _require_finite(energy.reshape(-1, 1), "energy", "per-atom energies")
            elif name == "force":
                signals["force"] = _require_finite(force, "force", "force statistics")
            elif name == "composition":
                # Atom numbers are not a numeric feature space; they become
                # element fractions once the shared element list is known.
                signals["composition"] = elements
        if "composition" in signals and len(elements) != n:
            raise AppError(ANALYSIS_INPUT_INVALID, "composition metadata does not align with the descriptor samples")
        return signals


__all__ = ["AnalysisDataMixin"]
