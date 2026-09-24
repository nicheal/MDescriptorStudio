import json
from types import SimpleNamespace

import numpy as np
import pytest

from mdescriptor_studio_backend.errors import AppError
from mdescriptor_studio_backend.services.result_service import ResultService
from mdescriptor_studio_backend.services.result_transfer_service import ResultTransferService
from mdescriptor_studio_backend.services.analysis_service import AnalysisService
from mdescriptor_studio_backend.storage.database import Database


@pytest.mark.parametrize("semantics", ["structure", "atom"])
def test_transfer_roundtrip_and_analysis_identity(tmp_path, semantics):
    data = tmp_path / "app"
    data.mkdir()
    db = Database(data / "db.sqlite")
    db.execute("INSERT INTO datasets (id,name,format,source_path,number_of_frames,elements,properties,periodicity,fingerprint,file_size,created_at) VALUES ('ds_1','test','extxyz','unused',2,'[]','{}','{}','fp',0,'2026-01-01')")
    datasets = SimpleNamespace(refresh_if_changed=lambda row: "fp", adapter_for=lambda row: SimpleNamespace(get_frame=lambda i: SimpleNamespace(numbers=[1, 1])))
    results = ResultService(db)
    transfer = ResultTransferService(results, datasets)
    values = np.arange(12 if semantics == "atom" else 6, dtype=np.float32).reshape(-1, 3)
    source = tmp_path / "custom.npz"
    payload = {"values": values, "metadata": json.dumps({"descriptor": "Custom", "row_semantics": semantics, "descriptor_version": "v1", "configuration": {"cutoff": 5}})}
    if semantics == "atom":
        payload["row_offsets"] = np.array([0, 2, 4])
    np.savez(source, **payload)
    run = transfer.import_file({"dataset_id": "ds_1", "path": str(source)})["run_id"]
    analysis = AnalysisService(db, None, results, datasets=datasets, data_dir=data)
    identity = analysis.sample_identity({"run_id": run, "mode": semantics, "i": len(values) - 1})
    assert identity["frame"] == 1
    assert identity["row"] == (1 if semantics == "atom" else None)
    destination = tmp_path / "export.npz"
    transfer.export_file({"run_id": run, "path": str(destination)})
    restored = transfer.import_file({"dataset_id": "ds_1", "path": str(destination)})["run_id"]
    np.testing.assert_array_equal(results.load_values(restored)[0], values)
    assert results.feature_space_signature(run)[0] == results.feature_space_signature(restored)[0]
    for invalid in (np.full_like(values, np.nan), values[:-1], values.astype(complex)):
        np.savez(source, **{**payload, "values": invalid})
        with pytest.raises(AppError):
            transfer.import_file({"dataset_id": "ds_1", "path": str(source)})
    assert len(results.list({"dataset_id": "ds_1"})) == 2
    db.close()
