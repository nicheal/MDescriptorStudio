"""Thread-safety tests for the deferred engine warmup coordination."""

from __future__ import annotations

import threading
import time

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
    assert adapter._warming is False
    assert adapter._warmup_owner is None


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
    assert max_active == 1
    assert adapter._warming is False
    assert adapter._warmup_owner is None
