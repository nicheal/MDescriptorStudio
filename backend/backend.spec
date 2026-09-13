# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the MDescriptor Studio backend bundle (onedir).
# Build:  .venv\Scripts\python.exe -m PyInstaller backend.spec --noconfirm
# Output: dist\backend\  -> copied to src-tauri\resources\backend\
# (onedir: no per-launch archive extraction and no Windows Defender storm
#  over freshly extracted files, which dominated first data load after an
#  install; Tauri bundles the directory via `bundle.resources`.)

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []


def collect_runtime(package):
    """Collect package resources without shipping its test suite."""
    data, binary, hidden = collect_all(package)
    hidden = [
        module
        for module in hidden
        if module != f"{package}.tests" and ".tests" not in module
    ]
    return data, binary, hidden


# dpdata and the analysis stack register modules dynamically — static analysis
# alone would otherwise produce a sidecar that works for descriptors but fails
# only when the user first opens Analysis.
for pkg in (
    "mdescriptor",
    "mdescriptor_studio_backend",
    "dpdata",
    "sklearn",
    "scipy",
    "hdbscan",
    "array_api_compat",
):
    d, b, h = collect_runtime(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += ["numpy", "joblib"]

# Native statistics geometry kernel (optional at runtime — datasets.native
# falls back to scipy when absent).  Bundled at its package-relative location
# so datasets/native.py's lookup works unchanged in the frozen sidecar.
import os

_mds_native_dll = os.path.join(
    SPECPATH, "mdescriptor_studio_backend", "datasets", "_native", "mds_native.dll"
)
if os.path.exists(_mds_native_dll):
    binaries.append((_mds_native_dll, "mdescriptor_studio_backend/datasets/_native"))

a = Analysis(
    ["run_backend.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "PyInstaller"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # stdio protocol transport
    icon="../src-tauri/icons/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="backend",
)
