"""Node CRUD tools."""

import asyncpg

from memory_mcp.db import get_pool, resolve_workspace_id
from memory_mcp.embeddings import embed_observations


async def create_entities(
    entities: list[dict],
    workspace: str | None = None,
) -> list[dict]:
    """Create one or more nodes.

    Each entity dict: {name, entity_type, observations?, summary?, tags?}
    Returns list of {name, entity_type, created_at}.
    """
    pool = await get_pool()
    results = []

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)

        for entity in entities:
            name = entity["name"]
            entity_type = entity["entity_type"]
            summary = entity.get("summary")
            tags = entity.get("tags", [])
            obs_contents = entity.get("observations", [])

            # ON CONFLICT can't target partial indexes, so check existence explicitly.
            existing = await conn.fetchrow(
                "SELECT id FROM nodes WHERE workspace_id IS NOT DISTINCT FROM $1 AND name = $2",
                workspace_id, name,
            )
            if existing:
                node_id = existing["id"]
                row = await conn.fetchrow(
                    """
                    UPDATE nodes
                    SET entity_type = $2,
                        summary     = COALESCE($3, summary),
                        updated_at  = NOW()
                    WHERE id = $1
                    RETURNING name, entity_type, created_at
                    """,
                    node_id, entity_type, summary,
                )
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO nodes (workspace_id, name, entity_type, summary, tags)
                    VALUES ($1, $2, $3, $4, $5)
                    RETURNING id, name, entity_type, created_at
                    """,
                    workspace_id, name, entity_type, summary, tags,
                )
                node_id = row["id"]

            if obs_contents:
                current_max = await conn.fetchval(
                    "SELECT COALESCE(MAX(ordinal), -1) FROM observations WHERE node_id = $1",
                    node_id,
                )
                new_obs_ids = await conn.fetch(
                    """
                    INSERT INTO observations (node_id, ordinal, content)
                    SELECT $1, $2 + gs, unnest
                    FROM unnest($3::text[]) WITH ORDINALITY AS t(unnest, gs)
                    RETURNING id
                    """,
                    node_id, current_max, obs_contents,
                )
                await embed_observations(
                    conn, node_id, workspace_id, [r["id"] for r in new_obs_ids]
                )

            await conn.execute(
                """
                INSERT INTO events (node_id, workspace_id, operation, detail)
                VALUES ($1, $2, 'create_node', $3::jsonb)
                """,
                node_id, workspace_id, f'{{"name": "{name}"}}',
            )

            results.append({
                "name": row["name"],
                "entity_type": row["entity_type"],
                "created_at": row["created_at"].isoformat(),
            })

    return results


async def delete_entities(
    entity_names: list[str],
    workspace: str | None = None,
) -> dict:
    """Delete nodes by name (cascades to observations, relations, embeddings, events).

    Returns {deleted: [...], not_found: [...]}.
    """
    pool = await get_pool()
    deleted = []
    not_found = []

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)

        for name in entity_names:
            result = await conn.fetchval(
                """
                DELETE FROM nodes
                WHERE workspace_id IS NOT DISTINCT FROM $1 AND name = $2
                RETURNING name
                """,
                workspace_id, name,
            )
            if result:
                deleted.append(name)
            else:
                not_found.append(name)

    return {"deleted": deleted, "not_found": not_found}


async def open_nodes(
    names: list[str],
    workspace: str | None = None,
) -> dict:
    """Retrieve full node content: observations (ordered), summary, tags, relation stubs.

    Returns {entities: [...], relations: [...], not_found: [...]}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)

        node_rows = await conn.fetch(
            """
            SELECT id, name, entity_type, summary, tags, created_at, updated_at
            FROM nodes
            WHERE workspace_id IS NOT DISTINCT FROM $1 AND name = ANY($2)
            """,
            workspace_id, names,
        )

        found_names = {r["name"] for r in node_rows}
        not_found = [n for n in names if n not in found_names]
        node_ids = [r["id"] for r in node_rows]

        obs_rows = await conn.fetch(
            """
            SELECT node_id, ordinal, content
            FROM observations
            WHERE node_id = ANY($1)
            ORDER BY node_id, ordinal
            """,
            node_ids,
        )

        obs_by_node: dict[int, list] = {nid: [] for nid in node_ids}
        for obs in obs_rows:
            obs_by_node[obs["node_id"]].append({
                "ordinal": obs["ordinal"],
                "content": obs["content"],
            })

        relation_rows = await conn.fetch(
            """
            SELECT fn.name AS from_name, tn.name AS to_name, r.relation_type
            FROM relations r
            JOIN nodes fn ON fn.id = r.from_node_id
            JOIN nodes tn ON tn.id = r.to_node_id
            WHERE r.from_node_id = ANY($1) OR r.to_node_id = ANY($1)
            """,
            node_ids,
        )

        entities = []
        for row in node_rows:
            entities.append({
                "name": row["name"],
                "entity_type": row["entity_type"],
                "summary": row["summary"],
                "tags": list(row["tags"]),
                "observations": obs_by_node[row["id"]],
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat(),
            })

        relations = [
            {
                "from": r["from_name"],
                "to": r["to_name"],
                "relation_type": r["relation_type"],
            }
            for r in relation_rows
        ]

    return {"entities": entities, "relations": relations, "not_found": not_found}


