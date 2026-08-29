"""Engine update service: PyPI version check + in-venv upgrade (ADR-2 note:
this automates the pin bump; re-run scripts/probe_engine.py + pytest after).

Frozen (PyInstaller) builds cannot upgrade themselves — the frontend shows a
"download new installer" hint instead (ENGINE_UPDATE_UNSUPPORTED).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import urllib.request

from ..errors import AppError, INTERNAL_ERROR, INVALID_PARAMS

PYPI_JSON = "https://pypi.org/pypi/mdescriptor/json"


def version_tuple(v: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", v)[:3]
    while len(parts) < 3:
        parts.append("0")
    return tuple(int(p) for p in parts)


class UpdateService:
    def __init__(self, emit, installed_version: str):
        self.emit = emit
        self._lock = threading.Lock()
        self._checking = False
        self.installed = installed_version
        self.state: dict = {
            "installed": installed_version,
            "latest": None,
            "has_update": False,
            "status": "idle",  # idle|checking|up_to_date|available|error|unsupported
            "error": None,
            "restart_required": False,
        }

    # -- state ---------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return dict(self.state)

    def _set(self, **kv) -> None:
        with self._lock:
            self.state.update(kv)
            snap = dict(self.state)
        self.emit("engine.update.state", snap)

    @staticmethod
    def _frozen() -> bool:
        return getattr(sys, "frozen", False)

    # -- check -----------------------------------------------------------------
    def start_check(self) -> dict:
        """Kick off a background PyPI check; returns the current snapshot."""
        if self._frozen():
            self._set(status="unsupported", error="frozen build: update via new installer")
            return self.snapshot()
        with self._lock:
            if self._checking:
                return dict(self.state)
            self._checking = True
        self._set(status="checking")
        threading.Thread(target=self._check, daemon=True, name="pypi-check").start()
        return self.snapshot()

    def _check(self) -> None:
        try:
            req = urllib.request.Request(
                PYPI_JSON, headers={"User-Agent": "MDescriptorStudio-Backend/0.1"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            latest = str(data["info"]["version"]).strip()
            has_update = version_tuple(latest) > version_tuple(self.installed)
            self._set(
                latest=latest,
                has_update=has_update,
                status="available" if has_update else "up_to_date",
                error=None,
            )
        except Exception as exc:  # noqa: BLE001 - offline/proxy failures are normal
            self._set(status="error", error=str(exc))
        finally:
            with self._lock:
                self._checking = False

    # -- update (job runner) -----------------------------------------------------
    def update_runner(self, ctx, target_version: str):
        if self._frozen():
            raise AppError("ENGINE_UPDATE_UNSUPPORTED", "frozen build: update via new installer")
        if not target_version:
            raise AppError(INVALID_PARAMS, "target version unknown — run check first")
        ctx.progress(0, 1, f"pip install mdescriptor=={target_version}")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                f"mdescriptor=={target_version}",
                "--disable-pip-version-check",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        tail = ""
        try:
            for line in process.stdout:
                tail = line.strip()[:160]
                ctx.progress(0, 1, f"pip: {tail}")
                ctx.check_cancelled()
            code = process.wait(timeout=600)
        finally:
            # a cancelled job must not leave pip finishing the install in the
            # background (red-team follow-up)
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        if code != 0:
            self._set(status="error", error=f"pip exit {code}: {tail}")
            raise AppError(INTERNAL_ERROR, f"pip upgrade failed (exit {code}): {tail}")
        self._set(
            latest=target_version,
            has_update=False,
            status="up_to_date",
            restart_required=True,
        )
        ctx.progress(1, 1, "done — restart backend to load the new engine")
        return {
            "version": target_version,
            "restart_required": True,
            "note": "rerun scripts/probe_engine.py + pytest to refresh the API baseline (ADR-2)",
        }
