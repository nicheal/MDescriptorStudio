"""Descriptor submit -> compute -> cache flow through the real engine (M3/M4 backend)."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from make_fixtures import write_deepmd  # noqa: E402

from test_backend_smoke import BackendProcess  # noqa: E402
from test_dataset_flow import wait_job  # noqa: E402


def _register(bp: BackendProcess, path: Path) -> str:
    resp = bp.request(100, "dataset.register", {"path": str(path), "name": path.name})
    done = wait_job(bp, resp["result"]["job_id"])
    assert done["status"] == "COMPLETED", done
    return done["result"]["dataset_id"]


def test_descriptor_registry_and_compute(tmp_path: Path) -> None:
    ds_dir = tmp_path / "gaas"
    write_deepmd(ds_dir, 12, 64, seed=31)
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        ds_id = _register(bp, ds_dir)

        # registry is dynamic (28 entries, no hardcoding)
        listing = bp.request(101, "descriptor.list")
        names = [d["name"] for d in listing["result"]]
        assert len(names) == 28 and "ACE" in names and "NEP" in names
        assert all(
            item["display_name"]
            and item["description"]
            and item["schema_version"] >= 3
            and item["descriptor_version"]
            and item["execution_engine"]
            and item["input"]
            for item in listing["result"]
        )

        schema = bp.request(102, "descriptor.describe", {"name": "ACE"})["result"]
        assert schema["schema_version"] == 3
        assert schema["parameters"]["species"]["display_name"] == "Chemical species"
        assert schema["parameters"]["species"]["description"]
        assert schema["parameters"]["trans"]["properties"]["p"]["display_name"] == "Transform power"
        assert schema["parameters"]["species"]["required"] is True
        assert "properties" in schema["parameters"]["trans"]  # nested object

        # submit (whole dataset, small N for speed)
        sub = bp.request(
            103,
            "descriptor.submit",
            {
                "dataset_id": ds_id,
                "descriptor_name": "ACE",
                "parameters": {"species": [31, 33], "N": 1},
                "scope": "dataset",
            },
        )
        assert sub["result"]["cache"] is None
        done = wait_job(bp, sub["result"]["job_id"], timeout=300)
        assert done["status"] == "COMPLETED", done
        run_id = done["result"]["run_id"]
        assert done["result"]["shape"] == [12 * 64, done["result"]["feature_count"]]  # atom level
        assert done["result"]["level"] == "DescriptorLevel.ATOM"

        # second submit hits the cache
        sub2 = bp.request(
            104,
            "descriptor.submit",
            {
                "dataset_id": ds_id,
                "descriptor_name": "ACE",
                "parameters": {"species": [31, 33], "N": 1},
                "scope": "dataset",
            },
        )
        cache = sub2["result"]["cache"]
        assert cache is not None and cache["existing_run_id"] == run_id

        # result metadata roundtrip
        got = bp.request(105, "result.get", {"run_id": run_id})
        meta = got["result"]["metadata"]
        assert meta["descriptor"] == "ACE"
        assert meta["dataset_fingerprint"]
        assert meta["shape"] == [12 * 64, done["result"]["feature_count"]]
        assert Path(got["result"]["result_path"], "values.npy").exists()

        # validation errors
        bad = bp.request(
            106,
            "descriptor.submit",
            {
                "dataset_id": ds_id,
                "descriptor_name": "ACE",
                "parameters": {"species": [31, 33], "bogus": 1},
                "scope": "dataset",
            },
        )
        assert bad["error"]["code"] == "DESCRIPTOR_CONFIGURATION_ERROR"
    finally:
        assert bp.close() == 0


def test_descriptor_device_selection(tmp_path: Path) -> None:
    """Device param: schema-driven validation, cache-key separation, provenance."""
    ds_dir = tmp_path / "gaas"
    write_deepmd(ds_dir, 6, 64, seed=41)
    bp = BackendProcess(tmp_path)
    try:
        assert bp.read_line()["event"] == "backend.ready"
        ds_id = _register(bp, ds_dir)

        base = {
            "dataset_id": ds_id,
            "descriptor_name": "ACE",
            "parameters": {"species": [31, 33], "N": 1},
            "scope": "dataset",
        }

        # a device the schema does not declare is rejected at submit time
        bad = bp.request(200, "descriptor.submit", {**base, "device": "tpu"})
        assert bad["error"]["code"] == "INVALID_PARAMS", bad

        # cpu run completes; an identical cpu resubmit hits the cache
        sub_cpu = bp.request(201, "descriptor.submit", {**base, "device": "cpu"})
        done_cpu = wait_job(bp, sub_cpu["result"]["job_id"])
        assert done_cpu["status"] == "COMPLETED", done_cpu
        run_id = done_cpu["result"]["run_id"]

        hit = bp.request(202, "descriptor.submit", {**base, "device": "cpu"})
        assert hit["result"]["job_id"] is None and hit["result"]["cache"], hit

        got = bp.request(203, "result.get", {"run_id": run_id})
        assert got["result"]["device"] == "cpu"
        assert got["result"]["metadata"]["device"] == "cpu"

        # a different device must not hit the cpu cache; on a GPU-less machine
        # the engine fails the run with DEVICE_UNAVAILABLE (a CUDA machine
        # completes it), so accept both outcomes explicitly.
        sub_cuda = bp.request(204, "descriptor.submit", {**base, "device": "cuda"})
        assert sub_cuda["result"]["job_id"] and sub_cuda["result"]["cache"] is None, sub_cuda
        done_cuda = wait_job(bp, sub_cuda["result"]["job_id"], timeout=120.0)
        if done_cuda["status"] == "FAILED":
            assert done_cuda["error"]["code"] == "DEVICE_UNAVAILABLE", done_cuda
        else:
            assert done_cuda["status"] == "COMPLETED", done_cuda
    finally:
        assert bp.close() == 0