async def get_nodes_by_type(
    entity_type: str,
    workspace: str | None = None,
) -> list[dict]:
    """List all nodes of a given entity type.

    Returns list of {name, entity_type, summary, tags, updated_at}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        rows = await conn.fetch(
            """
            SELECT name, entity_type, summary, tags, updated_at
            FROM nodes
            WHERE workspace_id IS NOT DISTINCT FROM $1 AND entity_type = $2
            ORDER BY name
            """,
            workspace_id, entity_type,
        )

    return [
        {
            "name": r["name"],
            "entity_type": r["entity_type"],
            "summary": r["summary"],
            "tags": list(r["tags"]),
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]


async def get_recently_modified(
    days: int = 7,
    limit: int = 20,
    workspace: str | None = None,
) -> list[dict]:
    """Return nodes modified in the last N days, newest first.

    Returns list of {name, entity_type, summary, updated_at}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        rows = await conn.fetch(
            """
            SELECT name, entity_type, summary, updated_at
            FROM nodes
            WHERE workspace_id IS NOT DISTINCT FROM $1
              AND updated_at >= NOW() - ($2 || ' days')::INTERVAL
            ORDER BY updated_at DESC
            LIMIT $3
            """,
            workspace_id, str(days), limit,
        )

    return [
        {
            "name": r["name"],
            "entity_type": r["entity_type"],
            "summary": r["summary"],
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in rows
    ]


async def set_summary(
    name: str,
    summary: str,
    workspace: str | None = None,
) -> dict:
    """Set or replace the summary field on a node.

    Returns {name, summary, updated_at}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        row = await conn.fetchrow(
            """
            UPDATE nodes
            SET summary = $3, summary_updated_at = NOW(), updated_at = NOW()
            WHERE workspace_id IS NOT DISTINCT FROM $1 AND name = $2
            RETURNING name, summary, updated_at
            """,
            workspace_id, name, summary,
        )

    if row is None:
        raise ValueError(f"Node '{name}' not found")

    return {
        "name": row["name"],
        "summary": row["summary"],
        "updated_at": row["updated_at"].isoformat(),
    }


async def set_tags(
    name: str,
    tags: list[str],
    workspace: str | None = None,
) -> dict:
    """Replace the tag set on a node.

    Returns {name, tags}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        row = await conn.fetchrow(
            """
            UPDATE nodes
            SET tags = $3, updated_at = NOW()
            WHERE workspace_id IS NOT DISTINCT FROM $1 AND name = $2
            RETURNING name, tags
            """,
            workspace_id, name, tags,
        )

    if row is None:
        raise ValueError(f"Node '{name}' not found")

    return {"name": row["name"], "tags": list(row["tags"])}
