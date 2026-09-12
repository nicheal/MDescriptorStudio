import io
import json

import pytest

from mdescriptor_studio_backend.errors import AppError
from mdescriptor_studio_backend.services import update_service as updates


@pytest.mark.parametrize("latest,status", [("0.3.2", "up_to_date"), ("0.3.3", "available")])
def test_frozen_build_can_check_but_cannot_install(monkeypatch, latest, status):
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updates.threading.Thread, "start", lambda thread: thread.run())
    urls = []

    def response(request, timeout):
        urls.append(request.full_url)
        return io.BytesIO(json.dumps({"info": {"version": latest}}).encode())

    monkeypatch.setattr(updates.urllib.request, "urlopen", response)
    service = updates.UpdateService(lambda *_: None, "0.3.2")
    state = service.start_check()
    assert urls == ["https://pypi.org/pypi/mdescriptor/json"]
    assert state["status"] == status
    assert state["latest"] == latest
    assert state["installer_required"] is True
    with pytest.raises(AppError, match="frozen build"):
        service.update_runner(None, latest)


def test_failed_check_can_retry(monkeypatch):
    monkeypatch.setattr(updates.threading.Thread, "start", lambda thread: thread.run())
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *_args, **_kw: (_ for _ in ()).throw(OSError("offline")))
    service = updates.UpdateService(lambda *_: None, "0.3.2")
    assert service.start_check()["status"] == "error"
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *_args, **_kw: io.BytesIO(b'{"info":{"version":"0.3.3"}}'))
    assert service.start_check()["status"] == "available"
    assert service.snapshot()["error"] is None
