"""Thread-safety tests for the deferred warmup coordination.

Both warmup gates protect the same Windows hazard: heavy native imports must
happen on one dedicated thread, serialized against every request path.
"""

from __future__ import annotations

import threading
import time

import mdescriptor_studio_backend.analysis.algorithms._common as analysis_common
import mdescriptor_studio_backend.mdescriptor_adapter as adapter_module
from mdescriptor_studio_backend.mdescriptor_adapter import EngineAdapter


def test_build_waits_for_armed_warmup(monkeypatch) -> None:
    adapter = EngineAdapter()
    sentinel = object()
    monkeypatch.setattr(adapter_module.md, "create_descriptor", lambda config: sentinel)

    entered = threading.Event()
    release = threading.Event()

    def blocked_warmup() -> None:
        entered.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(adapter, "_warmup_locked", blocked_warmup)
    adapter.arm_deferred_warmup()

    warmup_thread = threading.Thread(target=adapter.warmup, name="test-warmup")
    warmup_thread.start()
    assert entered.wait(timeout=5)

    result: dict[str, object] = {}

    def build() -> None:
        result["descriptor"] = adapter.build("fake", {})

    build_thread = threading.Thread(target=build, name="test-build")
    build_thread.start()
    time.sleep(0.05)
    assert build_thread.is_alive(), "build must wait while the armed warmup is running"

    release.set()
    warmup_thread.join(timeout=5)
    build_thread.join(timeout=5)
    assert result["descriptor"] is sentinel
    assert adapter.build("fake", {}) is sentinel, "the gate must be open again once warmup finished"


def test_concurrent_builds_and_warmup_serialize_native_imports(monkeypatch) -> None:
    adapter = EngineAdapter()
    active = 0
    max_active = 0
    count_lock = threading.Lock()

    def fake_create(config):
        nonlocal active, max_active
        with count_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with count_lock:
            active -= 1
        return object()

    monkeypatch.setattr(adapter_module.md, "create_descriptor", fake_create)
    monkeypatch.setattr(adapter, "list_names", lambda: ["fake"])
    monkeypatch.setattr(adapter, "schema", lambda name: {"parameters": {}})

    threads = [
        threading.Thread(target=adapter.warmup, name="test-warmup"),
        threading.Thread(target=adapter.build, args=("fake", {}), name="test-build-1"),
        threading.Thread(target=adapter.build, args=("fake", {}), name="test-build-2"),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    # Without this the assertion below also holds when the warmup thread has
    # deadlocked against itself and never left the condition.
    stalled = [thread.name for thread in threads if thread.is_alive()]
    assert not stalled, f"threads did not finish: {stalled}"
    assert max_active == 1


def test_gated_analysis_imports_wait_for_warmup(monkeypatch) -> None:
    monkeypatch.setattr(analysis_common, "_warmup_gate", threading.Event())
    analysis_common.arm_analysis_warmup_gate()

    imported: list[object] = []
    thread = threading.Thread(
        target=lambda: imported.append(analysis_common._safe_import("json")),
        name="gated-import",
    )
    thread.start()
    time.sleep(0.05)
    assert not imported, "a gated import must wait for the warmup thread"

    availability = analysis_common.warmup()
    assert set(availability) == {"sklearn", "hdbscan"}
    thread.join(timeout=30)
    assert imported, "warmup must release the gated imports"
