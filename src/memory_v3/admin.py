"""Administrative helpers for the v3 database."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from memory_v3 import tools
from memory_v3.config import settings
from memory_v3.db import get_pool, serialize
from memory_v3.embeddings import embed_targets


def _normalize_workspace_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Workspace name is required")
    return name


def _normalize_subject_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Subject name is required")
    return name


def _normalize_object_id(object_id: int) -> int:
    if object_id <= 0:
        raise ValueError("ID must be a positive integer")
    return object_id


def _normalize_file_path(path: str | Path) -> Path:
    file_path = Path(path).expanduser()
    if not file_path.name:
        raise ValueError("File path is required")
    return file_path


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _parse_timestamp(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise TypeError(
        f"Expected ISO timestamp string or datetime, got {type(value).__name__}"
    )


def _emit_import_progress(
    progress: Callable[[str, int], None] | None,
    label: str,
    advance: int = 1,
) -> None:
    if progress is not None:
        progress(label, advance)


def _database_parts() -> tuple[str, str]:
    parsed = urlsplit(settings.async_database_url)
    user = parsed.username
    database = parsed.path.lstrip("/")
    if not user:
        raise ValueError("Database user is missing from ASYNC_DATABASE_URL_V3")
    if not database:
        raise ValueError("Database name is missing from ASYNC_DATABASE_URL_V3")
    return user, database


def _local_database_url() -> str:
    return settings.async_database_url.replace("postgresql+psycopg2://", "postgresql://")


def _resolve_database_method(method: str, *, required_local_tools: tuple[str, ...]) -> str:
    if method not in {"auto", "local", "docker"}:
        raise ValueError(f"Unsupported method: {method}")

    if method in {"auto", "local"}:
        if all(shutil.which(tool) for tool in required_local_tools):
            return "local"
        if method == "local":
            missing = ", ".join(
                tool for tool in required_local_tools if not shutil.which(tool)
            )
            raise RuntimeError(f"Missing required local PostgreSQL tools: {missing}")

    if method in {"auto", "docker"}:
        if shutil.which("docker"):
            return "docker"
        raise RuntimeError(
            "Docker is required for backup/restore when local PostgreSQL tools are unavailable"
        )

    raise RuntimeError("No supported backup/restore method is available")


def _run_database_subprocess(
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


async def _get_workspace_id(conn, workspace: str) -> int:
    workspace = _normalize_workspace_name(workspace)
    workspace_id = await conn.fetchval(
        "SELECT id FROM workspaces WHERE name = $1",
        workspace,
    )
    if workspace_id is None:
        raise ValueError(f"Workspace '{workspace}' not found")
    return workspace_id


async def _cleanup_target_metadata(conn, target_id: int) -> None:
    await conn.execute("DELETE FROM embeddings WHERE target_id = $1", target_id)
    await conn.execute("DELETE FROM id_registry WHERE id = $1", target_id)


async def _subject_names_for_observations(conn, observation_ids: list[int]) -> dict[int, list[str]]:
    if not observation_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT os.observation_id, s.name
        FROM observation_subjects os
        JOIN subjects s ON s.id = os.subject_id
        WHERE os.observation_id = ANY($1)
        ORDER BY s.name
        """,
        observation_ids,
    )
    names_by_id: dict[int, list[str]] = {}
    for row in rows:
        names_by_id.setdefault(row["observation_id"], []).append(row["name"])
    return names_by_id


