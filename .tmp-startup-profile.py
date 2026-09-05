"""Measure backend startup phase costs (temp profiling script)."""

import os
import sys
import time

os.environ["NUMBA_DISABLE_JIT_CACHE"] = "1"

t0 = time.perf_counter()

step = "import main module"
from mdescriptor_studio_backend.main import main  # noqa

print(f"{time.perf_counter() - t0:8.2f}s  {step}", flush=True)

from mdescriptor_studio_backend.config import data_dir
from mdescriptor_studio_backend.mdescriptor_adapter import EngineAdapter
from mdescriptor_studio_backend.analysis import AnalysisEngine
from mdescriptor_studio_backend.storage.database import Database
import mdescriptor as md


def mark(step):
    print(f"{time.perf_counter() - t0:8.2f}s  {step}", flush=True)


root = data_dir()
mark("resolve data dir")
db = Database(root / "database.sqlite")
mark("Database open")
adapter = EngineAdapter()
info = adapter.runtime_info()
mark(f"runtime_info (engine {info.get('version')})")

names = adapter.list_names()
mark(f"list_descriptors ({len(names)}: {names})")

adapter.warmup()
mark("adapter.warmup()")

avail = AnalysisEngine.warmup()
mark(f"AnalysisEngine.warmup() {avail}")
