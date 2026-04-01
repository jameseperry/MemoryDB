"""Observation management tools."""

import asyncpg

from memory_mcp.db import get_pool, resolve_workspace_id
from memory_mcp.embeddings import delete_observation_embeddings, embed_observations


async def _get_node_id(
    conn: asyncpg.Connection, workspace_id: int | None, name: str
) -> int | None:
    return await conn.fetchval(
        "SELECT id FROM nodes WHERE workspace_id IS NOT DISTINCT FROM $1 AND name = $2",
        workspace_id, name,
    )


async def add_observations(
    observations: list[dict],
    workspace: str | None = None,
) -> list[dict]:
    """Append observations to existing nodes.

    Each item: {entity_name, contents: [str, ...]}.
    Returns list of {entity_name, added: [{ordinal, content}], not_found: bool}.
    """
    pool = await get_pool()
    results = []

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)

        for item in observations:
            entity_name = item["entity_name"]
            contents = item["contents"]

            node_id = await _get_node_id(conn, workspace_id, entity_name)
            if node_id is None:
                results.append({"entity_name": entity_name, "added": [], "not_found": True})
                continue

            current_max = await conn.fetchval(
                "SELECT COALESCE(MAX(ordinal), -1) FROM observations WHERE node_id = $1",
                node_id,
            )

            next_ordinal = current_max + 1
            await conn.executemany(
                "INSERT INTO observations (node_id, ordinal, content) VALUES ($1, $2, $3)",
                [(node_id, next_ordinal + i, content) for i, content in enumerate(contents)],
            )
            rows = await conn.fetch(
                "SELECT id, ordinal, content FROM observations WHERE node_id = $1 AND ordinal >= $2 ORDER BY ordinal",
                node_id, next_ordinal,
            )

            await embed_observations(
                conn, node_id, workspace_id, [r["id"] for r in rows]
            )

            await conn.execute(
                """
                INSERT INTO events (node_id, workspace_id, operation)
                VALUES ($1, $2, 'add_observations')
                """,
                node_id, workspace_id,
            )

            results.append({
                "entity_name": entity_name,
                "added": [{"ordinal": r["ordinal"], "content": r["content"]} for r in rows],
                "not_found": False,
            })

    return results


async def replace_observation(
    entity_name: str,
    ordinal: int,
    new_content: str,
    workspace: str | None = None,
) -> dict:
    """Replace a single observation in-place by its ordinal.

    Returns {entity_name, ordinal, old_content, new_content}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        node_id = await _get_node_id(conn, workspace_id, entity_name)
        if node_id is None:
            raise ValueError(f"Node '{entity_name}' not found")

        row = await conn.fetchrow(
            """
            UPDATE observations
            SET content = $3
            WHERE node_id = $1 AND ordinal = $2
            RETURNING id, ordinal, content AS new_content
            """,
            node_id, ordinal, new_content,
        )
        if row is None:
            raise ValueError(f"Observation at ordinal {ordinal} not found on '{entity_name}'")

        await embed_observations(conn, node_id, workspace_id, [row["id"]])

        await conn.execute(
            "INSERT INTO events (node_id, workspace_id, operation) VALUES ($1, $2, 'replace_observation')",
            node_id, workspace_id,
        )

    return {
        "entity_name": entity_name,
        "ordinal": row["ordinal"],
        "new_content": row["new_content"],
    }


async def delete_observations(
    deletions: list[dict],
    workspace: str | None = None,
) -> list[dict]:
    """Delete specific observations by ordinal. Remaining ordinals are not renumbered.

    Each item: {entity_name, ordinals: [int, ...]}.
    Returns list of {entity_name, deleted_ordinals, not_found_ordinals}.
    """
    pool = await get_pool()
    results = []

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)

        for item in deletions:
            entity_name = item["entity_name"]
            ordinals = item["ordinals"]

            node_id = await _get_node_id(conn, workspace_id, entity_name)
            if node_id is None:
                results.append({
                    "entity_name": entity_name,
                    "deleted_ordinals": [],
                    "not_found_ordinals": ordinals,
                })
                continue

            existing = await conn.fetch(
                "SELECT ordinal FROM observations WHERE node_id = $1 AND ordinal = ANY($2)",
                node_id, ordinals,
            )
            existing_set = {r["ordinal"] for r in existing}
            to_delete = list(existing_set)
            not_found_ordinals = [o for o in ordinals if o not in existing_set]

            if to_delete:
                obs_id_rows = await conn.fetch(
                    "SELECT id FROM observations WHERE node_id = $1 AND ordinal = ANY($2)",
                    node_id, to_delete,
                )
                obs_ids_to_delete = [r["id"] for r in obs_id_rows]
                await conn.execute(
                    "DELETE FROM observations WHERE node_id = $1 AND ordinal = ANY($2)",
                    node_id, to_delete,
                )
                await delete_observation_embeddings(conn, node_id, workspace_id, obs_ids_to_delete)

            results.append({
                "entity_name": entity_name,
                "deleted_ordinals": sorted(to_delete),
                "not_found_ordinals": sorted(not_found_ordinals),
            })

    return results


async def query_observations(
    entity_name: str,
    query: str,
    mode: str = "embedding",
    workspace: str | None = None,
) -> list[dict]:
    """Search within a single node's observations.

    mode: 'embedding' (default) or 'text' (full-text search).
    Returns list of {ordinal, content, score}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        node_id = await _get_node_id(conn, workspace_id, entity_name)
        if node_id is None:
            raise ValueError(f"Node '{entity_name}' not found")

        if mode == "text":
            rows = await conn.fetch(
                """
                SELECT ordinal, content,
                       ts_rank(content_tsv, plainto_tsquery('english', $2)) AS score
                FROM observations
                WHERE node_id = $1
                  AND content_tsv @@ plainto_tsquery('english', $2)
                ORDER BY score DESC
                """,
                node_id, query,
            )
            return [
                {"ordinal": r["ordinal"], "content": r["content"], "score": float(r["score"])}
                for r in rows
            ]

        # Embedding mode — requires embedding pipeline; returns empty until implemented
        raise NotImplementedError(
            "Embedding mode for query_observations requires the embedding pipeline. "
            "Use mode='text' for now, or implement the embedding service."
        )