async def _subject_names_for_understandings(conn, understanding_ids: list[int]) -> dict[int, list[str]]:
    if not understanding_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT us.understanding_id, s.name
        FROM understanding_subjects us
        JOIN subjects s ON s.id = us.subject_id
        WHERE us.understanding_id = ANY($1)
        ORDER BY s.name
        """,
        understanding_ids,
    )
    names_by_id: dict[int, list[str]] = {}
    for row in rows:
        names_by_id.setdefault(row["understanding_id"], []).append(row["name"])
    return names_by_id


def backup_database(path: str | Path, method: str = "auto") -> dict:
    """Back up the v3 database to a plain SQL file."""
    backup_path = _normalize_file_path(path)
    user, database = _database_parts()
    resolved_method = _resolve_database_method(
        method,
        required_local_tools=("pg_dump",),
    )

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

    _run_database_subprocess(command, output_path=backup_path)
    return {
        "path": str(backup_path.resolve()),
        "method": resolved_method,
        "database": database,
    }


def restore_database(path: str | Path, method: str = "auto") -> dict:
    """Restore the v3 database from a plain SQL backup file."""
    backup_path = _normalize_file_path(path)
    if not backup_path.is_file():
        raise ValueError(f"Backup file not found: {backup_path}")

    user, database = _database_parts()
    resolved_method = _resolve_database_method(
        method,
        required_local_tools=("psql",),
    )

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

    _run_database_subprocess(command, input_path=backup_path)
    return {
        "path": str(backup_path.resolve()),
        "method": resolved_method,
        "database": database,
    }


async def list_workspaces() -> list[dict]:
    """Return all v3 workspaces ordered by name."""
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
    """Create a v3 workspace if needed and report whether it was newly created."""
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


async def delete_workspace(name: str) -> dict:
    """Delete a v3 workspace and report whether it existed."""
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


async def set_workspace_document_ids(
    name: str,
    *,
    soul_id: int | None = None,
    protocol_id: int | None = None,
    orientation_id: int | None = None,
) -> dict:
    """Set workspace special-understanding pointers."""
    name = _normalize_workspace_name(name)
    updates = {
        "soul_understanding_id": soul_id,
        "protocol_understanding_id": protocol_id,
        "orientation_understanding_id": orientation_id,
    }
    provided = {column: value for column, value in updates.items() if value is not None}
    if not provided:
        raise ValueError("At least one document ID must be provided")

    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace_row = await conn.fetchrow(
            """
            SELECT id, name, soul_understanding_id, protocol_understanding_id, orientation_understanding_id
            FROM workspaces
            WHERE name = $1
            """,
            name,
        )
        if workspace_row is None:
            raise ValueError(f"Workspace '{name}' not found")

        workspace_id = workspace_row["id"]
        for document_id in provided.values():
            understanding_row = await conn.fetchrow(
                """
                SELECT id
                FROM understandings
                WHERE id = $1
                  AND workspace_id = $2
                  AND superseded_by IS NULL
                """,
                document_id,
                workspace_id,
            )
            if understanding_row is None:
                raise ValueError(
                    f"Understanding {document_id} not found in workspace '{name}' or not active"
                )

        set_clauses = []
        args: list[object] = []
        for index, (column, value) in enumerate(provided.items(), start=2):
            set_clauses.append(f"{column} = ${index}")
            args.append(value)

        row = await conn.fetchrow(
            f"""
            UPDATE workspaces
            SET {", ".join(set_clauses)}
            WHERE name = $1
            RETURNING name, soul_understanding_id, protocol_understanding_id, orientation_understanding_id
            """,
            name,
            *args,
        )

    return serialize(row)


async def _collect_workspace_ids(conn, workspace_id: int) -> dict[str, list[int]]:
    subject_rows = await conn.fetch(
        "SELECT id FROM subjects WHERE workspace_id = $1 ORDER BY id",
        workspace_id,
    )
    observation_rows = await conn.fetch(
        "SELECT id FROM observations WHERE workspace_id = $1 ORDER BY id",
        workspace_id,
    )
    understanding_rows = await conn.fetch(
        "SELECT id FROM understandings WHERE workspace_id = $1 ORDER BY id",
        workspace_id,
    )
    perspective_rows = await conn.fetch(
        "SELECT id FROM perspectives WHERE workspace_id = $1 ORDER BY id",
        workspace_id,
    )
    target_ids = [row["id"] for row in observation_rows] + [
        row["id"] for row in understanding_rows
    ]
    utility_signal_rows = []
    surfaced_rows = []
    if target_ids:
        utility_signal_rows = await conn.fetch(
            "SELECT id FROM utility_signals WHERE target_id = ANY($1) ORDER BY id",
            target_ids,
        )
        surfaced_rows = await conn.fetch(
            "SELECT DISTINCT id FROM surfaced_in_session WHERE id = ANY($1) ORDER BY id",
            target_ids,
        )
    event_rows = await conn.fetch(
        "SELECT id FROM events WHERE workspace_id = $1 ORDER BY id",
        workspace_id,
    )
    return {
        "subject_ids": [row["id"] for row in subject_rows],
        "observation_ids": [row["id"] for row in observation_rows],
        "understanding_ids": [row["id"] for row in understanding_rows],
        "perspective_ids": [row["id"] for row in perspective_rows],
        "utility_signal_ids": [row["id"] for row in utility_signal_rows],
        "surfaced_ids": [row["id"] for row in surfaced_rows],
        "event_ids": [row["id"] for row in event_rows],
    }


async def reset_workspace(name: str) -> dict:
    """Delete all v3 data in a workspace while preserving the workspace row."""
    name = _normalize_workspace_name(name)
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            workspace_id = await _get_workspace_id(conn, name)
            ids = await _collect_workspace_ids(conn, workspace_id)

            await conn.execute(
                """
                UPDATE workspaces
                SET
                    soul_understanding_id = NULL,
                    protocol_understanding_id = NULL,
                    orientation_understanding_id = NULL,
                    current_generation = 0,
                    last_consolidated_at = NULL
                WHERE id = $1
                """,
                workspace_id,
            )
            await conn.execute(
                """
                UPDATE subjects
                SET
                    single_subject_understanding_id = NULL,
                    structural_understanding_id = NULL
                WHERE workspace_id = $1
                """,
                workspace_id,
            )
            await conn.execute(
                """
                UPDATE understandings
                SET superseded_by = NULL
                WHERE workspace_id = $1
                """,
                workspace_id,
            )
            if ids["surfaced_ids"]:
                await conn.execute(
                    """
                    DELETE FROM surfaced_in_session
                    WHERE id = ANY($1)
                    """,
                    ids["surfaced_ids"],
                )
            if ids["utility_signal_ids"]:
                await conn.execute(
                    "DELETE FROM utility_signals WHERE id = ANY($1)",
                    ids["utility_signal_ids"],
                )
            if ids["understanding_ids"]:
                await conn.execute(
                    "DELETE FROM understandings WHERE id = ANY($1)",
                    ids["understanding_ids"],
                )
            if ids["observation_ids"]:
                await conn.execute(
                    "DELETE FROM observations WHERE id = ANY($1)",
                    ids["observation_ids"],
                )
            if ids["subject_ids"]:
                await conn.execute(
                    "DELETE FROM subjects WHERE id = ANY($1)",
                    ids["subject_ids"],
                )
            if ids["perspective_ids"]:
                await conn.execute(
                    "DELETE FROM perspectives WHERE id = ANY($1)",
                    ids["perspective_ids"],
                )
            if ids["event_ids"]:
                await conn.execute(
                    "DELETE FROM events WHERE id = ANY($1)",
                    ids["event_ids"],
                )
            cleanup_ids = (
                ids["subject_ids"]
                + ids["observation_ids"]
                + ids["understanding_ids"]
                + ids["perspective_ids"]
                + ids["utility_signal_ids"]
                + ids["event_ids"]
            )
            if ids["observation_ids"] or ids["understanding_ids"]:
                await conn.execute(
                    "DELETE FROM embeddings WHERE target_id = ANY($1)",
                    ids["observation_ids"] + ids["understanding_ids"],
                )
            if cleanup_ids:
                await conn.execute(
                    "DELETE FROM id_registry WHERE id = ANY($1)",
                    cleanup_ids,
                )

    return {
        "name": name,
        "subjects_deleted": len(ids["subject_ids"]),
        "observations_deleted": len(ids["observation_ids"]),
        "understandings_deleted": len(ids["understanding_ids"]),
        "perspectives_deleted": len(ids["perspective_ids"]),
        "utility_signals_deleted": len(ids["utility_signal_ids"]),
        "events_deleted": len(ids["event_ids"]),
    }


async def export_workspace(name: str, path: str | Path) -> dict:
    """Export one workspace to a portable JSON snapshot."""
    name = _normalize_workspace_name(name)
    export_path = _normalize_file_path(path)
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace_id = await _get_workspace_id(conn, name)
        workspace_row = await conn.fetchrow(
            """
            SELECT
                name,
                soul_understanding_id,
                protocol_understanding_id,
                orientation_understanding_id,
                current_generation,
                last_consolidated_at,
                created_at
            FROM workspaces
            WHERE id = $1
            """,
            workspace_id,
        )
        subjects = [
            serialize(row)
            for row in await conn.fetch(
                """
                SELECT
                    id,
                    name,
                    summary,
                    tags,
                    created_at,
                    single_subject_understanding_id,
                    structural_understanding_id
                FROM subjects
                WHERE workspace_id = $1
                ORDER BY id
                """,
                workspace_id,
            )
        ]
        observations = [
            serialize(row)
            for row in await conn.fetch(
                """
                SELECT
                    id,
                    content,
                    content_hash,
                    kind,
                    confidence,
                    generation,
                    observed_at,
                    session_id,
                    model_tier,
                    created_at
                FROM observations
                WHERE workspace_id = $1
                ORDER BY id
                """,
                workspace_id,
            )
        ]
        understandings = [
            serialize(row)
            for row in await conn.fetch(
                """
                SELECT
                    id,
                    content,
                    summary,
                    kind,
                    generation,
                    session_id,
                    model_tier,
                    reason,
                    created_at,
                    superseded_by
                FROM understandings
                WHERE workspace_id = $1
                ORDER BY id
                """,
                workspace_id,
            )
        ]
        observation_subjects = [
            serialize(row)
            for row in await conn.fetch(
                """
                SELECT os.observation_id, os.subject_id
                FROM observation_subjects os
                JOIN observations o ON o.id = os.observation_id
                WHERE o.workspace_id = $1
                ORDER BY os.observation_id, os.subject_id
                """,
                workspace_id,
            )
        ]
        understanding_subjects = [
            serialize(row)
            for row in await conn.fetch(
                """
                SELECT us.understanding_id, us.subject_id
                FROM understanding_subjects us
                JOIN understandings u ON u.id = us.understanding_id
                WHERE u.workspace_id = $1
                ORDER BY us.understanding_id, us.subject_id
                """,
                workspace_id,
            )
        ]
        understanding_sources = [
            serialize(row)
            for row in await conn.fetch(
                """
                SELECT src.understanding_id, src.observation_id
                FROM understanding_sources src
                JOIN understandings u ON u.id = src.understanding_id
                WHERE u.workspace_id = $1
                ORDER BY src.understanding_id, src.observation_id
                """,
                workspace_id,
            )
        ]
        perspectives = [
            serialize(row)
            for row in await conn.fetch(
                """
                SELECT id, name, instruction, is_default
                FROM perspectives
                WHERE workspace_id = $1
                ORDER BY id
                """,
                workspace_id,
            )
        ]
        target_ids = [row["id"] for row in observations] + [
            row["id"] for row in understandings
        ]
        utility_signals = []
        if target_ids:
            utility_signals = [
                serialize(row)
                for row in await conn.fetch(
                    """
                    SELECT id, target_id, signal_type, reason, session_id, created_at
                    FROM utility_signals
                    WHERE target_id = ANY($1)
                    ORDER BY id
                    """,
                    target_ids,
                )
            ]
        events = [
            serialize(row)
            for row in await conn.fetch(
                """
                SELECT id, session_id, timestamp, operation, detail
                FROM events
                WHERE workspace_id = $1
                ORDER BY id
                """,
                workspace_id,
            )
        ]

    payload = {
        "schema_version": 3,
        "workspace": serialize(workspace_row),
        "subjects": subjects,
        "observations": observations,
        "understandings": understandings,
        "observation_subjects": observation_subjects,
        "understanding_subjects": understanding_subjects,
        "understanding_sources": understanding_sources,
        "perspectives": perspectives,
        "utility_signals": utility_signals,
        "events": events,
    }
    export_path.parent.mkdir(parents=True, exist_ok=True)
    export_path.write_text(
        json.dumps(payload, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return {
        "name": name,
        "path": str(export_path.resolve()),
        "subjects_exported": len(subjects),
        "observations_exported": len(observations),
        "understandings_exported": len(understandings),
        "perspectives_exported": len(perspectives),
        "utility_signals_exported": len(utility_signals),
        "events_exported": len(events),
    }


async def import_workspace(
    path: str | Path,
    *,
    name: str | None = None,
    progress: Callable[[str, int], None] | None = None,
) -> dict:
    """Import a workspace snapshot created by export_workspace."""
    import_path = _normalize_file_path(path)
    if not import_path.is_file():
        raise ValueError(f"Workspace export file not found: {import_path}")

    payload = json.loads(import_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 3:
        raise ValueError("Unsupported workspace export schema_version")
    source_workspace = payload.get("workspace") or {}
    target_name = _normalize_workspace_name(name or source_workspace.get("name", ""))
    observations = payload.get("observations", [])
    understandings = payload.get("understandings", [])

    subject_id_map: dict[int, int] = {}
    observation_id_map: dict[int, int] = {}
    understanding_id_map: dict[int, int] = {}

    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            workspace_row = await conn.fetchrow(
                """
                INSERT INTO workspaces (
                    name,
                    current_generation,
                    last_consolidated_at,
                    created_at
                )
                VALUES ($1, $2, $3, COALESCE($4::timestamptz, NOW()))
                ON CONFLICT (name) DO UPDATE
                SET
                    current_generation = EXCLUDED.current_generation,
                    last_consolidated_at = EXCLUDED.last_consolidated_at
                RETURNING id
                """,
                target_name,
                source_workspace.get("current_generation", 0),
                _parse_timestamp(source_workspace.get("last_consolidated_at")),
                _parse_timestamp(source_workspace.get("created_at")),
            )
            workspace_id = workspace_row["id"]
            ids = await _collect_workspace_ids(conn, workspace_id)
            if any(
                ids[key]
                for key in [
                    "subject_ids",
                    "observation_ids",
                    "understanding_ids",
                    "perspective_ids",
                    "utility_signal_ids",
                    "event_ids",
                ]
            ):
                raise ValueError(
                    f"Workspace '{target_name}' is not empty. Reset it before importing."
                )
            _emit_import_progress(progress, "workspace")

            for subject in payload.get("subjects", []):
                row = await conn.fetchrow(
                    """
                    INSERT INTO subjects (
                        workspace_id,
                        name,
                        summary,
                        tags,
                        created_at
                    )
                    VALUES ($1, $2, $3, $4, COALESCE($5::timestamptz, NOW()))
                    RETURNING id
                    """,
                    workspace_id,
                    subject["name"],
                    subject.get("summary"),
                    subject.get("tags") or [],
                    _parse_timestamp(subject.get("created_at")),
                )
                subject_id_map[subject["id"]] = row["id"]
                _emit_import_progress(progress, "subjects")

            for perspective in payload.get("perspectives", []):
                await conn.execute(
                    """
                    INSERT INTO perspectives (
                        workspace_id,
                        name,
                        instruction,
                        is_default
                    )
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (workspace_id, name) DO UPDATE
                    SET
                        instruction = EXCLUDED.instruction,
                        is_default = EXCLUDED.is_default
                    """,
                    workspace_id,
                    perspective["name"],
                    perspective["instruction"],
                    perspective["is_default"],
                )
                _emit_import_progress(progress, "perspectives")

            for observation in observations:
                row = await conn.fetchrow(
                    """
                    INSERT INTO observations (
                        workspace_id,
                        content,
                        content_hash,
                        kind,
                        confidence,
                        generation,
                        observed_at,
                        session_id,
                        model_tier,
                        created_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6,
                        COALESCE($7::timestamptz, NOW()),
                        $8, $9,
                        COALESCE($10::timestamptz, NOW())
                    )
                    RETURNING id
                    """,
                    workspace_id,
                    observation["content"],
                    observation["content_hash"],
                    observation.get("kind"),
                    observation.get("confidence"),
                    observation["generation"],
                    _parse_timestamp(observation.get("observed_at")),
                    observation.get("session_id"),
                    observation.get("model_tier"),
                    _parse_timestamp(observation.get("created_at")),
                )
                observation_id_map[observation["id"]] = row["id"]
                _emit_import_progress(progress, "observations")

            for understanding in understandings:
                row = await conn.fetchrow(
                    """
                    INSERT INTO understandings (
                        workspace_id,
                        content,
                        summary,
                        kind,
                        generation,
                        session_id,
                        model_tier,
                        reason,
                        created_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8,
                        COALESCE($9::timestamptz, NOW())
                    )
                    RETURNING id
                    """,
                    workspace_id,
                    understanding["content"],
                    understanding.get("summary"),
                    understanding["kind"],
                    understanding["generation"],
                    understanding.get("session_id"),
                    understanding.get("model_tier"),
                    understanding.get("reason"),
                    _parse_timestamp(understanding.get("created_at")),
                )
                understanding_id_map[understanding["id"]] = row["id"]
                _emit_import_progress(progress, "understandings")

            if payload.get("observation_subjects"):
                await conn.executemany(
                    """
                    INSERT INTO observation_subjects (observation_id, subject_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (
                            observation_id_map[item["observation_id"]],
                            subject_id_map[item["subject_id"]],
                        )
                        for item in payload["observation_subjects"]
                    ],
                )
                _emit_import_progress(
                    progress,
                    "observation_subjects",
                    len(payload["observation_subjects"]),
                )

            if payload.get("understanding_subjects"):
                await conn.executemany(
                    """
                    INSERT INTO understanding_subjects (understanding_id, subject_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (
                            understanding_id_map[item["understanding_id"]],
                            subject_id_map[item["subject_id"]],
                        )
                        for item in payload["understanding_subjects"]
                    ],
                )
                _emit_import_progress(
                    progress,
                    "understanding_subjects",
                    len(payload["understanding_subjects"]),
                )

            if payload.get("understanding_sources"):
                await conn.executemany(
                    """
                    INSERT INTO understanding_sources (understanding_id, observation_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (
                            understanding_id_map[item["understanding_id"]],
                            observation_id_map[item["observation_id"]],
                        )
                        for item in payload["understanding_sources"]
                    ],
                )
                _emit_import_progress(
                    progress,
                    "understanding_sources",
                    len(payload["understanding_sources"]),
                )

            for understanding in understandings:
                old_superseded_by = understanding.get("superseded_by")
                if old_superseded_by is not None:
                    await conn.execute(
                        """
                        UPDATE understandings
                        SET superseded_by = $2
                        WHERE id = $1
                        """,
                        understanding_id_map[understanding["id"]],
                        understanding_id_map[old_superseded_by],
                    )
                _emit_import_progress(progress, "supersession")

            for subject in payload.get("subjects", []):
                single_id = subject.get("single_subject_understanding_id")
                structural_id = subject.get("structural_understanding_id")
                await conn.execute(
                    """
                    UPDATE subjects
                    SET
                        single_subject_understanding_id = $2,
                        structural_understanding_id = $3
                    WHERE id = $1
                    """,
                    subject_id_map[subject["id"]],
                    (
                        understanding_id_map[single_id]
                        if single_id is not None
                        else None
                    ),
                    (
                        understanding_id_map[structural_id]
                        if structural_id is not None
                        else None
                    ),
                )
                _emit_import_progress(progress, "subject_pointers")

            await conn.execute(
                """
                UPDATE workspaces
                SET
                    soul_understanding_id = $2,
                    protocol_understanding_id = $3,
                    orientation_understanding_id = $4
                WHERE id = $1
                """,
                workspace_id,
                (
                    understanding_id_map[source_workspace["soul_understanding_id"]]
                    if source_workspace.get("soul_understanding_id") is not None
                    else None
                ),
                (
                    understanding_id_map[source_workspace["protocol_understanding_id"]]
                    if source_workspace.get("protocol_understanding_id") is not None
                    else None
                ),
                (
                    understanding_id_map[source_workspace["orientation_understanding_id"]]
                    if source_workspace.get("orientation_understanding_id") is not None
                    else None
                ),
            )
            _emit_import_progress(progress, "workspace_documents")

            if payload.get("utility_signals"):
                await conn.executemany(
                    """
                    INSERT INTO utility_signals (
                        target_id,
                        signal_type,
                        reason,
                        session_id,
                        created_at
                    )
                    VALUES (
                        $1, $2, $3, $4,
                        COALESCE($5::timestamptz, NOW())
                    )
                    """,
                    [
                        (
                            observation_id_map.get(item["target_id"])
                            or understanding_id_map[item["target_id"]],
                            item["signal_type"],
                            item.get("reason"),
                            item.get("session_id"),
                            _parse_timestamp(item.get("created_at")),
                        )
                        for item in payload["utility_signals"]
                    ],
                )
                _emit_import_progress(
                    progress,
                    "utility_signals",
                    len(payload["utility_signals"]),
                )

            for event in payload.get("events", []):
                await conn.execute(
                    """
                    INSERT INTO events (
                        workspace_id,
                        session_id,
                        timestamp,
                        operation,
                        detail
                    )
                    VALUES (
                        $1, $2,
                        COALESCE($3::timestamptz, NOW()),
                        $4,
                        $5::jsonb
                    )
                    """,
                    workspace_id,
                    event.get("session_id"),
                    _parse_timestamp(event.get("timestamp")),
                    event["operation"],
                    json.dumps(event.get("detail")) if event.get("detail") is not None else None,
                )
                _emit_import_progress(progress, "events")

            imported_targets = [
                (observation_id_map[row["id"]], row["content"])
                for row in observations
            ]
            imported_understandings = [
                (understanding_id_map[row["id"]], row["content"])
                for row in understandings
            ]
            batch_size = 32
            for start_index in range(0, len(imported_targets), batch_size):
                target_batch = imported_targets[start_index : start_index + batch_size]
                await embed_targets(
                    conn,
                    workspace_id=workspace_id,
                    targets=target_batch,
                )
                _emit_import_progress(
                    progress,
                    "embedding_observations",
                    len(target_batch),
                )

            for start_index in range(0, len(imported_understandings), batch_size):
                target_batch = imported_understandings[
                    start_index : start_index + batch_size
                ]
                await embed_targets(
                    conn,
                    workspace_id=workspace_id,
                    targets=target_batch,
                )
                _emit_import_progress(
                    progress,
                    "embedding_understandings",
                    len(target_batch),
                )

    return {
        "name": target_name,
        "source_name": source_workspace.get("name"),
        "subjects_imported": len(subject_id_map),
        "observations_imported": len(observation_id_map),
        "understandings_imported": len(understanding_id_map),
        "perspectives_imported": len(payload.get("perspectives", [])),
        "utility_signals_imported": len(payload.get("utility_signals", [])),
        "events_imported": len(payload.get("events", [])),
    }


