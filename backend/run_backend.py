"""PyInstaller entry point: absolute import so the package context survives."""

import sys

from mdescriptor_studio_backend.main import main

if __name__ == "__main__":
    if "--mdescriptor-analysis-worker" in sys.argv[1:]:
        from mdescriptor_studio_backend.services.analysis_worker import run_analysis_subprocess

        run_analysis_subprocess()
        raise SystemExit(0)
    sys.exit(main())
