"""Portable descriptor NPZ files, normalized to the existing result contract."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import numpy as np

from ..errors import AppError, INVALID_PARAMS
from ..security import validate_local_path, ensure_no_reparse_points, remove_managed_tree, path_within
from .analysis_helpers import _NOW


class ResultTransferService:
    def __init__(self, results, datasets):
        self.results = results
        self.datasets = datasets
        self.db = results.db

    def import_file(self, params):
        try:
            return self._import(params)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise AppError(INVALID_PARAMS, f"Cannot import descriptor: {exc}") from exc

    def _import(self, params):
        path = validate_local_path(params.get("path"))
        if path.suffix.lower() != ".npz":
            raise ValueError("select an NPZ file")
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (params.get("dataset_id"),))
        if dataset is None:
            raise ValueError("select a dataset first")
        self.datasets.refresh_if_changed(dataset)
        dataset = self.db.query_one("SELECT * FROM datasets WHERE id = ?", (dataset["id"],))
        with np.load(path, allow_pickle=False) as archive:
            values = archive["values"]
            meta = json.loads(str(archive["metadata"].item()))
            offsets = archive["row_offsets"] if "row_offsets" in archive else None
        if not isinstance(meta, dict):
            raise ValueError("metadata must be a JSON object")
        if values.ndim != 2 or min(values.shape) == 0 or values.dtype.kind not in "fiu" or not np.isfinite(values).all():
            raise ValueError("values must be a nonempty finite real numeric matrix [rows, features]")
        name = meta.get("descriptor")
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError("metadata.descriptor must be a name of 1–128 characters")
        semantics = meta.get("row_semantics")
        if semantics not in ("structure", "atom"):
            raise ValueError("metadata.row_semantics must be structure or atom")
        configuration = meta.get("configuration", {})
        if not isinstance(configuration, dict):
            raise ValueError("metadata.configuration must be an object")
        if meta.get("dataset_fingerprint") and meta["dataset_fingerprint"] != dataset["fingerprint"]:
            raise ValueError("file belongs to a different dataset; select the original dataset")
        scope = meta.get("scope", "dataset")
        frame_index = meta.get("frame_index") if scope == "frame" else None
        count = dataset["number_of_frames"]
        if scope not in ("dataset", "frame"):
            raise ValueError("scope must be dataset or frame")
        if scope == "frame":
            if type(frame_index) is not int or not 0 <= frame_index < count:
                raise ValueError("frame_index is outside the dataset")
            count = 1
        if semantics == "structure":
            if len(values) != count or offsets is not None:
                raise ValueError("structure rows must match the frame count; omit row_offsets")
        else:
            if offsets is None or offsets.ndim != 1 or offsets.dtype.kind not in "iu" or len(offsets) != count + 1 or offsets[0] != 0 or offsets[-1] != len(values) or np.any(offsets[1:] <= offsets[:-1]):
                raise ValueError("atom row_offsets must start at 0, increase per frame, and end at the row count")
            adapter = self.datasets.adapter_for(dataset)
            for i in range(count):
                if int(offsets[i + 1]) - int(offsets[i]) != len(adapter.get_frame(frame_index if scope == "frame" else i).numbers):
                    raise ValueError(f"atom count differs from dataset frame {i}")
        run_id = "run_" + uuid.uuid4().hex
        root = self.results.data_dir / "results" / run_id
        ensure_no_reparse_points(root)
        now = _NOW()
        meta.update(run_id=run_id, descriptor=name.strip(), dataset_id=dataset["id"],
                    dataset_fingerprint=dataset["fingerprint"], scope=scope, frame_index=frame_index,
                    shape=list(values.shape), dtype=str(values.dtype), feature_count=values.shape[1],
                    level=semantics, row_offsets_verified=offsets is not None, configuration=configuration,
                    device="imported",
                    created_at=now, source="import")
        version = str(meta.get("engine_version") or "external")
        meta["engine_version"] = version
        try:
            root.mkdir(parents=True)
            np.save(root / "values.npy", values, allow_pickle=False)
            if offsets is not None:
                np.save(root / "row_offsets.npy", offsets, allow_pickle=False)
            (root / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            self.db.execute(
                "INSERT INTO descriptor_runs (id,dataset_id,descriptor_name,engine_version,descriptor_version,parameters_json,scope,frame_index,device,status,created_at,result_path,result_shape_json,feature_count,row_semantics) VALUES (?,?,?,?,?,?,?,?,?,'COMPLETED',?,?,?,?,?)",
                (run_id, dataset["id"], name.strip(), version, str(meta.get("descriptor_version") or version),
                 json.dumps(configuration), scope, frame_index, "imported", now, str(root), json.dumps(list(values.shape)), values.shape[1], semantics),
            )
        except Exception:
            remove_managed_tree(root)
            raise
        return {"run_id": run_id}

    def export_file(self, params):
        try:
            path = validate_local_path(params.get("path"))
            if path.suffix.lower() != ".npz":
                raise ValueError("export path must end in .npz")
            if path_within(self.results.data_dir, path):
                raise ValueError("export outside the application data directory")
            for dataset in self.db.query("SELECT source_path FROM datasets"):
                if path_within(Path(dataset["source_path"]), path):
                    raise ValueError("export must not overwrite a registered dataset")
            values, row = self.results.load_values(params.get("run_id"), mmap=True)
            payload = {"values": values, "metadata": np.array(json.dumps(row["metadata"], ensure_ascii=False))}
            offsets = Path(row["result_path"]) / "row_offsets.npy"
            ensure_no_reparse_points(offsets)
            if offsets.is_file():
                payload["row_offsets"] = np.load(offsets, allow_pickle=False)
            temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                with temporary.open("xb") as handle:
                    np.savez_compressed(handle, **payload)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            return {"path": str(path)}
        except (OSError, ValueError, TypeError) as exc:
            raise AppError(INVALID_PARAMS, f"Cannot export descriptor: {exc}") from exc
