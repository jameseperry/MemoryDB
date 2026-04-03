"""Embedding helpers for v3 subjects/observations/understandings."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import asyncpg

from memory_mcp.embeddings import embed_documents, embed_query
from memory_v3.config import settings

if TYPE_CHECKING:
    from collections.abc import Sequence


async def get_perspectives(
    conn: asyncpg.Connection,
    workspace_id: int,
) -> list[dict]:
    """Return workspace perspectives, falling back to global defaults."""
    rows = await conn.fetch(
        """
        SELECT id, workspace_id, name, instruction
        FROM perspectives
        WHERE workspace_id = $1 OR workspace_id IS NULL
        ORDER BY workspace_id NULLS LAST, name
        """,
        workspace_id,
    )
    seen: dict[str, dict] = {}
    for row in rows:
        name = row["name"]
        if name not in seen or row["workspace_id"] is not None:
            seen[name] = {
                "id": row["id"],
                "name": name,
                "instruction": row["instruction"],
            }
    return list(seen.values())


async def embed_targets(
    conn: asyncpg.Connection,
    *,
    workspace_id: int,
    targets: Sequence[tuple[int, str]],
    model_version: str | None = None,
) -> None:
    """Compute and store embeddings for target objects."""
    if not targets:
        return
    if model_version is None:
        model_version = settings.embed_model_name

    perspectives = await get_perspectives(conn, workspace_id)
    if not perspectives:
        return

    target_ids = [target_id for target_id, _ in targets]
    texts = [content for _, content in targets]
    loop = asyncio.get_event_loop()

    for perspective in perspectives:
        vectors = await loop.run_in_executor(
            None,
            embed_documents,
            texts,
            perspective["instruction"],
        )
        await conn.executemany(
            """
            INSERT INTO embeddings (
                workspace_id,
                target_id,
                perspective_id,
                vector,
                model_version
            )
            VALUES ($1, $2, $3, $4::vector, $5)
            ON CONFLICT (workspace_id, target_id, perspective_id)
                DO UPDATE SET
                    vector = EXCLUDED.vector,
                    model_version = EXCLUDED.model_version,
                    created_at = NOW()
            """,
            [
                (
                    workspace_id,
                    target_id,
                    perspective["id"],
                    str(vector),
                    model_version,
                )
                for target_id, vector in zip(target_ids, vectors)
            ],
        )


async def search_embeddings(
    conn: asyncpg.Connection,
    *,
    workspace_id: int,
    query: str,
    target_kind: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search embeddings across active understandings and observations."""
    perspectives = await get_perspectives(conn, workspace_id)
    if not perspectives:
        return []

    loop = asyncio.get_event_loop()
    vectors = await asyncio.gather(
        *[
            loop.run_in_executor(None, embed_query, query, perspective["instruction"])
            for perspective in perspectives
        ]
    )

    candidates: dict[int, dict] = {}

    for perspective, vector in zip(perspectives, vectors):
        rows = await conn.fetch(
            """
            WITH active_targets AS (
                SELECT
                    o.id AS target_id,
                    'observation'::text AS target_kind,
                    o.content AS matched_content,
                    o.generation,
                    o.created_at,
                    s.session_token AS session_id,
                    o.model_tier,
                    NULL::text AS summary
                FROM observations o
                LEFT JOIN sessions s ON s.session_id = o.session_id
                WHERE o.workspace_id = $1

                UNION ALL

                SELECT
                    u.id AS target_id,
                    'understanding'::text AS target_kind,
                    u.content AS matched_content,
                    u.generation,
                    u.created_at,
                    s.session_token AS session_id,
                    u.model_tier,
                    u.summary
                FROM understandings u
                LEFT JOIN sessions s ON s.session_id = u.session_id
                WHERE u.workspace_id = $1
                  AND u.superseded_by IS NULL
            )
            SELECT
                a.target_id,
                a.target_kind,
                a.summary,
                a.matched_content,
                a.generation,
                a.created_at,
                a.session_id,
                a.model_tier,
                1 - (e.vector <=> $3::vector) AS score
            FROM embeddings e
            JOIN active_targets a ON a.target_id = e.target_id
            WHERE e.workspace_id = $1
              AND e.perspective_id = $2
              AND ($4::text IS NULL OR a.target_kind = $4)
            ORDER BY e.vector <=> $3::vector
            LIMIT $5
            """,
            workspace_id,
            perspective["id"],
            str(vector),
            target_kind,
            limit * 2,
        )

        for row in rows:
            target_id = row["target_id"]
            score = float(row["score"])
            if target_id not in candidates or score > candidates[target_id]["score"]:
                candidates[target_id] = {
                    "id": target_id,
                    "kind": row["target_kind"],
                    "summary": row["summary"],
                    "matched_content": row["matched_content"],
                    "matched_perspective": perspective["name"],
                    "generation": row["generation"],
                    "score": score,
                    "created_at": row["created_at"],
                    "session_id": row["session_id"],
                    "model_tier": row["model_tier"],
                }

    return sorted(candidates.values(), key=lambda item: item["score"], reverse=True)[:limit]
