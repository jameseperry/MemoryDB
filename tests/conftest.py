"""Shared test fixtures."""

import asyncio

import pytest
import pytest_asyncio

TEST_WORKSPACE = "__test__"
OTHER_TEST_WORKSPACE = "__test_other__"


@pytest.fixture(scope="session")
def event_loop():
    """Single event loop shared across the entire test session.

    Required so the session-scoped asyncpg pool and all tests/fixtures
    live in the same loop — asyncpg connections are not loop-portable.
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def db_pool():
    """No-op fixture kept for tests that override DB plumbing locally."""
    yield


@pytest_asyncio.fixture(autouse=True)
async def isolated_workspace(db_pool):
    """No-op fixture kept for tests that do not touch a real Postgres DB."""
    yield


@pytest.fixture
def ws():
    """Convenience: the workspace name string to pass into every tool call."""
    return TEST_WORKSPACE


@pytest.fixture
def other_ws():
    """A second isolated workspace for cross-workspace assertions."""
    return OTHER_TEST_WORKSPACE
