"""asyncpg connection pool and shared DB helpers."""

from typing import Any

import asyncpg
from fastmcp.server.dependencies import get_http_headers

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
) -> int:
    """Resolve a workspace name to its integer ID.

    The effective workspace must be provided either explicitly or via the
    configured HTTP header, and it must already exist.
    """
    workspace = resolve_effective_workspace_name(workspace)
    row = await conn.fetchrow(
        "SELECT id FROM workspaces WHERE name = $1", workspace
    )
    if row is None:
        raise ValueError(f"Workspace '{workspace}' not found")
    return row["id"]


def resolve_effective_workspace_name(workspace: str | None) -> str:
    """Resolve the effective workspace name for the current request.

    Precedence:
    1. `X-Memory-Workspace` header, when present on an HTTP MCP request
    2. Explicit `workspace` argument for direct/internal callers

    If both are present they must match, otherwise the request is rejected.
    """
    header_key = settings.mcp_workspace_header.lower()
    header_workspace = get_http_headers().get(header_key)
    if header_workspace is not None:
        header_workspace = header_workspace.strip()
        if not header_workspace:
            raise ValueError(f"{settings.mcp_workspace_header} header cannot be empty")
        if workspace is not None and workspace != header_workspace:
            raise ValueError(
                "Workspace parameter does not match "
                f"{settings.mcp_workspace_header} header"
            )
        return header_workspace
    if workspace is None:
        raise ValueError("Workspace is required")
    workspace = workspace.strip()
    if not workspace:
        raise ValueError("Workspace is required")
    return workspace


def serialize(row: asyncpg.Record | dict | None) -> dict | None:
    """Convert an asyncpg Record to a plain dict."""
    if row is None:
        return None
    return dict(row)