async def count_reembed_targets() -> int:
    """Return how many objects will be embedded by reembed_database."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT
                (SELECT COUNT(*) FROM observations)
                +
                (
                    SELECT COUNT(*)
                    FROM understandings
                    WHERE superseded_by IS NULL
                )
            """
        )


async def reembed_database(
    *,
    progress: Callable[[str, int], None] | None = None,
) -> dict:
    """Regenerate all observation and active-understanding embeddings."""
    pool = await get_pool()
    observations_embedded = 0
    understandings_embedded = 0

    async with pool.acquire() as conn:
        workspace_rows = await conn.fetch(
            """
            SELECT id, name
            FROM workspaces
            ORDER BY name
            """
        )

        await conn.execute("DELETE FROM embeddings")

        batch_size = 32
        for workspace_row in workspace_rows:
            observation_rows = await conn.fetch(
                """
                SELECT id, content
                FROM observations
                WHERE workspace_id = $1
                ORDER BY id
                """,
                workspace_row["id"],
            )
            understanding_rows = await conn.fetch(
                """
                SELECT id, content
                FROM understandings
                WHERE workspace_id = $1
                  AND superseded_by IS NULL
                ORDER BY id
                """,
                workspace_row["id"],
            )
            workspace_targets = [
                (row["id"], row["content"])
                for row in observation_rows
            ] + [
                (row["id"], row["content"])
                for row in understanding_rows
            ]
            for start_index in range(0, len(workspace_targets), batch_size):
                target_batch = workspace_targets[
                    start_index : start_index + batch_size
                ]
                await embed_targets(
                    conn,
                    workspace_id=workspace_row["id"],
                    targets=target_batch,
                )
                _emit_import_progress(
                    progress,
                    workspace_row["name"],
                    len(target_batch),
                )

            observations_embedded += len(observation_rows)
            understandings_embedded += len(understanding_rows)

    return {
        "workspaces_reembedded": len(workspace_rows),
        "observations_reembedded": observations_embedded,
        "understandings_reembedded": understandings_embedded,
    }


