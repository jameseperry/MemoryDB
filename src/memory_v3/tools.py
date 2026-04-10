"""Core v3 subject/understanding tools."""

from __future__ import annotations

import json
import secrets
from typing import Literal

import asyncpg

from memory_v3.config import settings
from memory_v3.db import (
    ensure_request_writable,
    get_pool,
    get_workspace_generation,
    hash_content,
    record_event,
    resolve_effective_workspace_name,
    resolve_optional_session_id,
    resolve_session_id,
    resolve_workspace_id,
)
from memory_v3.embeddings import embed_targets, search_embeddings


def _normalize_subject_names(subject_names: list[str]) -> list[str]:
    """Normalize, validate, and deduplicate subject names while preserving order."""
    normalized: list[str] = []
    seen: set[str] = set()
    for name in subject_names:
        clean = name.strip()
        if not clean:
            continue
        if clean not in seen:
            seen.add(clean)
            normalized.append(clean)
    if not normalized:
        raise ValueError("At least one subject name is required")
    return normalized


async def _fetch_subject_rows(
    conn: asyncpg.Connection,
    workspace_id: int,
    subject_names: list[str],
) -> list[asyncpg.Record]:
    return await conn.fetch(
        """
        SELECT id, name, summary, tags, single_subject_understanding_id, structural_understanding_id, created_at
        FROM subjects
        WHERE workspace_id = $1
          AND name = ANY($2)
        ORDER BY name
        """,
        workspace_id,
        subject_names,
    )


async def _ensure_subjects(
    conn: asyncpg.Connection,
    workspace_id: int,
    subject_names: list[str],
) -> tuple[list[dict], list[str]]:
    """Ensure subjects exist, creating any missing names."""
    normalized = _normalize_subject_names(subject_names)
    existing_rows = await _fetch_subject_rows(conn, workspace_id, normalized)
    existing_by_name = {row["name"]: dict(row) for row in existing_rows}

    created: list[str] = []
    for name in normalized:
        if name in existing_by_name:
            continue
        row = await conn.fetchrow(
            """
            INSERT INTO subjects (workspace_id, name)
            VALUES ($1, $2)
            RETURNING id, name, summary, tags, single_subject_understanding_id, structural_understanding_id, created_at
            """,
            workspace_id,
            name,
        )
        existing_by_name[name] = dict(row)
        created.append(name)

    ordered = [existing_by_name[name] for name in normalized]
    return ordered, created


async def _require_subjects(
    conn: asyncpg.Connection,
    workspace_id: int,
    subject_names: list[str],
) -> list[dict]:
    rows = await _fetch_subject_rows(conn, workspace_id, _normalize_subject_names(subject_names))
    rows_by_name = {row["name"]: dict(row) for row in rows}
    missing = [name for name in _normalize_subject_names(subject_names) if name not in rows_by_name]
    if missing:
        raise ValueError(f"Subjects not found: {missing}")
    return [rows_by_name[name] for name in _normalize_subject_names(subject_names)]


async def _get_subject_names_for_targets(
    conn: asyncpg.Connection,
    observation_ids: list[int],
    understanding_ids: list[int],
) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    if observation_ids:
        rows = await conn.fetch(
            """
            SELECT os.observation_id AS target_id, s.name
            FROM observation_subjects os
            JOIN subjects s ON s.id = os.subject_id
            WHERE os.observation_id = ANY($1)
            ORDER BY s.name
            """,
            observation_ids,
        )
        for row in rows:
            result.setdefault(row["target_id"], []).append(row["name"])
    if understanding_ids:
        rows = await conn.fetch(
            """
            SELECT us.understanding_id AS target_id, s.name
            FROM understanding_subjects us
            JOIN subjects s ON s.id = us.subject_id
            WHERE us.understanding_id = ANY($1)
            ORDER BY s.name
            """,
            understanding_ids,
        )
        for row in rows:
            result.setdefault(row["target_id"], []).append(row["name"])
    return result


async def _get_observation_links(
    conn: asyncpg.Connection,
    observation_ids: list[int],
) -> dict[int, dict[str, list[int]]]:
    if not observation_ids:
        return {}

    result = {
        observation_id: {"points_to": [], "pointed_to_by": []}
        for observation_id in observation_ids
    }
    outgoing_rows = await conn.fetch(
        """
        SELECT source_observation_id, target_observation_id
        FROM observation_links
        WHERE source_observation_id = ANY($1)
        ORDER BY source_observation_id, target_observation_id
        """,
        observation_ids,
    )
    for row in outgoing_rows:
        result.setdefault(
            row["source_observation_id"],
            {"points_to": [], "pointed_to_by": []},
        )["points_to"].append(row["target_observation_id"])

    incoming_rows = await conn.fetch(
        """
        SELECT source_observation_id, target_observation_id
        FROM observation_links
        WHERE target_observation_id = ANY($1)
        ORDER BY target_observation_id, source_observation_id
        """,
        observation_ids,
    )
    for row in incoming_rows:
        result.setdefault(
            row["target_observation_id"],
            {"points_to": [], "pointed_to_by": []},
        )["pointed_to_by"].append(row["source_observation_id"])

    return result


def _mutation_rejection_reason(
    *,
    row: asyncpg.Record | dict | None,
    effective_session_id: str,
    current_generation: int,
) -> str | None:
    if row is None:
        return "not found"
    if row["session_id"] != effective_session_id:
        return "session mismatch"
    if current_generation > row["generation"]:
        return "already consolidated"
    return None


async def _clear_understanding_pointers(
    conn: asyncpg.Connection,
    *,
    understanding_id: int,
) -> None:
    await conn.execute(
        """
        UPDATE subjects
        SET
            single_subject_understanding_id = CASE
                WHEN single_subject_understanding_id = $1 THEN NULL
                ELSE single_subject_understanding_id
            END,
            structural_understanding_id = CASE
                WHEN structural_understanding_id = $1 THEN NULL
                ELSE structural_understanding_id
            END
        WHERE single_subject_understanding_id = $1
           OR structural_understanding_id = $1
        """,
        understanding_id,
    )
    await conn.execute(
        """
        UPDATE workspaces
        SET
            soul_understanding_id = CASE
                WHEN soul_understanding_id = $1 THEN NULL
                ELSE soul_understanding_id
            END,
            protocol_understanding_id = CASE
                WHEN protocol_understanding_id = $1 THEN NULL
                ELSE protocol_understanding_id
            END,
            orientation_understanding_id = CASE
                WHEN orientation_understanding_id = $1 THEN NULL
                ELSE orientation_understanding_id
            END,
            consolidation_understanding_id = CASE
                WHEN consolidation_understanding_id = $1 THEN NULL
                ELSE consolidation_understanding_id
            END
        WHERE soul_understanding_id = $1
           OR protocol_understanding_id = $1
           OR orientation_understanding_id = $1
           OR consolidation_understanding_id = $1
        """,
        understanding_id,
    )


def _split_target_ids(items: list[dict]) -> tuple[list[int], list[int]]:
    observation_ids = [item["id"] for item in items if item["kind"] == "observation"]
    understanding_ids = [item["id"] for item in items if item["kind"] == "understanding"]
    return observation_ids, understanding_ids


async def _mark_targets_surfaced(
    conn: asyncpg.Connection,
    *,
    workspace_id: int,
    session_id: str,
    target_ids: list[int],
) -> None:
    if not target_ids:
        return
    session_row_id = await resolve_session_id(
        conn,
        workspace_id=workspace_id,
        session_token=session_id,
    )
    await conn.executemany(
        """
        INSERT INTO surfaced_in_session (session_id, id)
        VALUES ($1, $2)
        ON CONFLICT (session_id, id)
            DO UPDATE SET surfaced_at = NOW()
        """,
        [(session_row_id, target_id) for target_id in target_ids],
    )


async def _advance_heartbeat_token(
    conn: asyncpg.Connection,
    *,
    workspace_id: int,
    session_id: str,
) -> int:
    token = secrets.randbelow(2_147_483_647) + 1
    await conn.execute(
        """
        INSERT INTO sessions (workspace_id, session_token, seen_set_token, updated_at)
        VALUES ($1, $2, $3, NOW())
        ON CONFLICT (workspace_id, session_token)
            DO UPDATE SET seen_set_token = EXCLUDED.seen_set_token, updated_at = NOW()
        """,
        workspace_id,
        session_id,
        token,
    )
    return token


async def _reset_seen_state(
    conn: asyncpg.Connection,
    *,
    workspace_id: int,
    session_id: str,
) -> int:
    deleted_rows = await conn.fetch(
        """
        DELETE FROM surfaced_in_session
        WHERE session_id = (
            SELECT session_id
            FROM sessions
            WHERE workspace_id = $1
              AND session_token = $2
        )
        RETURNING id
        """,
        workspace_id,
        session_id,
    )
    await conn.execute(
        """
        UPDATE sessions
        SET seen_set_token = 0, updated_at = NOW()
        WHERE workspace_id = $1
          AND session_token = $2
        """,
        workspace_id,
        session_id,
    )
    return len(deleted_rows)


def _normalize_model_tier(model_tier: str | None) -> str | None:
    if model_tier is None:
        return None
    normalized = model_tier.strip()
    return normalized or None


async def _set_session_model_tier(
    conn: asyncpg.Connection,
    *,
    workspace_id: int,
    session_id: str,
    model_tier: str | None,
) -> str | None:
    row = await conn.fetchrow(
        """
        INSERT INTO sessions (workspace_id, session_token, seen_set_token, updated_at, model_tier)
        VALUES ($1, $2, 0, NOW(), $3)
        ON CONFLICT (workspace_id, session_token)
            DO UPDATE SET
                model_tier = EXCLUDED.model_tier,
                updated_at = NOW()
        RETURNING model_tier
        """,
        workspace_id,
        session_id,
        _normalize_model_tier(model_tier),
    )
    return row["model_tier"]


async def _get_session_model_tier(
    conn: asyncpg.Connection,
    workspace_id: int,
    session_id: str,
) -> str | None:
    return await conn.fetchval(
        """
        SELECT model_tier
        FROM sessions
        WHERE workspace_id = $1
          AND session_token = $2
        """,
        workspace_id,
        session_id,
    )


