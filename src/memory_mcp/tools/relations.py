"""Relation management tools."""

import asyncpg

from memory_mcp.db import get_pool, resolve_workspace_id


async def _get_node_id(
    conn: asyncpg.Connection, workspace_id: int, name: str
) -> int | None:
    return await conn.fetchval(
        "SELECT id FROM nodes WHERE workspace_id = $1 AND name = $2",
        workspace_id, name,
    )


async def create_relations(
    relations: list[dict],
    workspace: str | None = None,
) -> dict:
    """Create directed typed edges between nodes.

    Each item: {from_entity, to_entity, relation_type}.
    Returns {created: [...], already_existed: [...], not_found: [str, ...]}.
    """
    pool = await get_pool()
    created = []
    already_existed = []
    not_found = []

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)

        for rel in relations:
            from_name = rel["from_entity"]
            to_name = rel["to_entity"]
            rel_type = rel["relation_type"]

            from_id = await _get_node_id(conn, workspace_id, from_name)
            to_id = await _get_node_id(conn, workspace_id, to_name)

            missing = [n for n, nid in [(from_name, from_id), (to_name, to_id)] if nid is None]
            if missing:
                not_found.extend(missing)
                continue

            result = await conn.fetchrow(
                """
                INSERT INTO relations (workspace_id, from_node_id, to_node_id, relation_type)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                workspace_id, from_id, to_id, rel_type,
            )

            entry = {"from": from_name, "to": to_name, "relation_type": rel_type}
            if result:
                created.append(entry)
            else:
                already_existed.append(entry)

    return {"created": created, "already_existed": already_existed, "not_found": not_found}


async def delete_relations(
    relations: list[dict],
    workspace: str | None = None,
) -> dict:
    """Delete specific relations.

    Each item: {from_entity, to_entity, relation_type}.
    Returns {deleted: int, not_found: int}.
    """
    pool = await get_pool()
    deleted = 0
    not_found = 0

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)

        for rel in relations:
            from_id = await _get_node_id(conn, workspace_id, rel["from_entity"])
            to_id = await _get_node_id(conn, workspace_id, rel["to_entity"])

            if from_id is None or to_id is None:
                not_found += 1
                continue

            result = await conn.execute(
                """
                DELETE FROM relations
                WHERE workspace_id = $1
                  AND from_node_id = $2
                  AND to_node_id = $3
                  AND relation_type = $4
                """,
                workspace_id, from_id, to_id, rel["relation_type"],
            )
            # execute() returns e.g. "DELETE 1"
            count = int(result.split()[-1])
            if count:
                deleted += count
            else:
                not_found += 1

    return {"deleted": deleted, "not_found": not_found}


async def update_relation_type(
    from_entity: str,
    to_entity: str,
    old_type: str,
    new_type: str,
    workspace: str | None = None,
) -> dict:
    """Rename the type string on an existing relation.

    Returns {from_entity, to_entity, old_type, new_type}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        from_id = await _get_node_id(conn, workspace_id, from_entity)
        to_id = await _get_node_id(conn, workspace_id, to_entity)

        if from_id is None or to_id is None:
            missing = from_entity if from_id is None else to_entity
            raise ValueError(f"Node '{missing}' not found")

        result = await conn.execute(
            """
            UPDATE relations
            SET relation_type = $5
            WHERE workspace_id = $1
              AND from_node_id = $2
              AND to_node_id = $3
              AND relation_type = $4
            """,
            workspace_id, from_id, to_id, old_type, new_type,
        )
        if result == "UPDATE 0":
            raise ValueError(
                f"Relation '{old_type}' from '{from_entity}' to '{to_entity}' not found"
            )

    return {
        "from_entity": from_entity,
        "to_entity": to_entity,
        "old_type": old_type,
        "new_type": new_type,
    }


async def get_relations_between(
    entity_a: str,
    entity_b: str,
    workspace: str | None = None,
) -> list[dict]:
    """Return all relations between two nodes (both directions).

    Returns list of {from, to, relation_type}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        a_id = await _get_node_id(conn, workspace_id, entity_a)
        b_id = await _get_node_id(conn, workspace_id, entity_b)

        if a_id is None or b_id is None:
            return []

        rows = await conn.fetch(
            """
            SELECT fn.name AS from_name, tn.name AS to_name, r.relation_type
            FROM relations r
            JOIN nodes fn ON fn.id = r.from_node_id
            JOIN nodes tn ON tn.id = r.to_node_id
            WHERE r.workspace_id = $1
              AND ((r.from_node_id = $2 AND r.to_node_id = $3)
                OR (r.from_node_id = $3 AND r.to_node_id = $2))
            """,
            workspace_id, a_id, b_id,
        )

    return [
        {"from": r["from_name"], "to": r["to_name"], "relation_type": r["relation_type"]}
        for r in rows
    ]