async def list_subjects(workspace: str, limit: int = 100) -> list[dict]:
    """Return subjects in a workspace ordered by name."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace_id = await _get_workspace_id(conn, workspace)
        rows = await conn.fetch(
            """
            SELECT
                id,
                name,
                summary,
                tags,
                single_subject_understanding_id,
                structural_understanding_id,
                created_at
            FROM subjects
            WHERE workspace_id = $1
            ORDER BY name
            LIMIT $2
            """,
            workspace_id,
            limit,
        )
    return [serialize(row) for row in rows]


async def create_subject(
    workspace: str,
    name: str,
    *,
    summary: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """Create one subject in a workspace."""
    result = await tools.create_subjects(
        [{"name": _normalize_subject_name(name), "summary": summary, "tags": tags or []}],
        workspace=_normalize_workspace_name(workspace),
    )
    return result[0]


async def show_subject(workspace: str, name: str) -> dict:
    """Return one subject and its linked observations/understandings."""
    workspace = _normalize_workspace_name(workspace)
    name = _normalize_subject_name(name)
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace_id = await _get_workspace_id(conn, workspace)
        row = await conn.fetchrow(
            """
            SELECT
                id,
                name,
                summary,
                tags,
                single_subject_understanding_id,
                structural_understanding_id,
                created_at
            FROM subjects
            WHERE workspace_id = $1
              AND name = $2
            """,
            workspace_id,
            name,
        )
        if row is None:
            raise ValueError(f"Subject '{name}' not found in workspace '{workspace}'")
        observation_ids = await conn.fetch(
            """
            SELECT o.id
            FROM observations o
            JOIN observation_subjects os ON os.observation_id = o.id
            WHERE os.subject_id = $1
            ORDER BY o.created_at DESC
            """,
            row["id"],
        )
        understanding_ids = await conn.fetch(
            """
            SELECT u.id
            FROM understandings u
            JOIN understanding_subjects us ON us.understanding_id = u.id
            WHERE us.subject_id = $1
            ORDER BY u.created_at DESC
            """,
            row["id"],
        )
    result = serialize(row)
    result["observation_ids"] = [item["id"] for item in observation_ids]
    result["understanding_ids"] = [item["id"] for item in understanding_ids]
    return result


async def delete_subject(workspace: str, name: str) -> dict:
    """Delete one subject by name and remove its id_registry row."""
    workspace = _normalize_workspace_name(workspace)
    name = _normalize_subject_name(name)
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace_id = await _get_workspace_id(conn, workspace)
        row = await conn.fetchrow(
            """
            DELETE FROM subjects
            WHERE workspace_id = $1
              AND name = $2
            RETURNING id, name
            """,
            workspace_id,
            name,
        )
        if row is not None:
            await _cleanup_target_metadata(conn, row["id"])
    return {"name": name, "deleted": row is not None}


async def list_observations(
    workspace: str,
    *,
    subject_name: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return observations in a workspace, optionally filtered by subject."""
    workspace = _normalize_workspace_name(workspace)
    subject_name = _normalize_subject_name(subject_name) if subject_name is not None else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace_id = await _get_workspace_id(conn, workspace)
        rows = await conn.fetch(
            """
            SELECT DISTINCT
                o.id,
                o.content,
                o.kind,
                o.confidence,
                o.generation,
                o.observed_at,
                o.session_id,
                o.model_tier,
                o.created_at
            FROM observations o
            LEFT JOIN observation_subjects os ON os.observation_id = o.id
            LEFT JOIN subjects s ON s.id = os.subject_id
            WHERE o.workspace_id = $1
              AND ($2::text IS NULL OR s.name = $2)
            ORDER BY o.created_at DESC
            LIMIT $3
            """,
            workspace_id,
            subject_name,
            limit,
        )
        subject_names = await _subject_names_for_observations(
            conn,
            [row["id"] for row in rows],
        )
    result = [serialize(row) for row in rows]
    for item in result:
        item["subject_names"] = subject_names.get(item["id"], [])
    return result


