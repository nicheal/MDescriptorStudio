"""Engine update service with constrained version and pip inputs."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import urllib.request

from ..errors import AppError, INTERNAL_ERROR, INVALID_PARAMS

PYPI_JSON = "https://pypi.org/pypi/mdescriptor/json"
_VERSION_RE = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:[-.][0-9A-Za-z.-]+)?$")
_SENSITIVE_ENV = {
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "MDS_DATA_DIR",
    "TEMP",
    "TMP",
    "TMPDIR",
}


def version_tuple(v: str) -> tuple[int, ...]:
    if not isinstance(v, str) or not _VERSION_RE.fullmatch(v.strip()):
        raise ValueError("invalid package version")
    return tuple(int(p) for p in v.strip().split(".", 3)[:3])


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
            "status": "idle",
            "error": None,
            "restart_required": False,
            "installer_required": self._frozen(),
        }

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

    def start_check(self) -> dict:
        with self._lock:
            if self._checking:
                return dict(self.state)
            self._checking = True
        self._set(status="checking", error=None)
        threading.Thread(target=self._check, daemon=True, name="pypi-check").start()
        return self.snapshot()

    def _check(self) -> None:
        try:
            req = urllib.request.Request(
                PYPI_JSON, headers={"User-Agent": "MDescriptorStudio-Backend/0.1"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read(4 * 1024 * 1024).decode("utf-8"))
            latest = str(data["info"]["version"]).strip()
            version_tuple(latest)
            has_update = version_tuple(latest) > version_tuple(self.installed)
            self._set(
                latest=latest,
                has_update=has_update,
                status="available" if has_update else "up_to_date",
                error=None,
            )
        except Exception:  # noqa: BLE001 - offline/proxy failures are normal
            self._set(status="error", error="Unable to check for engine updates")
        finally:
            with self._lock:
                self._checking = False

    def update_runner(self, ctx, target_version: str):
        if self._frozen():
            raise AppError("ENGINE_UPDATE_UNSUPPORTED", "frozen build: update via new installer")
        target_version = target_version.strip() if isinstance(target_version, str) else ""
        if not target_version:
            raise AppError(INVALID_PARAMS, "target version unknown — run check first")
        try:
            version_tuple(target_version)
        except ValueError as exc:
            raise AppError(INVALID_PARAMS, "target version is invalid") from exc
        latest = self.snapshot().get("latest")
        if latest is None or target_version != latest:
            raise AppError(INVALID_PARAMS, "target version is not the latest checked release")
        ctx.progress(0, 1, f"pip install mdescriptor=={target_version}")
        clean_env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() not in _SENSITIVE_ENV and not key.upper().startswith("PIP_")
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                f"mdescriptor=={target_version}",
                "--disable-pip-version-check",
                "--no-cache-dir",
                "--no-input",
                "--only-binary=:all:",
                "--index-url",
                "https://pypi.org/simple",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=clean_env,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        tail = ""
        try:
            for line in process.stdout:
                tail = line.strip()[:160]
                # pip output can contain temporary directories, local paths,
                # proxy details, or package metadata. Keep it in the local
                # log/diagnostic tail; never stream it to the renderer.
                ctx.progress(0, 1, "installing engine update")
                ctx.check_cancelled()
            code = process.wait(timeout=600)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        if code != 0:
            self._set(status="error", error="Engine update failed")
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
            "note": "restart the backend to load the updated engine",
        }
