"""
ReconcileAI - Global Pytest Configuration and Fixtures
Provides:
1. Isolated, disposable SQLite test database (prevents modifying persistent reconcile_ai.db)
2. Narrowly scoped test-cleanup context exclusively during fixture setup and teardown
   ensuring existing test isolation fixtures can purge test records while keeping all
   test execution bodies and production runtimes strictly immutable.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import pytest
from sqlalchemy import create_engine

# ---------------------------------------------------------------------------
# 1. Test Database Isolation Setup (Runs before any backend imports)
# ---------------------------------------------------------------------------
_ORIG_DATABASE_URL = os.environ.get("DATABASE_URL")
_TEMP_DIR = tempfile.mkdtemp(prefix="reconcile_ai_test_")
_TEST_DB_PATH = os.path.join(_TEMP_DIR, "reconcile_ai_test.db")
_TEST_DB_URL = f"sqlite:///{_TEST_DB_PATH}"

# Point environment variable to disposable test database
os.environ["DATABASE_URL"] = _TEST_DB_URL

# Import backend modules after setting DATABASE_URL
from backend.config import settings
import backend.database as db_module
from backend.database import engine, SessionLocal, init_db
from backend.models.audit import audit_log_cleanup_context

# Ensure settings and engine/SessionLocal explicitly point to the test DB
settings.DATABASE_URL = _TEST_DB_URL

if str(db_module.engine.url) != _TEST_DB_URL:
    db_module.engine.dispose()
    new_engine = create_engine(
        _TEST_DB_URL,
        connect_args={"check_same_thread": False},
        echo=False
    )
    db_module.engine = new_engine
    db_module.SessionLocal.configure(bind=new_engine)

# Initialize schema in disposable test database
init_db()


# ---------------------------------------------------------------------------
# 2. Test Database Lifecycle Fixture
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def test_database_lifecycle():
    """Ensures isolated test database lifecycle and cleans up temporary resources."""
    yield
    # Dispose engine to release Windows file locks
    try:
        db_module.engine.dispose()
    except Exception:
        pass
    # Clean up temporary database directory
    try:
        if os.path.exists(_TEMP_DIR):
            shutil.rmtree(_TEMP_DIR, ignore_errors=True)
    except Exception:
        pass
    # Restore original environment
    if _ORIG_DATABASE_URL is not None:
        os.environ["DATABASE_URL"] = _ORIG_DATABASE_URL
    else:
        os.environ.pop("DATABASE_URL", None)


# ---------------------------------------------------------------------------
# 3. Audit Immutability Test Context Hooks
# ---------------------------------------------------------------------------
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    """Enables audit cleanup context during test setup fixtures."""
    with audit_log_cleanup_context():
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    """Enables audit cleanup context during test teardown fixtures."""
    with audit_log_cleanup_context():
        yield


@pytest.hookimpl(hookwrapper=True)
def pytest_fixture_setup(fixturedef, request):
    """Enables audit cleanup context during fixture setup and execution."""
    with audit_log_cleanup_context():
        yield
