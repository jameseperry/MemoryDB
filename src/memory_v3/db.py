"""asyncpg connection pool and shared DB helpers for v3."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

import asyncpg
from fastmcp.server.dependencies import get_context, get_http_headers

from memory_v3.config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        raise RuntimeError("v3 database pool not initialised — call init_pool() first")
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


def resolve_effective_workspace_name(workspace: str | None) -> str:
    """Resolve the effective workspace name for the current request."""
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


async def resolve_workspace_id(
    conn: asyncpg.Connection,
    workspace: str | None,
) -> int:
    """Resolve a workspace name to its integer ID."""
    workspace_name = resolve_effective_workspace_name(workspace)
    workspace_id = await conn.fetchval(
        "SELECT id FROM workspaces WHERE name = $1",
        workspace_name,
    )
    if workspace_id is None:
        raise ValueError(f"Workspace '{workspace_name}' not found")
    return workspace_id


def resolve_effective_session_id(session_id: str | None = None) -> str:
    """Resolve the effective provenance session ID for the current request."""
    header_key = settings.mcp_session_header.lower()
    header_session_id = get_http_headers().get(header_key)
    if header_session_id is not None:
        header_session_id = header_session_id.strip()
        if not header_session_id:
            raise ValueError(f"{settings.mcp_session_header} header cannot be empty")
        if session_id is not None and session_id != header_session_id:
            raise ValueError(
                "Session parameter does not match "
                f"{settings.mcp_session_header} header"
            )
        return header_session_id

    if session_id is not None:
        session_id = session_id.strip()
        if not session_id:
            raise ValueError("Session ID cannot be empty")
        return session_id

    try:
        return get_context().session_id
    except Exception as exc:  # pragma: no cover - direct callers can pass explicit session_id
        raise ValueError("Session ID is required") from exc


def _parse_bool_header(value: str, *, header_name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(
        f"{header_name} header must be a boolean value like true/false"
    )


def resolve_effective_readonly(readonly: bool | None = None) -> bool:
    """Resolve the effective readonly flag for the current request."""
    header_key = settings.mcp_readonly_header.lower()
    header_readonly = get_http_headers().get(header_key)
    if header_readonly is not None:
        parsed_header = _parse_bool_header(
            header_readonly,
            header_name=settings.mcp_readonly_header,
        )
        if readonly is not None and readonly != parsed_header:
            raise ValueError(
                "Readonly parameter does not match "
                f"{settings.mcp_readonly_header} header"
            )
        return parsed_header
    return bool(readonly)


def ensure_request_writable(readonly: bool | None = None) -> None:
    """Raise if the current request is marked readonly."""
    if resolve_effective_readonly(readonly):
        raise PermissionError(
            f"{settings.mcp_readonly_header} forbids mutation for this request"
        )


def resolve_optional_session_id(session_id: str | None = None) -> str:
    """Resolve a session ID, falling back to a stable internal sentinel."""
    try:
        return resolve_effective_session_id(session_id)
    except ValueError:
        return "internal"


def hash_content(content: str) -> str:
    """Stable SHA-256 hash for observation deduplication."""
    normalized = content.strip()
    return sha256(normalized.encode("utf-8")).hexdigest()


async def get_workspace_generation(
    conn: asyncpg.Connection,
    workspace_id: int,
) -> int:
    """Return the current workspace generation index."""
    generation = await conn.fetchval(
        "SELECT current_generation FROM workspaces WHERE id = $1",
        workspace_id,
    )
    if generation is None:
        raise ValueError(f"Workspace ID {workspace_id} not found")
    return generation


async def record_event(
    conn: asyncpg.Connection,
    *,
    workspace_id: int,
    session_id: str,
    operation: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append a structured event row."""
    event_session_id = await resolve_session_id(
        conn,
        workspace_id=workspace_id,
        session_token=session_id,
    )
    await conn.execute(
        """
        INSERT INTO events (workspace_id, session_id, operation, detail)
        VALUES ($1, $2, $3, $4::jsonb)
        """,
        workspace_id,
        event_session_id,
        operation,
        json.dumps(detail) if detail is not None else None,
    )


async def resolve_session_id(
    conn: asyncpg.Connection,
    *,
    workspace_id: int,
    session_token: str,
    create: bool = False,
) -> int:
    """Resolve a transport session token to the workspace-local session row ID.

    By default, looks up an existing session and updates its timestamp.
    Pass create=True to create the session if it doesn't exist (used by
    orient and rejoin_session only).
    """
    if create:
        row = await conn.fetchrow(
            """
            INSERT INTO sessions (workspace_id, session_token, started_at, updated_at)
            VALUES ($1, $2, NOW(), NOW())
            ON CONFLICT (workspace_id, session_token)
                DO UPDATE SET updated_at = NOW()
            RETURNING session_id
            """,
            workspace_id,
            session_token,
        )
        return row["session_id"]

    row = await conn.fetchrow(
        """
        UPDATE sessions
        SET updated_at = NOW()
        WHERE workspace_id = $1 AND session_token = $2
        RETURNING session_id
        """,
        workspace_id,
        session_token,
    )
    if row is None:
        raise ValueError(
            "Session not found. Call orient() to start a new session, "
            "or rejoin_session(session_id) to reconnect to an existing one."
        )
    return row["session_id"]


def serialize(row: asyncpg.Record | dict | None) -> dict | None:
    """Convert an asyncpg Record to a plain dict."""
    if row is None:
        return None
    return dict(row)
