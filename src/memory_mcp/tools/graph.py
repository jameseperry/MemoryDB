"""Graph traversal tools."""

from memory_mcp.db import get_pool, resolve_workspace_id


async def get_neighborhood(
    name: str,
    depth: int = 1,
    workspace: str | None = None,
) -> dict:
    """Return a node and all nodes within N hops, with the connecting subgraph.

    Returns {nodes: [{name, entity_type, summary, tags}], relations: [{from, to, relation_type}]}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)

        # Bidirectional BFS via recursive CTE.
        # visited array prevents cycles; depth cap prevents runaway queries.
        node_rows = await conn.fetch(
            """
            WITH RECURSIVE neighborhood(id, name, entity_type, summary, tags, depth, visited) AS (
                SELECT n.id, n.name, n.entity_type, n.summary, n.tags, 0, ARRAY[n.id]
                FROM nodes n
                WHERE n.workspace_id IS NOT DISTINCT FROM $1 AND n.name = $2

                UNION ALL

                SELECT n.id, n.name, n.entity_type, n.summary, n.tags,
                       nb.depth + 1,
                       nb.visited || n.id
                FROM nodes n
                JOIN relations r
                    ON r.from_node_id = n.id OR r.to_node_id = n.id
                JOIN neighborhood nb
                    ON (r.from_node_id = nb.id OR r.to_node_id = nb.id)
                WHERE nb.depth < $3
                  AND NOT n.id = ANY(nb.visited)
                  AND n.workspace_id IS NOT DISTINCT FROM $1
            )
            SELECT DISTINCT id, name, entity_type, summary, tags
            FROM neighborhood
            """,
            workspace_id, name, depth,
        )

        if not node_rows:
            return {"nodes": [], "relations": []}

        node_ids = [r["id"] for r in node_rows]

        rel_rows = await conn.fetch(
            """
            SELECT fn.name AS from_name, tn.name AS to_name, r.relation_type
            FROM relations r
            JOIN nodes fn ON fn.id = r.from_node_id
            JOIN nodes tn ON tn.id = r.to_node_id
            WHERE r.from_node_id = ANY($1) AND r.to_node_id = ANY($1)
            """,
            node_ids,
        )

    return {
        "nodes": [
            {
                "name": r["name"],
                "entity_type": r["entity_type"],
                "summary": r["summary"],
                "tags": list(r["tags"]),
            }
            for r in node_rows
        ],
        "relations": [
            {"from": r["from_name"], "to": r["to_name"], "relation_type": r["relation_type"]}
            for r in rel_rows
        ],
    }


async def get_path(
    from_entity: str,
    to_entity: str,
    workspace: str | None = None,
) -> dict:
    """Find shortest relation path between two nodes (treats relations as undirected).

    Returns {found: bool, path: [str], relations: [{from, to, relation_type}]}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)

        row = await conn.fetchrow(
            """
            WITH RECURSIVE path_search(id, name, path_ids, path_names, depth) AS (
                SELECT n.id, n.name, ARRAY[n.id], ARRAY[n.name], 0
                FROM nodes n
                WHERE n.workspace_id IS NOT DISTINCT FROM $1 AND n.name = $2

                UNION ALL

                SELECT n.id, n.name,
                       ps.path_ids || n.id,
                       ps.path_names || n.name,
                       ps.depth + 1
                FROM nodes n
                JOIN relations r
                    ON r.from_node_id = n.id OR r.to_node_id = n.id
                JOIN path_search ps
                    ON (r.from_node_id = ps.id OR r.to_node_id = ps.id)
                WHERE NOT n.id = ANY(ps.path_ids)
                  AND ps.depth < 10
                  AND n.workspace_id IS NOT DISTINCT FROM $1
            )
            SELECT path_ids, path_names
            FROM path_search
            WHERE name = $3
            ORDER BY depth
            LIMIT 1
            """,
            workspace_id, from_entity, to_entity,
        )

        if row is None:
            return {"found": False, "path": [], "relations": []}

        path_ids: list[int] = list(row["path_ids"])
        path_names: list[str] = list(row["path_names"])

        # Fetch the actual relations along the path (in order).
        relations = []
        for i in range(len(path_ids) - 1):
            a, b = path_ids[i], path_ids[i + 1]
            rel_rows = await conn.fetch(
                """
                SELECT fn.name AS from_name, tn.name AS to_name, r.relation_type
                FROM relations r
                JOIN nodes fn ON fn.id = r.from_node_id
                JOIN nodes tn ON tn.id = r.to_node_id
                WHERE (r.from_node_id = $1 AND r.to_node_id = $2)
                   OR (r.from_node_id = $2 AND r.to_node_id = $1)
                """,
                a, b,
            )
            relations.extend(
                {"from": r["from_name"], "to": r["to_name"], "relation_type": r["relation_type"]}
                for r in rel_rows
            )

    return {"found": True, "path": path_names, "relations": relations}


