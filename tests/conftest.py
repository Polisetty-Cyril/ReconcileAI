"""
ReconcileAI - Global Pytest Configuration and Fixtures
Provides narrowly scoped test-cleanup context exclusively during fixture setup
and teardown, ensuring existing test isolation fixtures can purge test records
while keeping all test execution bodies and production runtimes strictly immutable.
"""

from __future__ import annotations

import pytest
from backend.models.audit import audit_log_cleanup_context


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
