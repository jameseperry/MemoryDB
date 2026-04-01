"""Administrative helpers that operate directly on the database."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from memory_mcp.config import settings
from memory_mcp.db import get_pool, serialize


def _normalize_workspace_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Workspace name is required")
    return name


def _normalize_backup_path(path: str | Path) -> Path:
    backup_path = Path(path).expanduser()
    if not backup_path.name:
        raise ValueError("Backup path is required")
    return backup_path


def _database_parts() -> tuple[str, str]:
    parsed = urlsplit(settings.async_database_url)
    database = parsed.path.lstrip("/")
    user = parsed.username
    if not database:
        raise ValueError("Database name is missing from ASYNC_DATABASE_URL")
    if not user:
        raise ValueError("Database user is missing from ASYNC_DATABASE_URL")
    return user, database


def _local_database_url() -> str:
    return settings.async_database_url.replace("postgresql+psycopg2://", "postgresql://")


def _resolve_backup_method(method: str, *, need_local_tools: tuple[str, ...]) -> str:
    if method not in {"auto", "local", "docker"}:
        raise ValueError(f"Unsupported method: {method}")

    if method in {"auto", "local"}:
        if all(shutil.which(tool) for tool in need_local_tools):
            return "local"
        if method == "local":
            missing = ", ".join(tool for tool in need_local_tools if not shutil.which(tool))
            raise RuntimeError(f"Missing required local PostgreSQL tools: {missing}")

    if method in {"auto", "docker"}:
        if shutil.which("docker"):
            return "docker"
        raise RuntimeError("Docker is required for backup/restore when local PostgreSQL tools are unavailable")

    raise RuntimeError("No supported backup/restore method is available")


def _run_backup_subprocess(
    command: list[str],
    *,
    output_path: Path | None = None,
    input_path: Path | None = None,
) -> None:
    kwargs: dict = {"stderr": subprocess.PIPE}
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("wb") as handle:
            kwargs["stdout"] = handle
            try:
                subprocess.run(command, check=True, **kwargs)
            except subprocess.CalledProcessError as exc:
                detail = exc.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(detail or "Backup command failed") from exc
        return

    if input_path is not None:
        with input_path.open("rb") as handle:
            kwargs["stdin"] = handle
            kwargs["stdout"] = subprocess.DEVNULL
            try:
                subprocess.run(command, check=True, **kwargs)
            except subprocess.CalledProcessError as exc:
                detail = exc.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(detail or "Restore command failed") from exc
        return

    raise ValueError("Either output_path or input_path must be provided")


def backup_database(path: str | Path, method: str = "auto") -> dict:
    """Back up the configured database to a plain SQL file."""
    backup_path = _normalize_backup_path(path)
    user, database = _database_parts()
    resolved_method = _resolve_backup_method(method, need_local_tools=("pg_dump",))

    if resolved_method == "local":
        command = [
            "pg_dump",
            f"--dbname={_local_database_url()}",
            "--format=plain",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
        ]
    else:
        command = [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "pg_dump",
            "-U",
            user,
            "-d",
            database,
            "--format=plain",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
        ]

    _run_backup_subprocess(command, output_path=backup_path)
    return {
        "path": str(backup_path.resolve()),
        "method": resolved_method,
        "database": database,
    }


def restore_database(path: str | Path, method: str = "auto") -> dict:
    """Restore the configured database from a plain SQL file."""
    backup_path = _normalize_backup_path(path)
    if not backup_path.is_file():
        raise ValueError(f"Backup file not found: {backup_path}")

    user, database = _database_parts()
    resolved_method = _resolve_backup_method(method, need_local_tools=("psql",))

    if resolved_method == "local":
        command = [
            "psql",
            f"--dbname={_local_database_url()}",
            "-v",
            "ON_ERROR_STOP=1",
            "-1",
        ]
    else:
        command = [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            user,
            "-d",
            database,
            "-v",
            "ON_ERROR_STOP=1",
            "-1",
        ]

    _run_backup_subprocess(command, input_path=backup_path)
    return {
        "path": str(backup_path.resolve()),
        "method": resolved_method,
        "database": database,
    }


async def list_workspaces() -> list[dict]:
    """Return all workspaces ordered by name."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT name, created_at
            FROM workspaces
            ORDER BY name
            """
        )
    return [serialize(row) for row in rows]


async def create_workspace(name: str) -> dict:
    """Create a workspace if needed and report whether it was newly created."""
    name = _normalize_workspace_name(name)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO workspaces (name)
            VALUES ($1)
            ON CONFLICT (name) DO NOTHING
            RETURNING name, created_at
            """,
            name,
        )
        if row is not None:
            result = serialize(row)
            result["created"] = True
            return result

        row = await conn.fetchrow(
            """
            SELECT name, created_at
            FROM workspaces
            WHERE name = $1
            """,
            name,
        )
    if row is None:
        raise RuntimeError(f"Workspace '{name}' could not be created")
    result = serialize(row)
    result["created"] = False
    return result


async def rehome_null_workspace_nodes(workspace_name: str) -> dict:
    """Move legacy NULL-workspace rows under the provided workspace."""
    workspace_name = _normalize_workspace_name(workspace_name)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            workspace_row = await conn.fetchrow(
                """
                SELECT id, name
                FROM workspaces
                WHERE name = $1
                """,
                workspace_name,
            )
            if workspace_row is None:
                raise ValueError(f"Workspace '{workspace_name}' not found")

            target_workspace_id = workspace_row["id"]

            conflicting = await conn.fetch(
                """
                SELECT legacy.name
                FROM nodes AS legacy
                JOIN nodes AS target
                  ON target.name = legacy.name
                 AND target.workspace_id = $1
                WHERE legacy.workspace_id IS NULL
                ORDER BY legacy.name
                """,
                target_workspace_id,
            )
            if conflicting:
                names = ", ".join(row["name"] for row in conflicting)
                raise ValueError(
                    "Cannot rehome NULL-workspace nodes because the target workspace "
                    f"already has nodes with the same names: {names}"
                )

            legacy_node_ids = await conn.fetch(
                """
                SELECT id
                FROM nodes
                WHERE workspace_id IS NULL
                ORDER BY id
                """
            )
            node_ids = [row["id"] for row in legacy_node_ids]

            node_count = await conn.execute(
                """
                UPDATE nodes
                SET workspace_id = $1
                WHERE workspace_id IS NULL
                """,
                target_workspace_id,
            )

            relation_count = "0"
            event_count = "0"

            if node_ids:
                relation_count = await conn.execute(
                    """
                    UPDATE relations
                    SET workspace_id = $1
                    WHERE workspace_id IS NULL
                      AND (from_node_id = ANY($2::bigint[]) OR to_node_id = ANY($2::bigint[]))
                    """,
                    target_workspace_id,
                    node_ids,
                )
                event_count = await conn.execute(
                    """
                    UPDATE events
                    SET workspace_id = $1
                    WHERE workspace_id IS NULL
                      AND node_id = ANY($2::bigint[])
                    """,
                    target_workspace_id,
                    node_ids,
                )

    return {
        "workspace": workspace_name,
        "nodes_rehomed": int(node_count.split()[-1]),
        "relations_rehomed": int(relation_count.split()[-1]),
        "events_rehomed": int(event_count.split()[-1]),
    }


async def delete_workspace(name: str) -> dict:
    """Delete a workspace and report whether it existed."""
    name = _normalize_workspace_name(name)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            DELETE FROM workspaces
            WHERE name = $1
            RETURNING name
            """,
            name,
        )
    return {"name": name, "deleted": row is not None}
