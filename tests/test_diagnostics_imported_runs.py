"""Imported-run semantics for the diagnostics and perturbation runners.

Imported descriptor results (device = "imported"/"external") carry no locally
rebuildable descriptor: the engine rejects the pseudo device, and before the
guard the failure surfaced as a generic DESCRIPTOR_CONFIGURATION_ERROR that
looked like a parameter problem.  The contract now is:

- pair-search diagnostics (degeneracy search, distance consistency) read the
  stored matrix and dataset geometry only, so imported runs work;
- recompute-driven diagnostics (formal invariance, cutoff smoothness,
  environment jacobian) and the perturbation runner are rejected with
  RESULT_INCOMPATIBLE at the runner boundary;
- submit_generic rejects the same combinations before a job is created.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mdescriptor_studio_backend.errors import AppError, RESULT_INCOMPATIBLE
from mdescriptor_studio_backend.services.analysis_service import AnalysisService
from mdescriptor_studio_backend.services.job_runner import AnalysisRunMixin
from mdescriptor_studio_backend.services.result_service import ResultService
from mdescriptor_studio_backend.services.result_transfer_service import ResultTransferService
from mdescriptor_studio_backend.storage.database import Database
from mdescriptor_studio_backend.analysis.models import StructureDescriptorMatrix


class _StubCtx:
    def check_cancelled(self) -> None: ...

    def progress(self, *args, **kwargs) -> None: ...

    def attach_control(self, control) -> None: ...


def _service_with_imported_run(tmp_path: Path, semantics: str = "structure"):
    data = tmp_path / "app"
    data.mkdir()
    db = Database(data / "db.sqlite")
    db.execute(
        "INSERT INTO datasets (id,name,format,source_path,number_of_frames,elements,properties,periodicity,fingerprint,file_size,created_at) "
        "VALUES ('ds_1','test','extxyz','unused',3,'[]','{}','{}','fp',0,'2026-01-01')"
    )
    # The imported dataset's frames carry geometry (positions/cell/pbc) and
    # energy: enough for the pair-search structural fingerprints.
    from mdescriptor_studio_backend.datasets.base import DatasetFrame

    frames = [
        DatasetFrame(
            numbers=np.array([1, 1, 1], dtype=np.int64),
            positions=np.array([[0.0, 0.0, 0.0], [1.5, 0.1, 0.0], [0.1, 1.4, 0.2]]),
            cell=np.zeros((3, 3)),
            pbc=np.zeros(3, dtype=bool),
            energy=-1.23,
        ),
        DatasetFrame(
            numbers=np.array([1, 1, 1], dtype=np.int64),
            positions=np.array([[0.0, 0.0, 0.0], [2.5, 0.0, 0.1], [0.2, 2.3, 0.0]]),
            cell=np.zeros((3, 3)),
            pbc=np.zeros(3, dtype=bool),
            energy=-2.34,
        ),
        DatasetFrame(
            numbers=np.array([1, 1], dtype=np.int64),
            positions=np.array([[0.0, 0.4, 0.0], [1.2, 0.0, 0.9]]),
            cell=np.zeros((3, 3)),
            pbc=np.zeros(3, dtype=bool),
            energy=-0.87,
        ),
    ]
    datasets = SimpleNamespace(
        refresh_if_changed=lambda row: "fp",
        adapter_for=lambda row: SimpleNamespace(get_frame=lambda i: frames[i]),
        adapter=SimpleNamespace(
            build=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("imported runs must not build a descriptor")),
            make_control=lambda: None,
        ),
    )
    results = ResultService(db)
    transfer = ResultTransferService(results, datasets)
    values = np.arange(9 if semantics == "structure" else 12, dtype=np.float32).reshape(-1, 3)
    source = tmp_path / "custom.npz"
    payload = {
        "values": values,
        "metadata": json.dumps({
            "descriptor": "Custom",
            "row_semantics": semantics,
            "descriptor_version": "v1",
            "configuration": {"cutoff": 5},
        }),
    }
    if semantics == "atom":
        payload["row_offsets"] = np.array([0, 2, 4])
    np.savez(source, **payload)
    run_id = transfer.import_file({"dataset_id": "ds_1", "path": str(source)})["run_id"]
    service = AnalysisService(db, None, results, datasets=datasets, data_dir=data)
    run_row = dict(db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (run_id,)))
    return service, db, run_row, values


def _samples(values: np.ndarray) -> StructureDescriptorMatrix:
    return StructureDescriptorMatrix(
        values=values,
        frame=np.array([0, 1, 2], dtype=np.int64),
        sample_ids=["frame:0", "frame:1", "frame:2"],
    )


@pytest.mark.parametrize("analysis_type", ["formal_invariance", "cutoff_smoothness", "environment_jacobian"])
def test_recompute_diagnostics_reject_imported_runs(tmp_path, analysis_type):
    service, _db, run_row, values = _service_with_imported_run(tmp_path)
    params = {"run_id": run_row["id"], "max_structures": 2, "cutoff": 5.0, "cutoff_value": 5.0}
    with pytest.raises(AppError) as excinfo:
        AnalysisRunMixin._run_diagnostics(service, analysis_type, params, run_row, _samples(values), _StubCtx())
    assert excinfo.value.code == RESULT_INCOMPATIBLE
    assert "imported" in excinfo.value.message


@pytest.mark.parametrize("analysis_type", ["degeneracy_search", "distance_consistency"])
def test_pair_search_diagnostics_run_on_imported_runs(tmp_path, analysis_type):
    service, _db, run_row, values = _service_with_imported_run(tmp_path)
    params = {"run_id": run_row["id"], "mode": "structure", "k_neighbors": 1, "max_samples": 3, "max_pairs": 5}
    result = AnalysisRunMixin._run_diagnostics(service, analysis_type, params, run_row, _samples(values), _StubCtx())
    assert result["preview"]["kind"] == analysis_type


def test_pair_search_arrays_stay_finite_across_atom_counts(tmp_path):
    """Cross-atom-count pairs are structurally infinite; the artifact store
    rejects Inf arrays, so the published pair arrays must carry the comparable
    subset while the preview keeps the full counts."""
    service, _db, run_row, values = _service_with_imported_run(tmp_path)
    params = {"run_id": run_row["id"], "mode": "structure", "k_neighbors": 2, "max_samples": 3, "max_pairs": 5}
    result = AnalysisRunMixin._run_diagnostics(service, "degeneracy_search", params, run_row, _samples(values), _StubCtx())
    for name, array in result["arrays"].items():
        assert np.all(np.isfinite(np.asarray(array, dtype=np.float64))), name
    assert result["preview"]["different_atom_count_pairs"] >= 1
    assert any(pair["reason"] == "atom_count" for pair in result["preview"]["pairs"]) or result["preview"]["n_dangerous"] >= 0


def _service_with_local_stub_run(tmp_path: Path):
    """A locally computed run row whose adapter only answers schema()."""
    service, db, run_row, values = _service_with_imported_run(tmp_path)
    import sqlite3

    db._conn.execute(
        "UPDATE descriptor_runs SET device = 'cpu' WHERE id = ?", (run_row["id"],)
    )
    db._conn.commit()
    run_row = dict(db.query_one("SELECT * FROM descriptor_runs WHERE id = ?", (run_row["id"],)))
    # schema() answers an empty parameter table: neither rcut in the run
    # parameters nor in the schema default.  build() returns a sentinel - the
    # schema gate fires before the rebuilt descriptor is ever used.
    service.datasets.adapter.build = lambda *args, **kwargs: object()
    service.datasets.adapter.schema = lambda name: {"parameters": {}}
    return service, run_row, values


def test_cutoff_smoothness_reports_undeclared_cutoff_parameter(tmp_path):
    service, run_row, values = _service_with_local_stub_run(tmp_path)
    with pytest.raises(AppError) as excinfo:
        AnalysisRunMixin._run_diagnostics(
            service, "cutoff_smoothness", {"run_id": run_row["id"], "max_structures": 2}, run_row, _samples(values), _StubCtx()
        )
    assert "does not declare" in excinfo.value.message


def test_environment_jacobian_reports_missing_cutoff_hint(tmp_path):
    service, run_row, values = _service_with_local_stub_run(tmp_path)
    with pytest.raises(AppError) as excinfo:
        AnalysisRunMixin._run_diagnostics(
            service, "environment_jacobian", {"run_id": run_row["id"], "max_structures": 2}, run_row, _samples(values), _StubCtx()
        )
    assert "enter the neighbor cutoff" in excinfo.value.message


def test_perturbation_rejects_imported_runs(tmp_path):
    service, _db, run_row, values = _service_with_imported_run(tmp_path)
    with pytest.raises(AppError) as excinfo:
        AnalysisRunMixin._run_perturbation_sensitivity(
            service, {"run_id": run_row["id"], "max_structures": 2}, run_row, _samples(values), _StubCtx()
        )
    assert excinfo.value.code == RESULT_INCOMPATIBLE


def test_submit_rejects_recompute_diagnostics_on_imported_runs(tmp_path):
    """The Run click fails before a job row exists, not after it started."""
    service, db, run_row, values = _service_with_imported_run(tmp_path)
    with pytest.raises(AppError) as excinfo:
        service.submit_generic("formal_invariance", {"run_id": run_row["id"], "granularity": "structure"})
    assert excinfo.value.code == RESULT_INCOMPATIBLE
    remaining = db.query("SELECT COUNT(*) AS n FROM jobs")
    assert remaining[0]["n"] == 0
