# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the MDescriptor Studio backend sidecar (onefile).
# Build:  .venv\Scripts\python.exe -m PyInstaller backend.spec --noconfirm
# Output: dist\backend.exe  -> copied to src-tauri\binaries\backend-<triple>.exe
# (onefile: Tauri externalBin carries a single file; extraction adds a few
#  seconds to first launch, acceptable vs. shipping a sidecar directory.)

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
    "umap",
    "hdbscan",
):
    d, b, h = collect_runtime(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += ["numpy", "joblib", "numba", "llvmlite"]

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
    a.binaries,
    a.datas,
    [],
    name="backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # stdio protocol transport
    icon="../src-tauri/icons/icon.ico",
)