async def _fetch_active_understandings_by_id(
    conn: asyncpg.Connection,
    understanding_ids: list[int],
    *,
    allow_missing: bool = False,
    context: str = "Understanding pointer",
) -> dict[int, asyncpg.Record]:
    if not understanding_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT id, content, summary, kind, generation, created_at, superseded_by
        FROM understandings
        WHERE id = ANY($1)
        """,
        understanding_ids,
    )
    rows_by_id = {row["id"]: row for row in rows}
    for understanding_id in understanding_ids:
        row = rows_by_id.get(understanding_id)
        if row is None:
            if allow_missing:
                continue
            raise ValueError(f"{context} {understanding_id} not found")
        if row["superseded_by"] is not None:
            raise ValueError(
                f"{context} {understanding_id} is superseded by "
                f"{row['superseded_by']}"
            )
    return rows_by_id


async def _get_current_understanding_id(
    conn: asyncpg.Connection,
    workspace_id: int,
    understanding_id: int,
) -> int:
    rows = await conn.fetch(
        """
        WITH RECURSIVE successors AS (
            SELECT id, superseded_by
            FROM understandings
            WHERE workspace_id = $1
              AND id = $2

            UNION ALL

            SELECT u.id, u.superseded_by
            FROM understandings u
            JOIN successors s ON u.id = s.superseded_by
            WHERE u.workspace_id = $1
        )
        SELECT id, superseded_by
        FROM successors
        """,
        workspace_id,
        understanding_id,
    )
    if not rows:
        raise ValueError(f"Understanding {understanding_id} not found")
    for row in rows:
        if row["superseded_by"] is None:
            return row["id"]
    return understanding_id


async def _find_active_understanding_exact_subjects(
    conn: asyncpg.Connection,
    *,
    workspace_id: int,
    kind: str,
    subject_ids: list[int],
) -> list[int]:
    """Return active understanding IDs with exactly the given subject set."""
    sorted_ids = sorted(subject_ids)
    rows = await conn.fetch(
        """
        SELECT u.id
        FROM understandings u
        JOIN understanding_subjects us ON us.understanding_id = u.id
        WHERE u.workspace_id = $1
          AND u.kind = $2
          AND u.superseded_by IS NULL
        GROUP BY u.id
        HAVING ARRAY_AGG(us.subject_id ORDER BY us.subject_id) = $3::bigint[]
        """,
        workspace_id,
        kind,
        sorted_ids,
    )
    return [row["id"] for row in rows]


async def _supersede_understanding_ids(
    conn: asyncpg.Connection,
    *,
    old_ids: list[int],
    new_id: int,
) -> None:
    if not old_ids:
        return
    await conn.execute(
        """
        UPDATE understandings
        SET superseded_by = $2
        WHERE id = ANY($1)
          AND superseded_by IS NULL
        """,
        old_ids,
        new_id,
    )


async def _link_observation_to_understandings(
    conn: asyncpg.Connection,
    *,
    observation_id: int,
    understanding_ids: list[int],
) -> None:
    if not understanding_ids:
        return
    await conn.executemany(
        """
        INSERT INTO understanding_sources (understanding_id, observation_id)
        VALUES ($1, $2)
        ON CONFLICT DO NOTHING
        """,
        [(understanding_id, observation_id) for understanding_id in understanding_ids],
    )


async def _update_special_pointer(
    conn: asyncpg.Connection,
    *,
    workspace_id: int,
    subject_id: int | None,
    kind: str,
    understanding_id: int,
) -> None:
    if kind == "single_subject":
        if subject_id is None:
            raise ValueError("single_subject understandings require exactly one subject")
        await conn.execute(
            """
            UPDATE subjects
            SET single_subject_understanding_id = $2
            WHERE id = $1
            """,
            subject_id,
            understanding_id,
        )
    elif kind == "structural":
        if subject_id is None:
            raise ValueError("structural understandings require exactly one subject")
        await conn.execute(
            """
            UPDATE subjects
            SET structural_understanding_id = $2
            WHERE id = $1
            """,
            subject_id,
            understanding_id,
        )
    elif kind == "soul":
        await conn.execute(
            "UPDATE workspaces SET soul_understanding_id = $2 WHERE id = $1",
            workspace_id,
            understanding_id,
        )
    elif kind == "protocol":
        await conn.execute(
            "UPDATE workspaces SET protocol_understanding_id = $2 WHERE id = $1",
            workspace_id,
            understanding_id,
        )
    elif kind == "orientation":
        await conn.execute(
            "UPDATE workspaces SET orientation_understanding_id = $2 WHERE id = $1",
            workspace_id,
            understanding_id,
        )
    elif kind == "consolidation":
        await conn.execute(
            "UPDATE workspaces SET consolidation_understanding_id = $2 WHERE id = $1",
            workspace_id,
            understanding_id,
        )


async def _create_understanding_record(
    conn: asyncpg.Connection,
    *,
    workspace_id: int,
    subject_rows: list[dict],
    content: str,
    summary: str,
    kind: str,
    generation: int,
    session_id: str,
    source_observation_ids: list[int] | None = None,
    reason: str | None = None,
    model_tier: str | None = None,
) -> dict:
    session_row_id = await resolve_session_id(
        conn,
        workspace_id=workspace_id,
        session_token=session_id,
    )
    subject_ids = [row["id"] for row in subject_rows]
    previous_ids = await _find_active_understanding_exact_subjects(
        conn,
        workspace_id=workspace_id,
        kind=kind,
        subject_ids=subject_ids,
    )

    row = await conn.fetchrow(
        """
        INSERT INTO understandings (
            workspace_id, content, summary, kind, generation,
            session_id, model_tier, reason
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id, content, summary, kind, generation, created_at
        """,
        workspace_id,
        content,
        summary,
        kind,
        generation,
        session_row_id,
        model_tier,
        reason,
    )
    understanding = dict(row)

    await conn.executemany(
        """
        INSERT INTO understanding_subjects (understanding_id, subject_id)
        VALUES ($1, $2)
        ON CONFLICT DO NOTHING
        """,
        [(understanding["id"], subject_id) for subject_id in subject_ids],
    )

    if source_observation_ids:
        await conn.executemany(
            """
            INSERT INTO understanding_sources (understanding_id, observation_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            [
                (understanding["id"], observation_id)
                for observation_id in source_observation_ids
            ],
        )

    await _supersede_understanding_ids(conn, old_ids=previous_ids, new_id=understanding["id"])

    if len(subject_ids) == 1 and kind in {"single_subject", "structural"}:
        await _update_special_pointer(
            conn,
            workspace_id=workspace_id,
            subject_id=subject_ids[0],
            kind=kind,
            understanding_id=understanding["id"],
        )
    elif kind in {"soul", "protocol", "orientation", "consolidation"}:
        await _update_special_pointer(
            conn,
            workspace_id=workspace_id,
            subject_id=subject_ids[0] if len(subject_ids) == 1 else None,
            kind=kind,
            understanding_id=understanding["id"],
        )

    await embed_targets(
        conn,
        workspace_id=workspace_id,
        targets=[(understanding["id"], content)],
    )
    return understanding


async def create_subjects(
    subjects: list[dict],
    workspace: str | None = None,
    readonly: bool | None = None,
) -> list[dict]:
    """Create named semantic regions."""
    ensure_request_writable(readonly)
    pool = await get_pool()
    session_id = resolve_optional_session_id()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)

        requested_names = _normalize_subject_names([subject["name"] for subject in subjects])
        existing_rows = await _fetch_subject_rows(conn, workspace_id, requested_names)
        existing_names = {row["name"] for row in existing_rows}
        if existing_names:
            raise ValueError(f"Subjects already exist: {sorted(existing_names)}")

        results = []
        for subject in subjects:
            name = subject["name"].strip()
            summary = subject.get("summary")
            tags = subject.get("tags", [])
            row = await conn.fetchrow(
                """
                INSERT INTO subjects (workspace_id, name, summary, tags)
                VALUES ($1, $2, $3, $4)
                RETURNING id, name, created_at
                """,
                workspace_id,
                name,
                summary,
                tags,
            )
            results.append(
                {
                    "name": row["name"],
                    "created_at": row["created_at"].isoformat(),
                }
            )

        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=session_id,
            operation="create_subjects",
            detail={"names": requested_names},
        )

    return results


async def get_subjects(
    names: list[str],
    workspace: str | None = None,
) -> list[dict]:
    """Return full subject content for named subjects."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        subject_rows = await conn.fetch(
            """
            SELECT
                s.id,
                s.name,
                s.summary,
                s.tags,
                s.single_subject_understanding_id,
                s.structural_understanding_id,
                (
                    SELECT COUNT(*)
                    FROM observation_subjects os
                    WHERE os.subject_id = s.id
                ) AS observation_count,
                (
                    SELECT MAX(o.created_at)
                    FROM observations o
                    JOIN observation_subjects os ON os.observation_id = o.id
                    WHERE os.subject_id = s.id
                ) AS last_observation_at
            FROM subjects s
            WHERE s.workspace_id = $1
              AND s.name = ANY($2)
            ORDER BY s.name
            """,
            workspace_id,
            names,
        )

        understanding_ids = [
            row["single_subject_understanding_id"]
            for row in subject_rows
            if row["single_subject_understanding_id"] is not None
        ] + [
            row["structural_understanding_id"]
            for row in subject_rows
            if row["structural_understanding_id"] is not None
        ]

        understanding_rows: dict[int, asyncpg.Record] = {}
        if understanding_ids:
            understanding_rows = await _fetch_active_understandings_by_id(
                conn,
                understanding_ids,
                allow_missing=True,
                context="Subject understanding pointer",
            )

    results = []
    for row in subject_rows:
        single_row = understanding_rows.get(row["single_subject_understanding_id"])
        structural_row = understanding_rows.get(row["structural_understanding_id"])
        results.append(
            {
                "name": row["name"],
                "summary": row["summary"],
                "tags": list(row["tags"]),
                "single_subject_understanding": (
                    {
                        "id": single_row["id"],
                        "summary": single_row["summary"],
                        "generation": single_row["generation"],
                    }
                    if single_row
                    else None
                ),
                "structural_understanding": (
                    {
                        "id": structural_row["id"],
                        "summary": structural_row["summary"],
                    }
                    if structural_row
                    else None
                ),
                "observation_count": row["observation_count"],
                "last_observation_at": (
                    row["last_observation_at"].isoformat()
                    if row["last_observation_at"] is not None
                    else None
                ),
            }
        )

    return results


async def set_subject_summary(
    name: str,
    summary: str,
    workspace: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Update a subject summary."""
    ensure_request_writable(readonly)
    pool = await get_pool()
    session_id = resolve_optional_session_id()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        row = await conn.fetchrow(
            """
            UPDATE subjects
            SET summary = $3
            WHERE workspace_id = $1
              AND name = $2
            RETURNING id, name, summary
            """,
            workspace_id,
            name,
            summary,
        )
        if row is None:
            raise ValueError(f"Subject '{name}' not found")

        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=session_id,
            operation="set_subject_summary",
            detail={"subject_name": name},
        )

    return {"name": row["name"], "summary": row["summary"]}


async def set_subject_tags(
    name: str,
    tags: list[str],
    workspace: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Replace subject tags."""
    ensure_request_writable(readonly)
    pool = await get_pool()
    session_id = resolve_optional_session_id()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        row = await conn.fetchrow(
            """
            UPDATE subjects
            SET tags = $3
            WHERE workspace_id = $1
              AND name = $2
            RETURNING name, tags
            """,
            workspace_id,
            name,
            tags,
        )
        if row is None:
            raise ValueError(f"Subject '{name}' not found")

        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=session_id,
            operation="set_subject_tags",
            detail={"subject_name": name},
        )

    return {"name": row["name"], "tags": list(row["tags"])}


async def set_structural_understanding(
    subject_name: str,
    content: str,
    workspace: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Write or replace a subject's structural understanding."""
    ensure_request_writable(readonly)
    pool = await get_pool()
    effective_session_id = resolve_optional_session_id()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        await resolve_session_id(
            conn,
            workspace_id=workspace_id,
            session_token=effective_session_id,
        )
        model_tier = await _get_session_model_tier(
            conn,
            workspace_id,
            effective_session_id,
        )
        subject_rows = await _require_subjects(conn, workspace_id, [subject_name])
        generation = await get_workspace_generation(conn, workspace_id)
        understanding = await _create_understanding_record(
            conn,
            workspace_id=workspace_id,
            subject_rows=subject_rows,
            content=content,
            summary=content[:160],
            kind="structural",
            generation=generation,
            session_id=effective_session_id,
            model_tier=model_tier,
        )
        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            operation="set_structural_understanding",
            detail={"subject_name": subject_name, "understanding_id": understanding["id"]},
        )

    return {
        "subject_name": subject_name,
        "understanding_id": understanding["id"],
        "created_at": understanding["created_at"].isoformat(),
    }


