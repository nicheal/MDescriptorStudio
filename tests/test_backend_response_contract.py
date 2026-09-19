"""Golden top-level response shape of the read-only RPCs, for the browser mock to match.

`frontend/e2e/*` runs the app against `frontend/src/preview.tsx`, a hand-written
mock of this protocol. That keeps the specs from ever noticing a response that
changed shape: the UI can be coded against keys the real backend never sends.
This file records what the *sidecar* actually answers, and
`frontend/e2e/wire-contract.spec.ts` asserts the mock answers the same way.

Only the zero/low-argument read methods are covered - enough to catch a rename
or a dropped field on the paths every page mounts through.

Regenerate after an intentional contract change:
    MDS_UPDATE_CONTRACT=1 python -m pytest tests/test_backend_response_contract.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from conftest import BackendProcess, register_dataset
from make_fixtures import write_extxyz

GOLDEN = Path(__file__).parent / "data" / "backend-response-keys.json"
UPDATE = bool(os.environ.get("MDS_UPDATE_CONTRACT"))

# (method, params). `settings.get` is included because its reply is the only
# thing the persisted UI state depends on.
METHODS: list[tuple[str, dict]] = [
    ("system.info", {}),
    ("dataset.list", {}),
    ("dataset.view.list", {}),
    ("dataset.findings", {"id": "ds_missing", "check": "missing_values", "limit": 5}),  # noqa: E501
    ("dataset.statistics", {"id": "ds_missing"}),
    ("descriptor.list", {}),
    ("result.list", {}),
    ("analysis.list", {}),
    ("job.list", {}),
    ("settings.get", {"key": "ui.language"}),
]


def _shape(result: object) -> dict:
    if isinstance(result, dict):
        return {"kind": "object", "keys": sorted(result)}
    if isinstance(result, list):
        return {"kind": "array"}
    return {"kind": type(result).__name__}


def _capture(data_dir: Path) -> dict[str, dict]:
    """Ask a real sidecar each question.

    A throwaway dataset is registered first so the id-taking methods answer on
    the success path - that is the envelope the mock has to imitate, and an
    error reply would pin nothing but the error shape.
    """
    proc = BackendProcess(data_dir)
    try:
        proc.read_line()  # backend.ready
        source = data_dir / "contract.xyz"
        write_extxyz(source, n_frames=2, natoms=2)
        dataset_id = register_dataset(proc, 900, source)
        contract: dict[str, dict] = {}
        for vid, (method, requested) in enumerate(METHODS, start=1):
            # "ds_missing" is a placeholder resolved to the throwaway dataset, so
            # the recorded params stay meaningful to the browser spec.
            params = {**requested, "id": dataset_id} if "id" in requested else requested
            reply = proc.request(vid, method, params)
            # Record the placeholder, not the generated id: the golden must be
            # reproducible, and the browser spec resolves @dataset itself.
            recorded = {**params, "id": "@dataset"} if "id" in params else params
            if "error" in reply:
                # Recorded with its code: a method that cannot be exercised on
                # the success path must be visible as such, not as a shape.
                contract[method] = {"params": recorded, "kind": "error", "code": reply["error"]["code"]}
            else:
                contract[method] = {"params": recorded, **_shape(reply["result"])}
        return contract
    finally:
        proc.close()


def test_read_rpc_shapes_match_the_golden(tmp_path: Path) -> None:
    observed = _capture(tmp_path)
    if UPDATE:
        GOLDEN.write_text(json.dumps(observed, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert observed == golden, (
        "a read RPC changed its top-level shape; update preview.tsx (and the UI) "
        "or regenerate the golden deliberately with MDS_UPDATE_CONTRACT=1"
    )
