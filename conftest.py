"""Global Pytest Configuration and Test Database Isolation Harness.

Guarantees 100% database immutability by redirecting all test executions to
an isolated temporary clone of instance/phase9_smartflow_submission.db.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from sqlalchemy import create_engine

BASE_DIR = Path(os.path.abspath(os.path.dirname(__file__)))
SUBMISSION_DB_PATH = BASE_DIR / "instance" / "phase9_smartflow_submission.db"
V2_DB_PATH = BASE_DIR / "instance" / "phase9_smartflow_v2.db"
PRIMARY_TEST_DB = SUBMISSION_DB_PATH if SUBMISSION_DB_PATH.exists() else V2_DB_PATH

_temp_test_dir: str | None = None
_temp_db_path: str | None = None
_isolated_engine = None


def pytest_configure(config):
    """Run once before any tests or fixtures are executed."""
    global _temp_test_dir, _temp_db_path, _isolated_engine

    if not PRIMARY_TEST_DB.exists():
        return

    # Create temporary directory and clone the pristine submission database
    _temp_test_dir = tempfile.mkdtemp(prefix="smartflow_pytest_")
    _temp_db_path = os.path.join(_temp_test_dir, "isolated_pytest_submission.db")
    shutil.copy2(str(PRIMARY_TEST_DB), _temp_db_path)

    # Set DATABASE_URL environment variable
    os.environ["DATABASE_URL"] = f"sqlite:///{_temp_db_path}"

    # Pre-emptively rebind Flask-SQLAlchemy application engine
    try:
        from app import app, db
        _isolated_engine = create_engine(f"sqlite:///{_temp_db_path}")
        db._app_engines[app] = {None: _isolated_engine}
    except Exception:
        pass


def pytest_unconfigure(config):
    """Clean up temporary database after all tests complete."""
    global _temp_test_dir, _isolated_engine

    try:
        from app import app, db
        if _isolated_engine:
            _isolated_engine.dispose()
    except Exception:
        pass

    if "DATABASE_URL" in os.environ:
        del os.environ["DATABASE_URL"]

    if _temp_test_dir and os.path.exists(_temp_test_dir):
        shutil.rmtree(_temp_test_dir, ignore_errors=True)
