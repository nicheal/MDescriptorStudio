"""PyInstaller entry point: absolute import so the package context survives."""

import os
import sys

# Numba cache files are pickle-bearing. Keep this flag before importing the
# backend; AnalysisEngine adds the version-independent runtime NullCache guard.
os.environ["NUMBA_DISABLE_JIT_CACHE"] = "1"

from mdescriptor_studio_backend.main import main

if __name__ == "__main__":
    sys.exit(main())
