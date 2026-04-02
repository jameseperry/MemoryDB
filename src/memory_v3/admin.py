"""Administrative helpers for the v3 database."""

from __future__ import annotations

from memory_v3 import tools
from memory_v3.db import get_pool, serialize


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
