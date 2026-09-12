"""Regression tests for the local IPC and filesystem trust boundaries."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from mdescriptor_studio_backend.datasets import compute_fingerprint
from mdescriptor_studio_backend.datasets.extxyz import ExtXYZAdapter
from mdescriptor_studio_backend.errors import AppError, INVALID_PARAMS, RESULT_INCOMPATIBLE
from mdescriptor_studio_backend.analysis.engine import _disable_numba_disk_cache
from mdescriptor_studio_backend.protocol.frames import parse_request, response_err
from mdescriptor_studio_backend.security import UnsafePathError, validate_local_path
from mdescriptor_studio_backend.services.analysis_service import AnalysisService
from mdescriptor_studio_backend.services.descriptor_service import DescriptorService
from mdescriptor_studio_backend.services.result_service import ResultService
from mdescriptor_studio_backend.storage.database import Database


def test_protocol_rejects_non_integral_request_ids_and_hides_diagnostics() -> None:
    for bad_id in (True, "1", {}, []):
        with pytest.raises(AppError) as exc:
            parse_request(json.dumps({"protocol_version": 1, "id": bad_id, "method": "system.info"}))
        assert exc.value.code == INVALID_PARAMS

    frame = response_err(1, AppError("INTERNAL_ERROR", "secret C:/private/file", {"path": "C:/private/file"}))
    assert "private" not in json.dumps(frame)
    assert frame["error"]["error_id"]


def test_protocol_rejects_deep_json_without_killing_dispatch() -> None:
    with pytest.raises(AppError) as exc:
        parse_request("{" + '"x":[' * 1200 + "0" + "]" * 1200 + "}")
    assert exc.value.code == INVALID_PARAMS


def test_numba_cache_guard_turns_cache_true_into_null_cache() -> None:
    numba = pytest.importorskip("numba")
    assert _disable_numba_disk_cache()

    @numba.njit(cache=True)
    def add_one(value):
        return value + 1

    assert add_one(2) == 3
    assert type(add_one._cache).__name__ == "NullCache"
    assert add_one._cache.cache_path is None


def test_local_path_policy_rejects_namespace_ads_and_device_forms(tmp_path: Path) -> None:
    for raw in (
        "relative/file.xyz",
        r"\\server\share\file.xyz",
        r"\\?\C:\file.xyz",
        r"\\.\PhysicalDrive0",
        r"C:\output\file.txt:secret",
        r"C:\output\CON.txt",
    ):
        with pytest.raises(UnsafePathError):
            validate_local_path(raw)
    assert validate_local_path(str(tmp_path / "safe.out")) == (tmp_path / "safe.out").resolve()


def test_fingerprint_v2_changes_when_file_content_changes(tmp_path: Path) -> None:
    source = tmp_path / "sample.xyz"
    source.write_bytes(b"A" * 4096)
    before = compute_fingerprint(source)
    # cross an NTFS last-write timestamp tick: two same-size writes inside one
    # tick share an mtime_ns, and the 2s fingerprint cache would then return
    # the first value for the second call
    time.sleep(0.02)
    source.write_bytes(b"B" * 4096)
    after = compute_fingerprint(source)
    assert before.startswith("v2:")
    assert after != before


def test_fingerprint_force_bypasses_ttl_cache(tmp_path: Path) -> None:
    source = tmp_path / "sample.xyz"
    source.write_bytes(b"A" * 4096)
    before = compute_fingerprint(source)
    stat = source.stat()
    source.write_bytes(b"B" * 4096)
    # Simulate a metadata-preserving replacement, which is the threat model
    # for a size/mtime-only cache key.
    import os

    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert compute_fingerprint(source) == before
    assert compute_fingerprint(source, use_cache=False) != before


def test_extxyz_atom_count_is_bounded(tmp_path: Path) -> None:
    source = tmp_path / "oversized.xyz"
    source.write_text("1000001\nProperties=species:S:1:pos:R:3\n", encoding="utf-8")
    with pytest.raises(AppError) as exc:
        ExtXYZAdapter(source)
    assert exc.value.code == "INVALID_DATASET"


def test_result_remove_refuses_poisoned_db_path(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    db.execute(
        "INSERT INTO datasets (id, name, format, source_path, number_of_frames, elements, properties, periodicity, fingerprint, file_size, created_at, last_scan_at)"
        " VALUES ('ds_1', 'd', 'extxyz', ?, 1, '[]', '{}', '{}', 'fp', 0, 'now', NULL)",
        (str(tmp_path / "source.xyz"),),
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    db.execute(
        "INSERT INTO descriptor_runs (id, dataset_id, descriptor_name, engine_version, parameters_json, scope, status, created_at, result_path)"
        " VALUES ('run_1', 'ds_1', 'ACE', 'test', '{}', 'dataset', 'COMPLETED', 'now', ?)",
        (str(outside),),
    )

    with pytest.raises(AppError) as exc:
        ResultService(db, tmp_path / "managed").remove({"run_id": "run_1"})
    assert exc.value.code == RESULT_INCOMPATIBLE
    assert marker.exists()
    assert db.query_one("SELECT id FROM descriptor_runs WHERE id = 'run_1'") is not None


def test_analysis_manifest_cannot_escape_managed_artifact(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite3")
    root = tmp_path / "analysis" / "ana_1"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(
        json.dumps({"completed": True, "files": {"coords": {"path": "../outside.npy"}}}),
        encoding="utf-8",
    )
    service = AnalysisService(db, jobs=None, results=ResultService(db, tmp_path), datasets=None, data_dir=tmp_path)
    assert not service._artifact_is_complete({"id": "ana_1", "result_path": str(root)})


def test_descriptor_schema_rejects_nonfinite_or_out_of_range_values() -> None:
    service = object.__new__(DescriptorService)
    with pytest.raises(AppError):
        service._check_value("r_cut", {"type": "number", "exclusiveMinimum": 0.0}, 0.0)
    with pytest.raises(AppError):
        service._check_value("max_rank", {"type": "integer", "minimum": 0, "maximum": 5}, 6)
    with pytest.raises(AppError):
        service._check_value("model", {"type": "model"}, r"\\server\share\model.pt")
