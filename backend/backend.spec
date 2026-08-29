# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the MDescriptor Studio backend sidecar (onefile).
# Build:  .venv\Scripts\python.exe -m PyInstaller backend.spec --noconfirm
# Output: dist\backend.exe  -> copied to src-tauri\binaries\backend-<triple>.exe
# (onefile: Tauri externalBin carries a single file; extraction adds a few
#  seconds to first launch, acceptable vs. shipping a sidecar directory.)

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
# dpdata (ADR-19: DeepMD import) registers format plugins via dynamic
# importlib imports — static analysis alone would miss them.
for pkg in ("mdescriptor", "mdescriptor_studio_backend", "dpdata"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
hiddenimports += ["numpy"]

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