async def get_subjects_by_tag(
    tag: str,
    workspace: str | None = None,
) -> list[dict]:
    """Return subjects carrying the given tag."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        rows = await conn.fetch(
            """
            SELECT name, summary, tags
            FROM subjects
            WHERE workspace_id = $1
              AND $2 = ANY(tags)
            ORDER BY name
            """,
            workspace_id,
            tag,
        )

    return [
        {"name": row["name"], "summary": row["summary"], "tags": list(row["tags"])}
        for row in rows
    ]


async def add_observations(
    observations: list[dict],
    workspace: str | None = None,
    session_id: str | None = None,
    readonly: bool | None = None,
) -> list[dict]:
    """Append observations with provenance metadata."""
    ensure_request_writable(readonly)
    pool = await get_pool()
    effective_session_id = resolve_optional_session_id(session_id)

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        session_row_id = await resolve_session_id(
            conn,
            workspace_id=workspace_id,
            session_token=effective_session_id,
        )
        model_tier = await _get_session_model_tier(
            conn,
            workspace_id,
            effective_session_id,
        )
        generation = await get_workspace_generation(conn, workspace_id)
        results = []

        for item in observations:
            subject_rows, created_subjects = await _ensure_subjects(
                conn,
                workspace_id,
                item["subject_names"],
            )
            target_understanding_ids = item.get("related_to") or []
            target_observation_ids = item.get("points_to") or []
            if target_understanding_ids:
                rows = await conn.fetch(
                    """
                    SELECT id
                    FROM understandings
                    WHERE workspace_id = $1
                      AND superseded_by IS NULL
                      AND id = ANY($2)
                    """,
                    workspace_id,
                    target_understanding_ids,
                )
                found_ids = {row["id"] for row in rows}
                missing = sorted(set(target_understanding_ids) - found_ids)
                if missing:
                    raise ValueError(f"Understandings not found or inactive: {missing}")
            if target_observation_ids:
                rows = await conn.fetch(
                    """
                    SELECT id
                    FROM observations
                    WHERE workspace_id = $1
                      AND id = ANY($2)
                    """,
                    workspace_id,
                    target_observation_ids,
                )
                found_ids = {row["id"] for row in rows}
                missing = sorted(set(target_observation_ids) - found_ids)
                if missing:
                    raise ValueError(f"Observations not found: {missing}")

            content = item["content"]
            content_hash = hash_content(content)
            observation_row = await conn.fetchrow(
                """
                SELECT id, content
                FROM observations
                WHERE workspace_id = $1
                  AND content_hash = $2
                """,
                workspace_id,
                content_hash,
            )
            if observation_row is None:
                observation_row = await conn.fetchrow(
                    """
                    INSERT INTO observations (
                        workspace_id, content, content_hash, kind, confidence,
                        generation, session_id, model_tier
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING id, content, created_at
                    """,
                    workspace_id,
                    content,
                    content_hash,
                    item.get("kind"),
                    item.get("confidence"),
                    generation,
                    session_row_id,
                    model_tier,
                )
                await embed_targets(
                    conn,
                    workspace_id=workspace_id,
                    targets=[(observation_row["id"], content)],
                )

            await conn.executemany(
                """
                INSERT INTO observation_subjects (observation_id, subject_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                [
                    (observation_row["id"], subject_row["id"])
                    for subject_row in subject_rows
                ],
            )
            await _link_observation_to_understandings(
                conn,
                observation_id=observation_row["id"],
                understanding_ids=target_understanding_ids,
            )
            if observation_row["id"] in target_observation_ids:
                raise ValueError("Observations cannot point to themselves")
            if target_observation_ids:
                await conn.executemany(
                    """
                    INSERT INTO observation_links (source_observation_id, target_observation_id)
                    VALUES ($1, $2)
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        (observation_row["id"], target_observation_id)
                        for target_observation_id in target_observation_ids
                    ],
                )

            results.append(
                {
                    "id": observation_row["id"],
                    "content": observation_row["content"],
                    "subject_names": [row["name"] for row in subject_rows],
                    "subjects_created": created_subjects,
                    "points_to": target_observation_ids,
                    "pointed_to_by": [],
                }
            )

        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            operation="add_observations",
            detail={"count": len(results)},
        )

    return results


async def delete_observations(
    ids: list[int],
    workspace: str | None = None,
    session_id: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Delete observations written in the current session and generation only."""
    ensure_request_writable(readonly)
    pool = await get_pool()
    effective_session_id = resolve_optional_session_id(session_id)

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        current_generation = await get_workspace_generation(conn, workspace_id)
        rows = await conn.fetch(
            """
            SELECT o.id, o.generation, s.session_token AS session_id
            FROM observations o
            LEFT JOIN sessions s ON s.session_id = o.session_id
            WHERE o.workspace_id = $1
              AND o.id = ANY($2)
            """,
            workspace_id,
            ids,
        )
        by_id = {row["id"]: row for row in rows}
        deleted: list[int] = []
        rejected: list[dict] = []

        for observation_id in ids:
            row = by_id.get(observation_id)
            rejection_reason = _mutation_rejection_reason(
                row=row,
                effective_session_id=effective_session_id,
                current_generation=current_generation,
            )
            if rejection_reason is not None:
                rejected.append({"id": observation_id, "reason": rejection_reason})
                continue
            await conn.execute("DELETE FROM observations WHERE id = $1", observation_id)
            deleted.append(observation_id)

        if deleted:
            await record_event(
                conn,
                workspace_id=workspace_id,
                session_id=effective_session_id,
                operation="delete_observations",
                detail={"deleted": deleted},
            )

    return {"deleted": deleted, "rejected": rejected}


async def query_observations(
    subject_names: list[str],
    query: str,
    mode: str = "text",
    workspace: str | None = None,
) -> list[dict]:
    """Search within observations tagged with all given subjects."""
    pool = await get_pool()
    if mode not in {"text", "embedding"}:
        raise ValueError("mode must be 'text' or 'embedding'")

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        subject_rows = await _require_subjects(conn, workspace_id, subject_names)
        subject_ids = sorted([row["id"] for row in subject_rows])

        if mode == "embedding":
            raw_results = await search_embeddings(
                conn,
                workspace_id=workspace_id,
                query=query,
                target_kind="observation",
                limit=settings.query_observations_search_limit,
            )
            if not raw_results:
                return []

            candidate_ids = [item["id"] for item in raw_results]
            rows = await conn.fetch(
                """
                SELECT o.id
                FROM observations o
                JOIN observation_subjects os ON os.observation_id = o.id
                WHERE o.workspace_id = $1
                  AND o.id = ANY($2::bigint[])
                GROUP BY o.id
                HAVING ARRAY_AGG(os.subject_id ORDER BY os.subject_id)
                    @> $3::bigint[]
                """,
                workspace_id,
                candidate_ids,
                subject_ids,
            )
            matching_ids = {row["id"] for row in rows}
            link_ids_by_observation = await _get_observation_links(conn, list(matching_ids))
            return [
                {
                    "id": item["id"],
                    "content": item["matched_content"],
                    "score": item["score"],
                    "points_to": link_ids_by_observation.get(item["id"], {}).get("points_to", []),
                    "pointed_to_by": link_ids_by_observation.get(item["id"], {}).get("pointed_to_by", []),
                }
                for item in raw_results
                if item["id"] in matching_ids
            ]

        rows = await conn.fetch(
            """
            SELECT
                o.id,
                o.content,
                ts_rank(o.content_tsv, plainto_tsquery('english', $2), 1) AS score
            FROM observations o
            JOIN observation_subjects os ON os.observation_id = o.id
            WHERE o.workspace_id = $1
              AND o.content_tsv @@ plainto_tsquery('english', $2)
            GROUP BY o.id, o.content, o.content_tsv, o.created_at
            HAVING ARRAY_AGG(os.subject_id ORDER BY os.subject_id)
                @> $3::bigint[]
            ORDER BY score DESC, o.created_at DESC
            """,
            workspace_id,
            query,
            sorted(subject_ids),
        )
        observation_links_by_id = await _get_observation_links(
            conn,
            [row["id"] for row in rows],
        )

    return [
        {
            "id": row["id"],
            "content": row["content"],
            "score": float(row["score"]),
            "points_to": observation_links_by_id.get(row["id"], {}).get("points_to", []),
            "pointed_to_by": observation_links_by_id.get(row["id"], {}).get("pointed_to_by", []),
        }
        for row in rows
    ]


async def create_understanding(
    subject_names: list[str],
    content: str,
    summary: str,
    kind: str | None = None,
    source_observation_ids: list[int] | None = None,
    workspace: str | None = None,
    session_id: str | None = None,
    reason: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Write a consolidated understanding tagged with one or more subjects."""
    ensure_request_writable(readonly)
    pool = await get_pool()
    effective_session_id = resolve_optional_session_id(session_id)

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        await resolve_session_id(
            conn,
            workspace_id=workspace_id,
            session_token=effective_session_id,
        )
        model_tier = await _get_session_model_tier(
            conn,
            workspace_id,
            effective_session_id,
        )
        subject_rows = await _require_subjects(conn, workspace_id, subject_names)
        if source_observation_ids:
            rows = await conn.fetch(
                """
                SELECT id
                FROM observations
                WHERE workspace_id = $1
                  AND id = ANY($2)
                """,
                workspace_id,
                source_observation_ids,
            )
            found_ids = {row["id"] for row in rows}
            missing = sorted(set(source_observation_ids) - found_ids)
            if missing:
                raise ValueError(f"Observations not found: {missing}")
        generation = await get_workspace_generation(conn, workspace_id)
        effective_kind = kind or ("single_subject" if len(subject_rows) == 1 else "relationship")
        understanding = await _create_understanding_record(
            conn,
            workspace_id=workspace_id,
            subject_rows=subject_rows,
            content=content,
            summary=summary,
            kind=effective_kind,
            generation=generation,
            session_id=effective_session_id,
            source_observation_ids=source_observation_ids,
            reason=reason,
            model_tier=model_tier,
        )
        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            operation="create_understanding",
            detail={
                "understanding_id": understanding["id"],
                "kind": effective_kind,
                "subject_names": [row["name"] for row in subject_rows],
            },
        )

    return {
        "id": understanding["id"],
        "subject_names": [row["name"] for row in subject_rows],
        "kind": effective_kind,
        "created_at": understanding["created_at"].isoformat(),
    }


async def get_understandings(
    subject_names: list[str],
    workspace: str | None = None,
) -> list[dict]:
    """Return all active understandings tagged with all given subjects."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        subject_rows = await _require_subjects(conn, workspace_id, subject_names)
        subject_ids = sorted([row["id"] for row in subject_rows])
        rows = await conn.fetch(
            """
            SELECT u.id, u.content, u.summary, u.kind, u.generation, u.created_at
            FROM understandings u
            JOIN understanding_subjects us ON us.understanding_id = u.id
            WHERE u.workspace_id = $1
              AND u.superseded_by IS NULL
            GROUP BY u.id, u.content, u.summary, u.kind, u.generation, u.created_at
            HAVING ARRAY_AGG(us.subject_id ORDER BY us.subject_id)
                @> $2::bigint[]
            ORDER BY u.created_at DESC
            """,
            workspace_id,
            subject_ids,
        )

        understanding_ids = [row["id"] for row in rows]
        subject_names_by_id = await _get_subject_names_for_targets(conn, [], understanding_ids)

    return [
        {
            "id": row["id"],
            "content": row["content"],
            "summary": row["summary"],
            "kind": row["kind"],
            "generation": row["generation"],
            "created_at": row["created_at"].isoformat(),
            "subject_names": subject_names_by_id.get(row["id"], []),
        }
        for row in rows
    ]


async def get_understanding_history(
    understanding_id: int,
    workspace: str | None = None,
) -> list[dict]:
    """Walk the full supersession history connected to an understanding."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        rows = await conn.fetch(
            """
            WITH RECURSIVE history AS (
                SELECT
                    id,
                    content,
                    summary,
                    kind,
                    generation,
                    created_at,
                    superseded_by,
                    ARRAY[id] AS visited_ids
                FROM understandings
                WHERE workspace_id = $1
                  AND id = $2

                UNION ALL

                SELECT
                    u.id,
                    u.content,
                    u.summary,
                    u.kind,
                    u.generation,
                    u.created_at,
                    u.superseded_by,
                    h.visited_ids || u.id
                FROM understandings u
                JOIN history h
                  ON u.id = h.superseded_by
                  OR u.superseded_by = h.id
                WHERE u.workspace_id = $1
                  AND NOT u.id = ANY(h.visited_ids)
            )
            SELECT DISTINCT
                id,
                content,
                summary,
                kind,
                generation,
                created_at,
                superseded_by
            FROM history
            ORDER BY created_at, id
            """,
            workspace_id,
            understanding_id,
        )

        understanding_ids = [row["id"] for row in rows]
        subject_names_by_id = await _get_subject_names_for_targets(conn, [], understanding_ids)

    return [
        {
            "id": row["id"],
            "content": row["content"],
            "summary": row["summary"],
            "kind": row["kind"],
            "generation": row["generation"],
            "created_at": row["created_at"].isoformat(),
            "superseded_by": row["superseded_by"],
            "subject_names": subject_names_by_id.get(row["id"], []),
        }
        for row in rows
    ]


async def update_understanding(
    understanding_id: int,
    new_content: str,
    new_summary: str,
    subject_names: list[str] | None = None,
    reason: str | None = None,
    workspace: str | None = None,
    session_id: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Revise a consolidated understanding by supersession."""
    ensure_request_writable(readonly)
    pool = await get_pool()
    effective_session_id = resolve_optional_session_id(session_id)

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        session_row_id = await resolve_session_id(
            conn,
            workspace_id=workspace_id,
            session_token=effective_session_id,
        )
        model_tier = await _get_session_model_tier(
            conn,
            workspace_id,
            effective_session_id,
        )
        old_row = await conn.fetchrow(
            """
            SELECT id, kind, superseded_by
            FROM understandings
            WHERE workspace_id = $1
              AND id = $2
            """,
            workspace_id,
            understanding_id,
        )
        if old_row is None:
            raise ValueError(f"Understanding {understanding_id} not found")
        if old_row["superseded_by"] is not None:
            current_id = await _get_current_understanding_id(
                conn,
                workspace_id,
                understanding_id,
            )
            raise ValueError(
                f"Understanding {understanding_id} is superseded. "
                f"Current understanding is {current_id}"
            )

        if subject_names is None:
            existing_subject_rows = await conn.fetch(
                """
                SELECT s.id, s.name, s.summary, s.tags, s.single_subject_understanding_id, s.structural_understanding_id, s.created_at
                FROM understanding_subjects us
                JOIN subjects s ON s.id = us.subject_id
                WHERE us.understanding_id = $1
                ORDER BY s.name
                """,
                understanding_id,
            )
            subject_rows = [dict(row) for row in existing_subject_rows]
        else:
            subject_rows = await _require_subjects(conn, workspace_id, subject_names)

        generation = await get_workspace_generation(conn, workspace_id)
        new_row = await conn.fetchrow(
            """
            INSERT INTO understandings (
                workspace_id, content, summary, kind, generation,
                session_id, model_tier, reason
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id, created_at
            """,
            workspace_id,
            new_content,
            new_summary,
            old_row["kind"],
            generation,
            session_row_id,
            model_tier,
            reason,
        )

        await conn.executemany(
            """
            INSERT INTO understanding_subjects (understanding_id, subject_id)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            [(new_row["id"], row["id"]) for row in subject_rows],
        )
        await conn.execute(
            """
            UPDATE understandings
            SET superseded_by = $2
            WHERE id = $1
            """,
            understanding_id,
            new_row["id"],
        )

        await _update_special_pointer(
            conn,
            workspace_id=workspace_id,
            subject_id=subject_rows[0]["id"] if len(subject_rows) == 1 else None,
            kind=old_row["kind"],
            understanding_id=new_row["id"],
        )
        await embed_targets(
            conn,
            workspace_id=workspace_id,
            targets=[(new_row["id"], new_content)],
        )
        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            operation="update_understanding",
            detail={"old_understanding_id": understanding_id, "new_understanding_id": new_row["id"]},
        )

    return {
        "old_understanding_id": understanding_id,
        "new_understanding_id": new_row["id"],
        "subject_names": [row["name"] for row in subject_rows],
    }


async def delete_understanding(
    understanding_id: int,
    workspace: str | None = None,
    session_id: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Delete an active understanding written in the current session and generation."""
    ensure_request_writable(readonly)
    pool = await get_pool()
    effective_session_id = resolve_optional_session_id(session_id)

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        current_generation = await get_workspace_generation(conn, workspace_id)
        row = await conn.fetchrow(
            """
            SELECT u.id, u.kind, u.generation, u.superseded_by, s.session_token AS session_id
            FROM understandings u
            LEFT JOIN sessions s ON s.session_id = u.session_id
            WHERE u.workspace_id = $1
              AND u.id = $2
            """,
            workspace_id,
            understanding_id,
        )
        rejection_reason = _mutation_rejection_reason(
            row=row,
            effective_session_id=effective_session_id,
            current_generation=current_generation,
        )
        if rejection_reason is not None:
            raise ValueError(f"Understanding {understanding_id} cannot be deleted: {rejection_reason}")
        if row["superseded_by"] is not None:
            raise ValueError(
                f"Understanding {understanding_id} is superseded by {row['superseded_by']}"
            )

        predecessor_count = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM understandings
            WHERE workspace_id = $1
              AND superseded_by = $2
            """,
            workspace_id,
            understanding_id,
        )
        if predecessor_count:
            raise ValueError(
                f"Understanding {understanding_id} cannot be deleted because it has revision history"
            )

        await _clear_understanding_pointers(conn, understanding_id=understanding_id)
        await conn.execute("DELETE FROM understandings WHERE id = $1", understanding_id)
        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            operation="delete_understanding",
            detail={"understanding_id": understanding_id},
        )

    return {"id": understanding_id, "deleted": True}


async def rewrite_understanding(
    understanding_id: int,
    new_content: str,
    new_summary: str,
    workspace: str | None = None,
    session_id: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Rewrite an active understanding in place within the current session and generation."""
    ensure_request_writable(readonly)
    pool = await get_pool()
    effective_session_id = resolve_optional_session_id(session_id)

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        current_generation = await get_workspace_generation(conn, workspace_id)
        row = await conn.fetchrow(
            """
            SELECT u.id, u.kind, u.generation, u.superseded_by, s.session_token AS session_id
            FROM understandings u
            LEFT JOIN sessions s ON s.session_id = u.session_id
            WHERE u.workspace_id = $1
              AND u.id = $2
            """,
            workspace_id,
            understanding_id,
        )
        rejection_reason = _mutation_rejection_reason(
            row=row,
            effective_session_id=effective_session_id,
            current_generation=current_generation,
        )
        if rejection_reason is not None:
            raise ValueError(f"Understanding {understanding_id} cannot be rewritten: {rejection_reason}")
        if row["superseded_by"] is not None:
            raise ValueError(
                f"Understanding {understanding_id} is superseded by {row['superseded_by']}"
            )

        await conn.execute(
            """
            UPDATE records
            SET content = $3
            WHERE id = $1
              AND workspace_id = $2
            """,
            understanding_id,
            workspace_id,
            new_content,
        )
        await conn.execute(
            """
            UPDATE understanding_records
            SET summary = $3
            WHERE id = $1
              AND workspace_id = $2
            """,
            understanding_id,
            workspace_id,
            new_summary,
        )
        await embed_targets(
            conn,
            workspace_id=workspace_id,
            targets=[(understanding_id, new_content)],
        )
        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            operation="rewrite_understanding",
            detail={"understanding_id": understanding_id},
        )

    return {
        "understanding_id": understanding_id,
        "rewritten": True,
        "new_content": new_content,
        "new_summary": new_summary,
    }

async def _search_text(
    conn: asyncpg.Connection,
    *,
    workspace_id: int,
    query: str,
    limit: int,
) -> list[dict]:
    rows = await conn.fetch(
        """
        WITH text_matches AS (
            SELECT
                o.id,
                'observation'::text AS kind,
                NULL::text AS summary,
                o.content AS matched_content,
                o.generation,
                o.created_at,
                s.session_token AS session_id,
                o.model_tier,
                ts_rank(o.content_tsv, plainto_tsquery('english', $2), 1)
                    + CASE
                        WHEN o.created_at >= NOW() - make_interval(days => $4::int)
                        THEN $5::double precision
                        ELSE 0
                      END
                    AS score
            FROM observations o
            LEFT JOIN sessions s ON s.session_id = o.session_id
            WHERE o.workspace_id = $1
              AND o.content_tsv @@ plainto_tsquery('english', $2)

            UNION ALL

            SELECT
                u.id,
                'understanding'::text AS kind,
                u.summary,
                u.content AS matched_content,
                u.generation,
                u.created_at,
                s.session_token AS session_id,
                u.model_tier,
                ts_rank(u.content_tsv, plainto_tsquery('english', $2), 1) * $6::double precision AS score
            FROM understandings u
            LEFT JOIN sessions s ON s.session_id = u.session_id
            WHERE u.workspace_id = $1
              AND u.superseded_by IS NULL
              AND u.content_tsv @@ plainto_tsquery('english', $2)
        )
        SELECT *
        FROM text_matches
        ORDER BY score DESC, created_at DESC
        LIMIT $3
        """,
        workspace_id,
        query,
        limit,
        settings.search_recent_observation_window_days,
        settings.search_recent_observation_bonus,
        settings.search_understanding_score_multiplier,
    )
    return [dict(row) for row in rows]


async def search(
    query: str,
    limit: int = 10,
    mode: str = "embedding",
    workspace: str | None = None,
) -> list[dict]:
    """Search across understandings and observations."""
    pool = await get_pool()
    if mode not in {"embedding", "text"}:
        raise ValueError("mode must be 'embedding' or 'text'")

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        if mode == "embedding":
            raw_results = await search_embeddings(
                conn,
                workspace_id=workspace_id,
                query=query,
                limit=limit,
            )
            if not raw_results:
                raw_results = await _search_text(
                    conn,
                    workspace_id=workspace_id,
                    query=query,
                    limit=limit,
                )
        else:
            raw_results = await _search_text(
                conn,
                workspace_id=workspace_id,
                query=query,
                limit=limit,
            )

        observation_ids = [item["id"] for item in raw_results if item["kind"] == "observation"]
        understanding_ids = [item["id"] for item in raw_results if item["kind"] == "understanding"]
        subject_names_by_id = await _get_subject_names_for_targets(
            conn,
            observation_ids,
            understanding_ids,
        )
        observation_links_by_id = await _get_observation_links(conn, observation_ids)

    return [
        {
            "id": item["id"],
            "kind": item["kind"],
            "subject_names": subject_names_by_id.get(item["id"], []),
            "summary": item.get("summary"),
            "matched_content": item["matched_content"],
            "matched_perspective": item.get("matched_perspective"),
            "generation": item.get("generation"),
            "created_at": (
                item["created_at"].isoformat()
                if item.get("created_at") is not None
                else None
            ),
            "session_id": item.get("session_id"),
            "model_tier": item.get("model_tier"),
            "score": float(item["score"]),
            "points_to": (
                observation_links_by_id.get(item["id"], {}).get("points_to", [])
                if item["kind"] == "observation"
                else []
            ),
            "pointed_to_by": (
                observation_links_by_id.get(item["id"], {}).get("pointed_to_by", [])
                if item["kind"] == "observation"
                else []
            ),
        }
        for item in raw_results
    ]


async def recall(
    question_or_subject_name: str,
    workspace: str | None = None,
) -> dict:
    """Directed retrieval for a subject name or natural-language question."""
    pool = await get_pool()
    effective_session_id = resolve_optional_session_id()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        subject_row = await conn.fetchrow(
            """
            SELECT id, name, summary, tags, single_subject_understanding_id, structural_understanding_id
            FROM subjects
            WHERE workspace_id = $1
              AND name = $2
            """,
            workspace_id,
            question_or_subject_name.strip(),
        )
        if subject_row is not None:
            single_understanding = None
            if subject_row["single_subject_understanding_id"] is not None:
                single_rows = await _fetch_active_understandings_by_id(
                    conn,
                    [subject_row["single_subject_understanding_id"]],
                    allow_missing=False,
                    context=f"Single-subject understanding pointer for {subject_row['name']}",
                )
                single_understanding = single_rows[
                    subject_row["single_subject_understanding_id"]
                ]
            structural_understanding = None
            if subject_row["structural_understanding_id"] is not None:
                structural_rows = await _fetch_active_understandings_by_id(
                    conn,
                    [subject_row["structural_understanding_id"]],
                    allow_missing=False,
                    context=f"Structural understanding pointer for {subject_row['name']}",
                )
                structural_understanding = structural_rows[
                    subject_row["structural_understanding_id"]
                ]
            recent_observations = await conn.fetch(
                """
                SELECT o.id, o.content, o.kind, o.created_at
                FROM observations o
                JOIN observation_subjects os ON os.observation_id = o.id
                WHERE os.subject_id = $1
                ORDER BY o.created_at DESC
                LIMIT 5
                """,
                subject_row["id"],
            )
            observation_links_by_id = await _get_observation_links(
                conn,
                [row["id"] for row in recent_observations],
            )
            target_ids = [row["id"] for row in recent_observations]
            if single_understanding is not None:
                target_ids.append(single_understanding["id"])
            if structural_understanding is not None:
                target_ids.append(structural_understanding["id"])
            await _mark_targets_surfaced(
                conn,
                workspace_id=workspace_id,
                session_id=effective_session_id,
                target_ids=target_ids,
            )
            await record_event(
                conn,
                workspace_id=workspace_id,
                session_id=effective_session_id,
                operation="recall",
                detail={"mode": "subject", "subject_name": subject_row["name"]},
            )
            return {
                "subject": {
                    "name": subject_row["name"],
                    "summary": subject_row["summary"],
                    "tags": list(subject_row["tags"]),
                },
                "single_subject_understanding": (
                    {
                        "id": single_understanding["id"],
                        "content": single_understanding["content"],
                        "summary": single_understanding["summary"],
                        "generation": single_understanding["generation"],
                        "updated_at": single_understanding["created_at"].isoformat(),
                    }
                    if single_understanding
                    else None
                ),
                "structural_understanding": (
                    {
                        "id": structural_understanding["id"],
                        "content": structural_understanding["content"],
                        "updated_at": structural_understanding["created_at"].isoformat(),
                    }
                    if structural_understanding
                    else None
                ),
                "recent_observations": [
                    {
                        "id": row["id"],
                        "content": row["content"],
                        "kind": row["kind"],
                        "created_at": row["created_at"].isoformat(),
                        "points_to": observation_links_by_id.get(row["id"], {}).get("points_to", []),
                        "pointed_to_by": observation_links_by_id.get(row["id"], {}).get("pointed_to_by", []),
                    }
                    for row in recent_observations
                ],
            }

    search_results = await search(
        question_or_subject_name,
        limit=settings.recall_search_limit,
        workspace=workspace,
    )
    if search_results:
        async with pool.acquire() as conn:
            workspace_id = await resolve_workspace_id(conn, workspace)
            await _mark_targets_surfaced(
                conn,
                workspace_id=workspace_id,
                session_id=effective_session_id,
                target_ids=[item["id"] for item in search_results],
            )
            await record_event(
                conn,
                workspace_id=workspace_id,
                session_id=effective_session_id,
                operation="recall",
                detail={"mode": "question", "result_count": len(search_results)},
            )
    best = search_results[0] if search_results else None
    return {
        "best_answer": (
            {
                "subject_names": best["subject_names"],
                "content": best["matched_content"],
                "confidence": best["score"],
                "kind": best["kind"],
                "source": best["kind"],
            }
            if best
            else None
        ),
        "supporting": [
            {
                "subject_names": item["subject_names"],
                "content": item["matched_content"],
                "score": item["score"],
            }
            for item in search_results[1:]
        ],
        "provenance": (
            {
                "session_id": best.get("session_id"),
                "model_tier": best.get("model_tier"),
                "created_at": best.get("created_at"),
            }
            if best
            else None
        ),
    }


async def orient(
    workspace: str | None = None,
    session_id: str | None = None,
    model_tier: str | None = None,
    mode: Literal["interaction", "consolidation"] = "interaction",
) -> dict:
    """Return workspace documents and a lightweight operational envelope."""
    if mode not in {"interaction", "consolidation"}:
        raise ValueError("mode must be 'interaction' or 'consolidation'")

    pool = await get_pool()
    effective_session_id = resolve_optional_session_id(session_id)

    async with pool.acquire() as conn:
        workspace_name = resolve_effective_workspace_name(workspace)
        workspace_row = await conn.fetchrow(
            """
            SELECT
                id,
                soul_understanding_id,
                protocol_understanding_id,
                orientation_understanding_id,
                consolidation_understanding_id,
                last_consolidated_at
            FROM workspaces
            WHERE name = $1
            """,
            workspace_name,
        )
        if workspace_row is None:
            raise ValueError(f"Workspace '{workspace_name}' not found")

        workspace_id = workspace_row["id"]
        await _set_session_model_tier(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            model_tier=model_tier,
        )
        await _reset_seen_state(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
        )

        pointer_ids = [
            pointer_id
            for pointer_id in [
                workspace_row["soul_understanding_id"],
                workspace_row["protocol_understanding_id"],
                workspace_row["orientation_understanding_id"],
                workspace_row["consolidation_understanding_id"],
            ]
            if pointer_id is not None
        ]
        understanding_rows = {}
        if pointer_ids:
            understanding_rows = await _fetch_active_understandings_by_id(
                conn,
                pointer_ids,
                allow_missing=False,
                context="Workspace special understanding pointer",
            )

        pending_subjects = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM subjects s
            WHERE s.workspace_id = $1
              AND (
                  s.single_subject_understanding_id IS NULL
                  OR EXISTS (
                      SELECT 1
                      FROM observations o
                      JOIN observation_subjects os ON os.observation_id = o.id
                      JOIN understandings u ON u.id = s.single_subject_understanding_id
                      WHERE os.subject_id = s.id
                        AND o.generation > u.generation
                  )
              )
            """,
            workspace_id,
        )

        recent_activity = None
        if workspace_row["last_consolidated_at"] is not None:
            since = workspace_row["last_consolidated_at"]
            obs_rows = await conn.fetch(
                """
                SELECT DISTINCT s.name
                FROM observations o
                JOIN observation_subjects os ON os.observation_id = o.id
                JOIN subjects s ON s.id = os.subject_id
                WHERE o.workspace_id = $1
                  AND o.created_at >= $2
                ORDER BY s.name
                """,
                workspace_id,
                since,
            )
            und_rows = await conn.fetch(
                """
                SELECT DISTINCT s.name
                FROM understandings u
                JOIN understanding_subjects us ON us.understanding_id = u.id
                JOIN subjects s ON s.id = us.subject_id
                WHERE u.workspace_id = $1
                  AND u.created_at >= $2
                  AND u.superseded_by IS NULL
                ORDER BY s.name
                """,
                workspace_id,
                since,
            )
            recent_activity = {
                "since": since.isoformat(),
                "subjects_with_new_observations": [row["name"] for row in obs_rows],
                "subjects_with_new_understandings": [row["name"] for row in und_rows],
            }

        last_consolidation_event = None
        if mode == "consolidation":
            event_row = await conn.fetchrow(
                """
                SELECT e.timestamp, e.detail, s.session_token
                FROM events e
                LEFT JOIN sessions s ON s.session_id = e.session_id
                WHERE e.workspace_id = $1
                  AND e.operation = 'finalize_consolidation'
                ORDER BY e.timestamp DESC, e.id DESC
                LIMIT 1
                """,
                workspace_id,
            )
            if event_row is not None:
                detail = event_row["detail"] or {}
                if isinstance(detail, str):
                    detail = json.loads(detail)
                last_consolidation_event = {
                    "timestamp": event_row["timestamp"].isoformat(),
                    "summary": detail.get("summary"),
                    "expected_generation": detail.get("expected_generation"),
                    "new_generation": detail.get("new_generation"),
                    "updated_understanding_ids": detail.get("updated_understanding_ids", []),
                    "created_understanding_ids": detail.get("created_understanding_ids", []),
                    "session_id": event_row["session_token"],
                }

        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            operation="orient",
            detail={"session_reset": True},
        )

    def _pointer_payload(pointer_id: int | None, compaction_note: str | None) -> dict | None:
        if pointer_id is None:
            return None
        row = understanding_rows.get(pointer_id)
        if row is None:
            return None
        payload = {
            "content": row["content"],
            "summary": row["summary"],
            "updated_at": row["created_at"].isoformat(),
        }
        if compaction_note is not None:
            payload["compaction_note"] = compaction_note
        return payload

    soul_payload = _pointer_payload(
        workspace_row["soul_understanding_id"],
        (
            "CRITICAL: This content establishes the fundamental character "
            "and values for this session. It must be preserved during "
            "context compaction. Do not remove."
        ),
    )
    orientation_payload = _pointer_payload(
        workspace_row["orientation_understanding_id"],
        None,
    )
    if mode == "interaction":
        documents = {
            "soul": soul_payload,
            "protocol": _pointer_payload(
                workspace_row["protocol_understanding_id"],
                (
                    "CRITICAL: This content contains operational rules required "
                    "for correct system behaviour. Preserve during compaction."
                ),
            ),
            "orientation": orientation_payload,
        }
    else:
        documents = {
            "soul": soul_payload,
            "consolidation": _pointer_payload(
                workspace_row["consolidation_understanding_id"],
                (
                    "CRITICAL: This content contains consolidation guidance for "
                    "memory maintenance and synthesis. Preserve during compaction."
                ),
            ),
            "orientation": orientation_payload,
            "last_consolidation_event": last_consolidation_event,
        }

    return {
        **documents,
        "pending_consolidation_count": pending_subjects or 0,
        "recent_activity": recent_activity,
    }


async def finalize_consolidation(
    expected_generation: int,
    summary: str,
    updated_understanding_ids: list[int] | None = None,
    created_understanding_ids: list[int] | None = None,
    workspace: str | None = None,
    session_id: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Finalize a consolidation pass by advancing the workspace generation."""
    ensure_request_writable(readonly)
    pool = await get_pool()
    effective_session_id = resolve_optional_session_id(session_id)
    normalized_summary = summary.strip()
    if not normalized_summary:
        raise ValueError("summary is required")

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        current_generation = await get_workspace_generation(conn, workspace_id)
        if current_generation != expected_generation:
            raise ValueError(
                "Consolidation generation mismatch: "
                f"expected {expected_generation}, current {current_generation}"
            )

        row = await conn.fetchrow(
            """
            UPDATE workspaces
            SET
                current_generation = current_generation + 1,
                last_consolidated_at = NOW()
            WHERE id = $1
              AND current_generation = $2
            RETURNING current_generation, last_consolidated_at
            """,
            workspace_id,
            expected_generation,
        )
        if row is None:
            latest_generation = await get_workspace_generation(conn, workspace_id)
            raise ValueError(
                "Consolidation generation mismatch: "
                f"expected {expected_generation}, current {latest_generation}"
            )

        detail = {
            "summary": normalized_summary,
            "expected_generation": expected_generation,
            "new_generation": row["current_generation"],
            "updated_understanding_ids": sorted(updated_understanding_ids or []),
            "created_understanding_ids": sorted(created_understanding_ids or []),
        }
        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            operation="finalize_consolidation",
            detail=detail,
        )

    return {
        "summary": normalized_summary,
        "expected_generation": expected_generation,
        "new_generation": row["current_generation"],
        "updated_understanding_ids": detail["updated_understanding_ids"],
        "created_understanding_ids": detail["created_understanding_ids"],
        "last_consolidated_at": row["last_consolidated_at"].isoformat(),
    }


async def set_session_model_tier(
    model_tier: str | None = None,
    workspace: str | None = None,
    session_id: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Set or clear the model tier associated with the active session."""
    ensure_request_writable(readonly)
    pool = await get_pool()
    effective_session_id = resolve_optional_session_id(session_id)

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        stored_model_tier = await _set_session_model_tier(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            model_tier=model_tier,
        )
        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            operation="set_session_model_tier",
            detail={"model_tier": stored_model_tier},
        )

    return {
        "session_id": effective_session_id,
        "model_tier": stored_model_tier,
    }


async def get_workspace_documents(
    workspace: str | None = None,
) -> dict:
    """Return the active workspace special-document pointer IDs."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        row = await conn.fetchrow(
            """
            SELECT
                soul_understanding_id,
                protocol_understanding_id,
                orientation_understanding_id,
                consolidation_understanding_id
            FROM workspaces
            WHERE id = $1
            """,
            workspace_id,
        )
        if row is None:
            raise ValueError(f"Workspace ID {workspace_id} not found")

    return {
        "soul_understanding_id": row["soul_understanding_id"],
        "protocol_understanding_id": row["protocol_understanding_id"],
        "orientation_understanding_id": row["orientation_understanding_id"],
        "consolidation_understanding_id": row["consolidation_understanding_id"],
    }


async def set_workspace_documents(
    soul_understanding_id: int | None = None,
    protocol_understanding_id: int | None = None,
    orientation_understanding_id: int | None = None,
    consolidation_understanding_id: int | None = None,
    workspace: str | None = None,
    session_id: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Set one or more workspace special-document pointers."""
    ensure_request_writable(readonly)
    updates = {
        "soul_understanding_id": soul_understanding_id,
        "protocol_understanding_id": protocol_understanding_id,
        "orientation_understanding_id": orientation_understanding_id,
        "consolidation_understanding_id": consolidation_understanding_id,
    }
    provided_updates = {
        key: value for key, value in updates.items() if value is not None
    }
    if not provided_updates:
        raise ValueError("At least one special understanding ID must be provided")

    pool = await get_pool()
    effective_session_id = resolve_optional_session_id(session_id)

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        await _fetch_active_understandings_by_id(
            conn,
            list(provided_updates.values()),
            allow_missing=False,
            context="Workspace special understanding pointer",
        )
        set_clauses = [
            f"{column} = ${index}"
            for index, column in enumerate(provided_updates, start=2)
        ]
        row = await conn.fetchrow(
            f"""
            UPDATE workspaces
            SET {", ".join(set_clauses)}
            WHERE id = $1
            RETURNING
                soul_understanding_id,
                protocol_understanding_id,
                orientation_understanding_id,
                consolidation_understanding_id
            """,
            workspace_id,
            *provided_updates.values(),
        )
        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            operation="set_workspace_documents",
            detail=provided_updates,
        )

    return dict(row)


async def reset_seen(
    workspace: str | None = None,
    session_id: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Clear the surfaced-item log for the active session."""
    ensure_request_writable(readonly)
    pool = await get_pool()
    effective_session_id = resolve_optional_session_id(session_id)

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        cleared_count = await _reset_seen_state(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
        )
        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            operation="reset_seen",
            detail={"cleared": cleared_count},
        )

    return {"cleared": cleared_count}


async def bring_to_mind(
    topic_or_context: str,
    last_token: int | None = None,
    include_seen: bool = False,
    workspace: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Associative recall with session-scoped de-duplication and compaction recovery."""
    pool = await get_pool()
    effective_session_id = resolve_optional_session_id(session_id)
    workspace_name = resolve_effective_workspace_name(workspace)

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace_name)
        session_row = await conn.fetchrow(
            """
            SELECT seen_set_token, updated_at
            FROM sessions
            WHERE workspace_id = $1
              AND session_token = $2
            """,
            workspace_id,
            effective_session_id,
        )

        compaction_detected = False
        if session_row is not None:
            if last_token is None or session_row["seen_set_token"] != last_token:
                compaction_detected = True
            elif (
                session_row["updated_at"] is not None
                and await conn.fetchval(
                    """
                    SELECT NOW() - $1::timestamptz > make_interval(mins => $2::int)
                    """,
                    session_row["updated_at"],
                    settings.bring_to_mind_idle_reset_minutes,
                )
            ):
                compaction_detected = True

        if compaction_detected:
            await conn.execute(
                """
                DELETE FROM surfaced_in_session
                WHERE session_id = (
                    SELECT session_id
                    FROM sessions
                    WHERE workspace_id = $1
                      AND session_token = $2
                )
                """,
                workspace_id,
                effective_session_id,
            )

        seen_ids: set[int] = set()
        if not include_seen and not compaction_detected:
            rows = await conn.fetch(
                """
                SELECT id
                FROM surfaced_in_session
                WHERE session_id = (
                    SELECT session_id
                    FROM sessions
                    WHERE workspace_id = $1
                      AND session_token = $2
                )
                """,
                workspace_id,
                effective_session_id,
            )
            seen_ids = {row["id"] for row in rows}

    search_results = await search(
        topic_or_context,
        limit=settings.bring_to_mind_search_limit,
        workspace=workspace_name,
    )
    filtered_results = [
        item for item in search_results if include_seen or item["id"] not in seen_ids
    ][: settings.bring_to_mind_result_limit]

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace_name)
        await _mark_targets_surfaced(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            target_ids=[item["id"] for item in filtered_results],
        )
        heartbeat_token = await _advance_heartbeat_token(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
        )
        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            operation="bring_to_mind",
            detail={
                "topic": topic_or_context[:160],
                "compaction_detected": compaction_detected,
                "result_count": len(filtered_results),
                "include_seen": include_seen,
            },
        )

    return {
        "compaction_note": (
            "DISPOSABLE: This entire response is ephemeral. All content is "
            "retrievable on demand from the memory database. This response "
            "should be the first candidate for removal during context compaction."
        ),
        "heartbeat_token": heartbeat_token,
        "compaction_detected": compaction_detected,
        "results": [
            {
                "id": item["id"],
                "source": item["kind"],
                "subject_names": item["subject_names"],
                "summary": item["summary"],
                "content": item["matched_content"],
                "relevance_score": item["score"],
                "generation": item["generation"],
            }
            for item in filtered_results
        ],
    }


async def remember(
    subject_names: list[str],
    content: str,
    kind: str | None = None,
    confidence: float | None = None,
    related_to: list[int] | None = None,
    points_to: list[int] | None = None,
    workspace: str | None = None,
    session_id: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Append an observation tagged with one or more subjects."""
    results = await add_observations(
        [
            {
                "subject_names": subject_names,
                "content": content,
                "kind": kind,
                "confidence": confidence,
                "related_to": related_to,
                "points_to": points_to,
            }
        ],
        workspace=workspace,
        session_id=session_id,
        readonly=readonly,
    )
    return results[0]


async def mark_useful(
    id: int,
    workspace: str | None = None,
    session_id: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Record a useful signal against an observation or understanding."""
    return await _mark_signal(
        id=id,
        signal_type="useful",
        reason=None,
        workspace=workspace,
        session_id=session_id,
        readonly=readonly,
    )


async def mark_questionable(
    id: int,
    reason: str | None = None,
    workspace: str | None = None,
    session_id: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Record a questionable signal against an observation or understanding."""
    return await _mark_signal(
        id=id,
        signal_type="questionable",
        reason=reason,
        workspace=workspace,
        session_id=session_id,
        readonly=readonly,
    )


async def _mark_signal(
    *,
    id: int,
    signal_type: str,
    reason: str | None,
    workspace: str | None,
    session_id: str | None,
    readonly: bool | None = None,
) -> dict:
    ensure_request_writable(readonly)
    pool = await get_pool()
    effective_session_id = resolve_optional_session_id(session_id)

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        session_row_id = await resolve_session_id(
            conn,
            workspace_id=workspace_id,
            session_token=effective_session_id,
        )
        target_kind = await conn.fetchval(
            """
            SELECT r.record_type
            FROM records r
            LEFT JOIN understanding_records u ON u.id = r.id
            WHERE r.id = $1
              AND r.workspace_id = $2
              AND (
                  (
                      r.record_type = 'observation'
                  ) OR (
                      r.record_type = 'understanding'
                      AND u.superseded_by IS NULL
                  )
              )
            """,
            id,
            workspace_id,
        )
        if target_kind not in {"observation", "understanding"}:
            raise ValueError(
                f"ID {id} is not an active observation or understanding in this workspace"
            )

        row = await conn.fetchrow(
            """
            INSERT INTO utility_signals (
                workspace_id,
                target_id,
                signal_type,
                reason,
                session_id
            )
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id, created_at
            """,
            workspace_id,
            id,
            signal_type,
            reason,
            session_row_id,
        )
        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=effective_session_id,
            operation=f"mark_{signal_type}",
            detail={"target_id": id},
        )

    return {
        "id": row["id"],
        "target_id": id,
        "signal_type": signal_type,
        "created_at": row["created_at"].isoformat(),
    }


async def open_intersection(
    subject_a: str,
    subject_b: str,
    workspace: str | None = None,
) -> dict:
    """Return the full active intersection between two subjects."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        subject_rows = await _require_subjects(conn, workspace_id, [subject_a, subject_b])
        subject_ids = sorted([row["id"] for row in subject_rows])

        relationship_row = await conn.fetchrow(
            """
            SELECT u.id, u.content, u.summary, u.generation, u.model_tier, u.created_at
            FROM understandings u
            JOIN understanding_subjects us ON us.understanding_id = u.id
            WHERE u.workspace_id = $1
              AND u.kind = 'relationship'
              AND u.superseded_by IS NULL
            GROUP BY u.id, u.content, u.summary, u.generation, u.model_tier, u.created_at
            HAVING ARRAY_AGG(us.subject_id ORDER BY us.subject_id) = $2::bigint[]
            ORDER BY u.created_at DESC
            LIMIT 1
            """,
            workspace_id,
            subject_ids,
        )

        other_understandings = await conn.fetch(
            """
            SELECT u.id, u.summary
            FROM understandings u
            JOIN understanding_subjects us ON us.understanding_id = u.id
            WHERE u.workspace_id = $1
              AND u.superseded_by IS NULL
              AND ($2::bigint[] <@ (
                    SELECT ARRAY_AGG(us2.subject_id ORDER BY us2.subject_id)
                    FROM understanding_subjects us2
                    WHERE us2.understanding_id = u.id
              ))
              AND ($3::bigint IS NULL OR u.id != $3)
            GROUP BY u.id, u.summary, u.created_at
            ORDER BY u.created_at DESC
            """,
            workspace_id,
            subject_ids,
            relationship_row["id"] if relationship_row else None,
        )

        observations = await conn.fetch(
            """
            SELECT o.id, o.content, o.kind, o.created_at
            FROM observations o
            JOIN observation_subjects os ON os.observation_id = o.id
            WHERE o.workspace_id = $1
            GROUP BY o.id, o.content, o.kind, o.created_at
            HAVING ARRAY_AGG(os.subject_id ORDER BY os.subject_id) @> $2::bigint[]
            ORDER BY o.created_at DESC
            """,
            workspace_id,
            subject_ids,
        )
        observation_links_by_id = await _get_observation_links(
            conn,
            [row["id"] for row in observations],
        )

    return {
        "subject_a": {"name": subject_rows[0]["name"], "summary": subject_rows[0]["summary"]},
        "subject_b": {"name": subject_rows[1]["name"], "summary": subject_rows[1]["summary"]},
        "relationship_understanding": (
            {
                "id": relationship_row["id"],
                "content": relationship_row["content"],
                "summary": relationship_row["summary"],
                "generation": relationship_row["generation"],
                "model_tier": relationship_row["model_tier"],
                "created_at": relationship_row["created_at"].isoformat(),
            }
            if relationship_row
            else None
        ),
        "other_understandings": [
            {"id": row["id"], "summary": row["summary"]}
            for row in other_understandings
        ],
        "observations": [
            {
                "id": row["id"],
                "content": row["content"],
                "kind": row["kind"],
                "created_at": row["created_at"].isoformat(),
                "points_to": observation_links_by_id.get(row["id"], {}).get("points_to", []),
                "pointed_to_by": observation_links_by_id.get(row["id"], {}).get("pointed_to_by", []),
            }
            for row in observations
        ],
        "intersection_size": len(observations) + len(other_understandings) + (1 if relationship_row else 0),
    }


async def open_around(
    subject_name: str,
    workspace: str | None = None,
) -> dict:
    """Return the neighborhood of a subject ordered by intersection size."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        subject_rows = await _require_subjects(conn, workspace_id, [subject_name])
        subject_row = subject_rows[0]

        neighbor_rows = await conn.fetch(
            """
            WITH paired_observations AS (
                SELECT
                    os2.subject_id AS neighbor_id,
                    COUNT(*) AS count
                FROM observation_subjects os1
                JOIN observation_subjects os2
                  ON os1.observation_id = os2.observation_id
                JOIN observations o ON o.id = os1.observation_id
                WHERE os1.subject_id = $2
                  AND os2.subject_id != $2
                  AND o.workspace_id = $1
                GROUP BY os2.subject_id
            ),
            paired_understandings AS (
                SELECT
                    us2.subject_id AS neighbor_id,
                    COUNT(*) AS count
                FROM understanding_subjects us1
                JOIN understanding_subjects us2
                  ON us1.understanding_id = us2.understanding_id
                JOIN understandings u ON u.id = us1.understanding_id
                WHERE us1.subject_id = $2
                  AND us2.subject_id != $2
                  AND u.workspace_id = $1
                  AND u.superseded_by IS NULL
                GROUP BY us2.subject_id
            )
            SELECT
                s.id,
                s.name,
                s.summary,
                COALESCE(po.count, 0) + COALESCE(pu.count, 0) AS intersection_size
            FROM subjects s
            LEFT JOIN paired_observations po ON po.neighbor_id = s.id
            LEFT JOIN paired_understandings pu ON pu.neighbor_id = s.id
            WHERE s.workspace_id = $1
              AND s.id != $2
              AND (COALESCE(po.count, 0) + COALESCE(pu.count, 0)) > 0
            ORDER BY intersection_size DESC, s.name
            """,
            workspace_id,
            subject_row["id"],
        )

        perspective_id = await conn.fetchval(
            """
            SELECT id
            FROM perspectives
            WHERE workspace_id = $1 OR workspace_id IS NULL
            ORDER BY CASE WHEN name = 'general' THEN 0 ELSE 1 END, workspace_id NULLS LAST
            LIMIT 1
            """,
            workspace_id,
        )

        similarity_by_neighbor: dict[int, float] = {}
        if perspective_id is not None and subject_row["single_subject_understanding_id"] is not None:
            sim_rows = await conn.fetch(
                """
                SELECT
                    s.id AS neighbor_id,
                    1 - (e1.vector <=> e2.vector) AS similarity
                FROM subjects s
                JOIN embeddings e1
                  ON e1.target_id = $2
                 AND e1.workspace_id = $4
                 AND e1.perspective_id = $3
                JOIN embeddings e2
                  ON e2.target_id = s.single_subject_understanding_id
                 AND e2.workspace_id = $4
                 AND e2.perspective_id = $3
                WHERE s.id = ANY($1)
                  AND s.single_subject_understanding_id IS NOT NULL
                """,
                [row["id"] for row in neighbor_rows],
                subject_row["single_subject_understanding_id"],
                perspective_id,
                workspace_id,
            )
            similarity_by_neighbor = {
                row["neighbor_id"]: float(row["similarity"]) for row in sim_rows
            }

        relationship_ids = {}
        if neighbor_rows:
            for row in neighbor_rows:
                exact_ids = await _find_active_understanding_exact_subjects(
                    conn,
                    workspace_id=workspace_id,
                    kind="relationship",
                    subject_ids=sorted([subject_row["id"], row["id"]]),
                )
                if exact_ids:
                    rel_row = await conn.fetchrow(
                        "SELECT id, summary FROM understandings WHERE id = $1",
                        exact_ids[0],
                    )
                    relationship_ids[row["id"]] = rel_row

    return {
        "subject": {
            "name": subject_row["name"],
            "summary": subject_row["summary"],
            "tags": list(subject_row["tags"]),
        },
        "neighbors": [
            {
                "subject": {"name": row["name"], "summary": row["summary"]},
                "intersection_size": row["intersection_size"],
                "similarity_score": similarity_by_neighbor.get(row["id"], 0.0),
                "intersection_understanding": (
                    {
                        "id": relationship_ids[row["id"]]["id"],
                        "summary": relationship_ids[row["id"]]["summary"],
                    }
                    if row["id"] in relationship_ids
                    else None
                ),
            }
            for row in neighbor_rows
        ],
    }


async def get_consolidation_report(
    workspace: str | None = None,
) -> dict:
    """Return a first-pass v3 consolidation report."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        current_generation = await get_workspace_generation(conn, workspace_id)

        subjects_needing_understanding = await conn.fetch(
            """
            SELECT s.name, COUNT(os.observation_id) AS observation_count, $2::int AS generation
            FROM subjects s
            JOIN observation_subjects os ON os.subject_id = s.id
            WHERE s.workspace_id = $1
              AND s.single_subject_understanding_id IS NULL
            GROUP BY s.id
            ORDER BY observation_count DESC, s.name
            """,
            workspace_id,
            current_generation,
        )

        stale_understandings = await conn.fetch(
            """
            SELECT u.id, u.summary, u.generation, u.created_at
            FROM understandings u
            JOIN understanding_subjects us ON us.understanding_id = u.id
            JOIN subjects s ON s.id = us.subject_id
            WHERE u.workspace_id = $1
              AND u.kind = 'single_subject'
              AND u.superseded_by IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM observations o
                  JOIN observation_subjects os ON os.observation_id = o.id
                  WHERE os.subject_id = s.id
                    AND o.generation > u.generation
              )
            ORDER BY u.created_at DESC
            """,
            workspace_id,
        )

        intersections_needing_synthesis = await conn.fetch(
            """
            WITH current_pairs AS (
                SELECT
                    LEAST(s1.name, s2.name) AS subject_a,
                    GREATEST(s1.name, s2.name) AS subject_b,
                    LEAST(os1.subject_id, os2.subject_id) AS id_a,
                    GREATEST(os1.subject_id, os2.subject_id) AS id_b,
                    COUNT(*) AS new_generation_count
                FROM observations o
                JOIN observation_subjects os1 ON os1.observation_id = o.id
                JOIN observation_subjects os2 ON os2.observation_id = o.id
                JOIN subjects s1 ON s1.id = os1.subject_id
                JOIN subjects s2 ON s2.id = os2.subject_id
                WHERE o.workspace_id = $1
                  AND o.generation = $2
                  AND os1.subject_id < os2.subject_id
                GROUP BY subject_a, subject_b, id_a, id_b
            )
            SELECT *
            FROM current_pairs
            ORDER BY new_generation_count DESC, subject_a, subject_b
            """,
            workspace_id,
            current_generation,
        )

        existing_relationship_rows = {}
        if intersections_needing_synthesis:
            for row in intersections_needing_synthesis:
                exact_ids = await _find_active_understanding_exact_subjects(
                    conn,
                    workspace_id=workspace_id,
                    kind="relationship",
                    subject_ids=[row["id_a"], row["id_b"]],
                )
                if exact_ids:
                    rel_row = await conn.fetchrow(
                        "SELECT id, summary FROM understandings WHERE id = $1",
                        exact_ids[0],
                    )
                    existing_relationship_rows[(row["id_a"], row["id_b"])] = rel_row

        unlinked_observations = await conn.fetch(
            """
            SELECT
                o.id,
                o.content,
                o.created_at
            FROM observations o
            LEFT JOIN understanding_sources us ON us.observation_id = o.id
            WHERE o.workspace_id = $1
              AND us.observation_id IS NULL
            ORDER BY o.created_at DESC
            LIMIT 20
            """,
            workspace_id,
        )

        unlinked_subject_names = await _get_subject_names_for_targets(
            conn,
            [row["id"] for row in unlinked_observations],
            [],
        )

        semantically_dense_intersections = await conn.fetch(
            """
            WITH general_perspective AS (
                SELECT id
                FROM perspectives
                WHERE workspace_id = $1 OR workspace_id IS NULL
                ORDER BY CASE WHEN name = 'general' THEN 0 ELSE 1 END, workspace_id NULLS LAST
                LIMIT 1
            ),
            pair_overlap AS (
                SELECT
                    LEAST(os1.subject_id, os2.subject_id) AS subject_a_id,
                    GREATEST(os1.subject_id, os2.subject_id) AS subject_b_id,
                    COUNT(*) AS intersection_size
                FROM observation_subjects os1
                JOIN observation_subjects os2
                  ON os1.observation_id = os2.observation_id
                JOIN observations o ON o.id = os1.observation_id
                WHERE o.workspace_id = $1
                  AND os1.subject_id < os2.subject_id
                GROUP BY 1, 2
            )
            SELECT
                sa.id AS subject_a_id,
                sa.name AS subject_a,
                sb.id AS subject_b_id,
                sb.name AS subject_b,
                pair_overlap.intersection_size,
                1 - (ea.vector <=> eb.vector) AS similarity_score
            FROM pair_overlap
            JOIN subjects sa ON sa.id = pair_overlap.subject_a_id
            JOIN subjects sb ON sb.id = pair_overlap.subject_b_id
            JOIN general_perspective gp ON TRUE
            JOIN embeddings ea
              ON ea.target_id = sa.single_subject_understanding_id
             AND ea.workspace_id = $1
             AND ea.perspective_id = gp.id
            JOIN embeddings eb
              ON eb.target_id = sb.single_subject_understanding_id
             AND eb.workspace_id = $1
             AND eb.perspective_id = gp.id
            WHERE pair_overlap.intersection_size >= $2
              AND NOT EXISTS (
                  SELECT 1
                  FROM understandings u
                  JOIN understanding_subjects usa ON usa.understanding_id = u.id
                  JOIN understanding_subjects usb
                    ON usb.understanding_id = u.id
                  WHERE u.workspace_id = $1
                    AND u.kind = 'relationship'
                    AND u.superseded_by IS NULL
                    AND usa.subject_id = sa.id
                    AND usb.subject_id = sb.id
              )
            ORDER BY similarity_score DESC, pair_overlap.intersection_size DESC
            LIMIT 10
            """,
            workspace_id,
            settings.dense_intersection_min_size,
        )

        questionable_items = await conn.fetch(
            """
            SELECT target_id AS id, signal_type AS kind, reason, created_at AS flagged_at
            FROM utility_signals
            WHERE workspace_id = $1
              AND signal_type = 'questionable'
            ORDER BY created_at DESC
            LIMIT 20
            """,
            workspace_id,
        )

    stale_subject_names = []
    if stale_understandings:
        async with pool.acquire() as conn:
            names_map = await _get_subject_names_for_targets(
                conn,
                [],
                [row["id"] for row in stale_understandings],
            )
            stale_subject_names = [
                {
                    "id": row["id"],
                    "subject_names": names_map.get(row["id"], []),
                    "summary": row["summary"],
                    "generation": row["generation"],
                    "last_updated": row["created_at"].isoformat(),
                }
                for row in stale_understandings
            ]

    return {
        "subjects_needing_understanding": [
            {
                "name": row["name"],
                "observation_count": row["observation_count"],
                "generation": row["generation"],
            }
            for row in subjects_needing_understanding
        ],
        "stale_understandings": stale_subject_names,
        "intersections_needing_synthesis": [
            {
                "subject_a": row["subject_a"],
                "subject_b": row["subject_b"],
                "generation": current_generation,
                "intersection_size": row["new_generation_count"],
                "new_generation_count": row["new_generation_count"],
                "existing_understanding": (
                    {
                        "id": existing_relationship_rows[(row["id_a"], row["id_b"])]["id"],
                        "summary": existing_relationship_rows[(row["id_a"], row["id_b"])]["summary"],
                    }
                    if (row["id_a"], row["id_b"]) in existing_relationship_rows
                    else None
                ),
            }
            for row in intersections_needing_synthesis
        ],
        "semantically_dense_intersections": [
            {
                "subject_a": row["subject_a"],
                "subject_b": row["subject_b"],
                "similarity_score": float(row["similarity_score"]),
                "intersection_size": row["intersection_size"],
            }
            for row in semantically_dense_intersections
        ],
        "unlinked_observations": [
            {
                "subject_names": unlinked_subject_names.get(row["id"], []),
                "content": row["content"],
                "created_at": row["created_at"].isoformat(),
            }
            for row in unlinked_observations
        ],
        "questionable_items": [
            {
                "id": row["id"],
                "kind": row["kind"],
                "reason": row["reason"],
                "flagged_at": row["flagged_at"].isoformat(),
            }
            for row in questionable_items
        ],
    }


async def get_pending_consolidation(
    workspace: str | None = None,
) -> list[dict]:
    """Return a lightweight list of current consolidation candidates."""
    report = await get_consolidation_report(workspace=workspace)
    pending = []
    for item in report["subjects_needing_understanding"]:
        pending.append(
            {
                "item_type": "subject",
                "subject_names": [item["name"]],
                "generation": item["generation"],
                "priority": item["observation_count"],
            }
        )
    for item in report["intersections_needing_synthesis"]:
        pending.append(
            {
                "item_type": "intersection",
                "subject_names": [item["subject_a"], item["subject_b"]],
                "generation": item["generation"],
                "priority": item["intersection_size"],
            }
        )
    return sorted(pending, key=lambda item: item["priority"], reverse=True)


async def find_similar_subjects(
    limit: int = 20,
    min_score: float = 0.75,
    workspace: str | None = None,
) -> list[dict]:
    """Return semantically similar subjects using single-subject understandings."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        perspective_id = await conn.fetchval(
            """
            SELECT id
            FROM perspectives
            WHERE workspace_id = $1 OR workspace_id IS NULL
            ORDER BY CASE WHEN name = 'general' THEN 0 ELSE 1 END, workspace_id NULLS LAST
            LIMIT 1
            """,
            workspace_id,
        )
        if perspective_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT
                sa.name AS subject_a,
                sb.name AS subject_b,
                1 - (ea.vector <=> eb.vector) AS similarity_score
            FROM subjects sa
            JOIN subjects sb
              ON sb.workspace_id = sa.workspace_id
             AND sb.id > sa.id
            JOIN embeddings ea
              ON ea.target_id = sa.single_subject_understanding_id
             AND ea.workspace_id = $1
             AND ea.perspective_id = $2
            JOIN embeddings eb
              ON eb.target_id = sb.single_subject_understanding_id
             AND eb.workspace_id = $1
             AND eb.perspective_id = $2
            WHERE sa.workspace_id = $1
              AND sa.single_subject_understanding_id IS NOT NULL
              AND sb.single_subject_understanding_id IS NOT NULL
              AND 1 - (ea.vector <=> eb.vector) >= $3
            ORDER BY similarity_score DESC
            LIMIT $4
            """,
            workspace_id,
            perspective_id,
            min_score,
            limit,
        )

    return [
        {
            "subject_a": row["subject_a"],
            "subject_b": row["subject_b"],
            "similarity_score": float(row["similarity_score"]),
        }
        for row in rows
    ]


async def merge_subjects(
    primary: str,
    duplicate: str,
    workspace: str | None = None,
    readonly: bool | None = None,
) -> dict:
    """Merge duplicate subject tags into a primary subject."""
    ensure_request_writable(readonly)
    pool = await get_pool()
    session_id = resolve_optional_session_id()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        subject_rows = await _require_subjects(conn, workspace_id, [primary, duplicate])
        primary_row = next(row for row in subject_rows if row["name"] == primary)
        duplicate_row = next(row for row in subject_rows if row["name"] == duplicate)

        await conn.executemany(
            """
            INSERT INTO observation_subjects (observation_id, subject_id)
            SELECT observation_id, $2
            FROM observation_subjects
            WHERE subject_id = $1
            ON CONFLICT DO NOTHING
            """,
            [(duplicate_row["id"], primary_row["id"])],
        )
        await conn.executemany(
            """
            INSERT INTO understanding_subjects (understanding_id, subject_id)
            SELECT understanding_id, $2
            FROM understanding_subjects
            WHERE subject_id = $1
            ON CONFLICT DO NOTHING
            """,
            [(duplicate_row["id"], primary_row["id"])],
        )

        merged_tags = sorted(set(primary_row["tags"]) | set(duplicate_row["tags"]))
        await conn.execute(
            """
            UPDATE subjects
            SET tags = $2
            WHERE id = $1
            """,
            primary_row["id"],
            merged_tags,
        )
        await conn.execute("DELETE FROM subjects WHERE id = $1", duplicate_row["id"])
        await record_event(
            conn,
            workspace_id=workspace_id,
            session_id=session_id,
            operation="merge_subjects",
            detail={"primary": primary, "duplicate": duplicate},
        )

    return {"primary": primary, "duplicate": duplicate, "merged": True}


async def get_stats(
    workspace: str | None = None,
) -> dict:
    """Summary statistics for the active workspace."""
    pool = await get_pool()
    workspace_name = resolve_effective_workspace_name(workspace)

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace_name)
        row = await conn.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM subjects WHERE workspace_id = $1) AS subject_count,
                (SELECT COUNT(*) FROM observations WHERE workspace_id = $1) AS observation_count,
                (
                    SELECT COUNT(*)
                    FROM understandings
                    WHERE workspace_id = $1
                      AND superseded_by IS NULL
                ) AS understanding_count,
                (SELECT current_generation FROM workspaces WHERE id = $1) AS current_generation,
                CASE
                    WHEN (
                        SELECT COUNT(*) FROM (
                            SELECT id FROM observations WHERE workspace_id = $1
                            UNION ALL
                            SELECT id FROM understandings WHERE workspace_id = $1 AND superseded_by IS NULL
                        ) targets
                    ) = 0 THEN NULL
                    ELSE ROUND(
                        (
                            SELECT COUNT(DISTINCT e.target_id)
                            FROM embeddings e
                            JOIN records r
                              ON r.id = e.target_id
                             AND r.workspace_id = e.workspace_id
                            LEFT JOIN understanding_records u ON u.id = r.id
                            WHERE e.workspace_id = $1
                              AND (
                                r.record_type = 'observation'
                                OR (
                                    r.record_type = 'understanding'
                                    AND u.superseded_by IS NULL
                                )
                            )
                        )::numeric
                        /
                        (
                            SELECT COUNT(*) FROM (
                                SELECT id FROM observations WHERE workspace_id = $1
                                UNION ALL
                                SELECT id FROM understandings WHERE workspace_id = $1 AND superseded_by IS NULL
                            ) targets
                        ),
                        3
                    )
                END AS embedding_coverage
            """,
            workspace_id,
        )

    return {
        "subject_count": row["subject_count"],
        "observation_count": row["observation_count"],
        "understanding_count": row["understanding_count"],
        "embedding_coverage": (
            float(row["embedding_coverage"])
            if row["embedding_coverage"] is not None
            else None
        ),
        "current_generation": row["current_generation"],
        "workspace": workspace_name,
    }
