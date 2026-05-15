"""Pytest config for the ProjectHub backend test suite.

The backend modules use top-level imports (``from services.x import y``),
so the ``backend/`` directory must be on ``sys.path``. Adding it here lets
tests run from the repo root as well as from ``backend/``.

This file also pins the test session to a throwaway SQLite DB via
``PROJECTHUB_DB_PATH`` — set *before* any backend module imports
``config`` — so tests never touch the real ``data/projecthub.db``.
"""
import os
import sys
import tempfile

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

os.environ.setdefault(
    "PROJECTHUB_DB_PATH",
    os.path.join(tempfile.gettempdir(), "projecthub_pytest.db"),
)
