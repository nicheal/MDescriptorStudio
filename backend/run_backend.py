"""PyInstaller entry point: absolute import so the package context survives."""

import sys

from mdescriptor_studio_backend.main import main

if __name__ == "__main__":
    sys.exit(main())