async def get_orphans(
    workspace: str | None = None,
) -> list[dict]:
    """Return nodes with no relations.

    Returns list of {name, entity_type, summary, updated_at}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        rows = await conn.fetch(
            """
            SELECT n.name, n.entity_type, n.summary, n.updated_at
            FROM nodes n
            WHERE n.workspace_id IS NOT DISTINCT FROM $1
              AND NOT EXISTS (
                  SELECT 1 FROM relations r
                  WHERE r.from_node_id = n.id OR r.to_node_id = n.id
              )
            ORDER BY n.updated_at DESC
            """,
            workspace_id,
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


async def get_relation_gaps(
    workspace: str | None = None,
) -> list[dict]:
    """Find nodes referenced by name in other nodes' observations but with no formal relation.

    Heuristic text-match — results need judgment to confirm.
    Returns list of {node, referenced_name, reference_count}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        rows = await conn.fetch(
            """
            SELECT
                src.name AS node,
                ref.name AS referenced_name,
                COUNT(*) AS reference_count
            FROM observations o
            JOIN nodes src ON src.id = o.node_id
            JOIN nodes ref
                ON ref.workspace_id IS NOT DISTINCT FROM src.workspace_id
               AND ref.id != src.id
               AND o.content ILIKE '%' || ref.name || '%'
            WHERE src.workspace_id IS NOT DISTINCT FROM $1
              AND NOT EXISTS (
                  SELECT 1 FROM relations r
                  WHERE (r.from_node_id = src.id AND r.to_node_id = ref.id)
                     OR (r.from_node_id = ref.id AND r.to_node_id = src.id)
              )
            GROUP BY src.name, ref.name
            ORDER BY reference_count DESC
            """,
            workspace_id,
        )

    return [
        {
            "node": r["node"],
            "referenced_name": r["referenced_name"],
            "reference_count": r["reference_count"],
        }
        for r in rows
    ]


async def find_similar_nodes(
    workspace: str | None = None,
    limit: int = 20,
    min_score: float = 0.75,
) -> list[dict]:
    """Find pairs of nodes that are semantically similar but have no existing relation.

    Uses per-node aggregate embeddings (mean-pooled). Uses 'general' perspective;
    falls back to any available perspective if 'general' is not found.
    Returns list of {node_a, node_b, similarity, node_a_type, node_b_type}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)

        # Pick perspective: prefer 'general', fall back to first available.
        perspective_id = await conn.fetchval(
            """
            SELECT id FROM perspectives
            WHERE (workspace_id IS NOT DISTINCT FROM $1 OR workspace_id IS NULL)
            ORDER BY CASE WHEN name = 'general' THEN 0 ELSE 1 END,
                     workspace_id NULLS LAST
            LIMIT 1
            """,
            workspace_id,
        )
        if perspective_id is None:
            return []

        rows = await conn.fetch(
            """
            SELECT
                na.name      AS node_a,
                nb.name      AS node_b,
                na.entity_type AS node_a_type,
                nb.entity_type AS node_b_type,
                1 - (nea.vector <=> neb.vector) AS similarity
            FROM node_embeddings nea
            JOIN node_embeddings neb
                ON neb.perspective_id = nea.perspective_id
               AND neb.node_id > nea.node_id
            JOIN nodes na ON na.id = nea.node_id
            JOIN nodes nb ON nb.id = neb.node_id
            WHERE nea.perspective_id = $2
              AND na.workspace_id IS NOT DISTINCT FROM $1
              AND nb.workspace_id IS NOT DISTINCT FROM $1
              AND 1 - (nea.vector <=> neb.vector) >= $3
              AND NOT EXISTS (
                  SELECT 1 FROM relations r
                  WHERE (r.from_node_id = nea.node_id AND r.to_node_id = neb.node_id)
                     OR (r.from_node_id = neb.node_id AND r.to_node_id = nea.node_id)
              )
            ORDER BY similarity DESC
            LIMIT $4
            """,
            workspace_id, perspective_id, min_score, limit,
        )

    return [
        {
            "node_a": r["node_a"],
            "node_b": r["node_b"],
            "similarity": float(r["similarity"]),
            "node_a_type": r["node_a_type"],
            "node_b_type": r["node_b_type"],
        }
        for r in rows
    ]