async def create_observation(
    workspace: str,
    subject_names: list[str],
    content: str,
    *,
    kind: str | None = None,
    confidence: float | None = None,
    related_to: list[int] | None = None,
    session_id: str = "admin-cli",
) -> dict:
    """Create one observation through the regular v3 write path."""
    result = await tools.add_observations(
        [
            {
                "subject_names": subject_names,
                "content": content,
                "kind": kind,
                "confidence": confidence,
                "related_to": related_to,
            }
        ],
        workspace=_normalize_workspace_name(workspace),
        session_id=session_id,
    )
    return result[0]


async def show_observation(workspace: str, observation_id: int) -> dict:
    """Return one observation with subject and related-understanding links."""
    workspace = _normalize_workspace_name(workspace)
    observation_id = _normalize_object_id(observation_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace_id = await _get_workspace_id(conn, workspace)
        row = await conn.fetchrow(
            """
            SELECT
                id,
                content,
                content_hash,
                kind,
                confidence,
                generation,
                observed_at,
                session_id,
                model_tier,
                created_at
            FROM observations
            WHERE workspace_id = $1
              AND id = $2
            """,
            workspace_id,
            observation_id,
        )
        if row is None:
            raise ValueError(
                f"Observation {observation_id} not found in workspace '{workspace}'"
            )
        subject_names = await _subject_names_for_observations(conn, [observation_id])
        source_rows = await conn.fetch(
            """
            SELECT understanding_id
            FROM understanding_sources
            WHERE observation_id = $1
            ORDER BY understanding_id
            """,
            observation_id,
        )
    result = serialize(row)
    result["subject_names"] = subject_names.get(observation_id, [])
    result["related_understanding_ids"] = [item["understanding_id"] for item in source_rows]
    return result


async def delete_observation(workspace: str, observation_id: int) -> dict:
    """Delete one observation by ID and clean up its metadata rows."""
    workspace = _normalize_workspace_name(workspace)
    observation_id = _normalize_object_id(observation_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace_id = await _get_workspace_id(conn, workspace)
        row = await conn.fetchrow(
            """
            DELETE FROM observations
            WHERE workspace_id = $1
              AND id = $2
            RETURNING id
            """,
            workspace_id,
            observation_id,
        )
        if row is not None:
            await _cleanup_target_metadata(conn, observation_id)
    return {"id": observation_id, "deleted": row is not None}


async def list_understandings(
    workspace: str,
    *,
    subject_name: str | None = None,
    kind: str | None = None,
    include_superseded: bool = False,
    limit: int = 100,
) -> list[dict]:
    """Return understandings in a workspace, optionally filtered by subject and kind."""
    workspace = _normalize_workspace_name(workspace)
    subject_name = _normalize_subject_name(subject_name) if subject_name is not None else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace_id = await _get_workspace_id(conn, workspace)
        rows = await conn.fetch(
            """
            SELECT DISTINCT
                u.id,
                u.kind,
                u.summary,
                u.generation,
                u.session_id,
                u.model_tier,
                u.reason,
                u.created_at,
                u.superseded_by
            FROM understandings u
            LEFT JOIN understanding_subjects us ON us.understanding_id = u.id
            LEFT JOIN subjects s ON s.id = us.subject_id
            WHERE u.workspace_id = $1
              AND ($2::text IS NULL OR s.name = $2)
              AND ($3::text IS NULL OR u.kind = $3)
              AND ($4::bool OR u.superseded_by IS NULL)
            ORDER BY u.created_at DESC
            LIMIT $5
            """,
            workspace_id,
            subject_name,
            kind,
            include_superseded,
            limit,
        )
        subject_names = await _subject_names_for_understandings(
            conn,
            [row["id"] for row in rows],
        )
    result = [serialize(row) for row in rows]
    for item in result:
        item["subject_names"] = subject_names.get(item["id"], [])
    return result


async def create_understanding(
    workspace: str,
    subject_names: list[str],
    content: str,
    summary: str,
    *,
    kind: str | None = None,
    source_observation_ids: list[int] | None = None,
    reason: str | None = None,
    session_id: str = "admin-cli",
) -> dict:
    """Create one understanding through the regular v3 write path."""
    return await tools.create_understanding(
        subject_names,
        content,
        summary,
        kind=kind,
        source_observation_ids=source_observation_ids,
        workspace=_normalize_workspace_name(workspace),
        session_id=session_id,
        reason=reason,
    )


async def show_understanding(workspace: str, understanding_id: int) -> dict:
    """Return one understanding with subject and source-observation links."""
    workspace = _normalize_workspace_name(workspace)
    understanding_id = _normalize_object_id(understanding_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace_id = await _get_workspace_id(conn, workspace)
        row = await conn.fetchrow(
            """
            SELECT
                id,
                content,
                summary,
                kind,
                generation,
                session_id,
                model_tier,
                reason,
                created_at,
                superseded_by
            FROM understandings
            WHERE workspace_id = $1
              AND id = $2
            """,
            workspace_id,
            understanding_id,
        )
        if row is None:
            raise ValueError(
                f"Understanding {understanding_id} not found in workspace '{workspace}'"
            )
        subject_names = await _subject_names_for_understandings(conn, [understanding_id])
        source_rows = await conn.fetch(
            """
            SELECT observation_id
            FROM understanding_sources
            WHERE understanding_id = $1
            ORDER BY observation_id
            """,
            understanding_id,
        )
    result = serialize(row)
    result["subject_names"] = subject_names.get(understanding_id, [])
    result["source_observation_ids"] = [item["observation_id"] for item in source_rows]
    return result


async def delete_understanding(workspace: str, understanding_id: int) -> dict:
    """Delete one understanding by ID and clean up pointers/metadata rows."""
    workspace = _normalize_workspace_name(workspace)
    understanding_id = _normalize_object_id(understanding_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace_id = await _get_workspace_id(conn, workspace)
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE workspaces
                SET
                    soul_understanding_id = NULLIF(soul_understanding_id, $2),
                    protocol_understanding_id = NULLIF(protocol_understanding_id, $2),
                    orientation_understanding_id = NULLIF(orientation_understanding_id, $2)
                WHERE id = $1
                """,
                workspace_id,
                understanding_id,
            )
            await conn.execute(
                """
                UPDATE subjects
                SET
                    single_subject_understanding_id = NULLIF(single_subject_understanding_id, $2),
                    structural_understanding_id = NULLIF(structural_understanding_id, $2)
                WHERE workspace_id = $1
                """,
                workspace_id,
                understanding_id,
            )
            await conn.execute(
                """
                UPDATE understandings
                SET superseded_by = NULL
                WHERE workspace_id = $1
                  AND superseded_by = $2
                """,
                workspace_id,
                understanding_id,
            )
            row = await conn.fetchrow(
                """
                DELETE FROM understandings
                WHERE workspace_id = $1
                  AND id = $2
                RETURNING id
                """,
                workspace_id,
                understanding_id,
            )
            if row is not None:
                await _cleanup_target_metadata(conn, understanding_id)
    return {"id": understanding_id, "deleted": row is not None}


async def list_utility_signals(workspace: str, limit: int = 100) -> list[dict]:
    """Return utility signals for observation/understanding IDs in a workspace."""
    workspace = _normalize_workspace_name(workspace)
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace_id = await _get_workspace_id(conn, workspace)
        rows = await conn.fetch(
            """
            SELECT
                us.id,
                us.target_id,
                ir.kind AS target_kind,
                us.signal_type,
                us.reason,
                us.session_id,
                us.created_at
            FROM utility_signals us
            JOIN id_registry ir ON ir.id = us.target_id
            WHERE EXISTS (
                SELECT 1 FROM observations o WHERE o.id = us.target_id AND o.workspace_id = $1
            ) OR EXISTS (
                SELECT 1 FROM understandings u WHERE u.id = us.target_id AND u.workspace_id = $1
            )
            ORDER BY us.created_at DESC
            LIMIT $2
            """,
            workspace_id,
            limit,
        )
    return [serialize(row) for row in rows]


async def list_events(workspace: str, limit: int = 100) -> list[dict]:
    """Return recent event log rows for a workspace."""
    workspace = _normalize_workspace_name(workspace)
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace_id = await _get_workspace_id(conn, workspace)
        rows = await conn.fetch(
            """
            SELECT id, session_id, timestamp, operation, detail
            FROM events
            WHERE workspace_id = $1
            ORDER BY timestamp DESC
            LIMIT $2
            """,
            workspace_id,
            limit,
        )
    return [serialize(row) for row in rows]


async def list_perspectives(workspace: str, include_global: bool = True) -> list[dict]:
    """Return workspace perspectives, optionally including global defaults."""
    workspace = _normalize_workspace_name(workspace)
    pool = await get_pool()
    async with pool.acquire() as conn:
        workspace_id = await _get_workspace_id(conn, workspace)
        rows = await conn.fetch(
            """
            SELECT id, workspace_id, name, instruction, is_default
            FROM perspectives
            WHERE workspace_id = $1
               OR ($2::bool AND workspace_id IS NULL)
            ORDER BY workspace_id NULLS LAST, name
            """,
            workspace_id,
            include_global,
        )
    return [serialize(row) for row in rows]
