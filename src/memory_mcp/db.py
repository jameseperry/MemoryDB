"""asyncpg connection pool and shared DB helpers."""

from typing import Any

import asyncpg

from memory_mcp.config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        raise RuntimeError("Database pool not initialised — call init_pool() first")
    return _pool


async def init_pool() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.async_database_url,
        min_size=settings.db_min_connections,
        max_size=settings.db_max_connections,
    )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def resolve_workspace_id(
    conn: asyncpg.Connection, workspace: str | None
) -> int | None:
    """Resolve a workspace name to its integer ID, creating it if necessary.

    Returns None for the default workspace (workspace=None).
    """
    if workspace is None:
        return None
    row = await conn.fetchrow(
        "SELECT id FROM workspaces WHERE name = $1", workspace
    )
    if row:
        return row["id"]
    row = await conn.fetchrow(
        "INSERT INTO workspaces (name) VALUES ($1) RETURNING id", workspace
    )
    return row["id"]


def serialize(row: asyncpg.Record | dict | None) -> dict | None:
    """Convert an asyncpg Record to a plain dict."""
    if row is None:
        return None
    return dict(row)
