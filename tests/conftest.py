"""Shared fixtures for all tests.

Isolation strategy: every test runs against a dedicated workspace ("__test__").
Deleting that workspace cascades to all its nodes, observations, relations,
embeddings, and events — the default workspace (real memory graph) is never touched.
"""

import asyncio
import os

import pytest
import pytest_asyncio

os.environ.setdefault("ASYNC_DATABASE_URL", "postgresql://memory:memory@localhost:5432/memory")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://memory:memory@localhost:5432/memory")

from memory_mcp.db import close_pool, get_pool, init_pool

TEST_WORKSPACE = "__test__"


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
    """Initialize the asyncpg pool once for the whole test session."""
    await init_pool()
    yield
    await close_pool()


@pytest_asyncio.fixture(autouse=True)
async def isolated_workspace(db_pool):
    """Drop and recreate the test workspace around every test.

    autouse=True means every test gets a clean slate automatically,
    with no explicit fixture reference needed.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM workspaces WHERE name = $1", TEST_WORKSPACE)
    yield
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM workspaces WHERE name = $1", TEST_WORKSPACE)


@pytest.fixture
def ws():
    """Convenience: the workspace name string to pass into every tool call."""
    return TEST_WORKSPACE
