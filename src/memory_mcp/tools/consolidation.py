"""Consolidation and stats tools."""

from memory_mcp.db import get_pool, resolve_workspace_id


async def get_consolidation_report(
    workspace: str | None = None,
) -> dict:
    """Full consolidation report: stale summaries, relation candidates, orphans, event log.

    Backend computes candidates; Claude applies judgment.
    Returns {stale_summaries, relation_candidates, orphaned_nodes, event_summary}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)

        # Stale summaries: no summary, or summary predates latest observation.
        stale_rows = await conn.fetch(
            """
            SELECT
                n.name,
                n.entity_type,
                n.summary_updated_at,
                COUNT(o.id)      AS observation_count,
                MAX(o.created_at) AS last_observation_at
            FROM nodes n
            LEFT JOIN observations o ON o.node_id = n.id
            WHERE n.workspace_id IS NOT DISTINCT FROM $1
            GROUP BY n.id
            HAVING n.summary_updated_at IS NULL
                OR n.summary_updated_at < MAX(o.created_at)
            ORDER BY last_observation_at DESC NULLS LAST
            """,
            workspace_id,
        )

        # Relation candidates: semantically similar node pairs with no formal relation.
        # Reuses find_similar_nodes logic inline to avoid double pool.acquire.
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

        relation_candidates = []
        if perspective_id is not None:
            cand_rows = await conn.fetch(
                """
                SELECT
                    na.name AS node_a,
                    nb.name AS node_b,
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
                  AND 1 - (nea.vector <=> neb.vector) >= 0.75
                  AND NOT EXISTS (
                      SELECT 1 FROM relations r
                      WHERE (r.from_node_id = nea.node_id AND r.to_node_id = neb.node_id)
                         OR (r.from_node_id = neb.node_id AND r.to_node_id = nea.node_id)
                  )
                ORDER BY similarity DESC
                LIMIT 20
                """,
                workspace_id, perspective_id,
            )
            relation_candidates = [
                {"node_a": r["node_a"], "node_b": r["node_b"], "similarity_score": float(r["similarity"])}
                for r in cand_rows
            ]

        # Orphaned nodes.
        orphan_rows = await conn.fetch(
            """
            SELECT n.name, n.entity_type, n.updated_at
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

        # Event log summary since last consolidation report request.
        # "Since last consolidation" = last 30 days as a practical default.
        event_row = await conn.fetchrow(
            """
            SELECT
                MIN(occurred_at) AS since,
                COUNT(*) FILTER (WHERE operation LIKE 'create%') AS creates,
                COUNT(*) FILTER (WHERE operation LIKE '%observation%' OR operation = 'replace_observation') AS updates,
                COUNT(*) FILTER (WHERE operation LIKE 'delete%') AS deletes
            FROM events
            WHERE (workspace_id IS NOT DISTINCT FROM $1 OR $1 IS NULL)
              AND occurred_at >= NOW() - INTERVAL '30 days'
            """,
            workspace_id,
        )

    return {
        "stale_summaries": [
            {
                "name": r["name"],
                "entity_type": r["entity_type"],
                "observation_count": r["observation_count"],
                "last_observation_at": r["last_observation_at"].isoformat() if r["last_observation_at"] else None,
                "summary_updated_at": r["summary_updated_at"].isoformat() if r["summary_updated_at"] else None,
            }
            for r in stale_rows
        ],
        "relation_candidates": relation_candidates,
        "orphaned_nodes": [
            {
                "name": r["name"],
                "entity_type": r["entity_type"],
                "updated_at": r["updated_at"].isoformat(),
            }
            for r in orphan_rows
        ],
        "event_summary": {
            "since": event_row["since"].isoformat() if event_row["since"] else None,
            "creates": event_row["creates"] or 0,
            "updates": event_row["updates"] or 0,
            "deletes": event_row["deletes"] or 0,
        },
    }


async def get_pending_consolidation(
    workspace: str | None = None,
) -> list[dict]:
    """Nodes that have no summary, or whose summary predates their last observation.

    Returns list of {name, entity_type, observation_count, last_observation_at, summary_updated_at}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)
        rows = await conn.fetch(
            """
            SELECT
                n.name,
                n.entity_type,
                COUNT(o.id)       AS observation_count,
                MAX(o.created_at) AS last_observation_at,
                n.summary_updated_at
            FROM nodes n
            LEFT JOIN observations o ON o.node_id = n.id
            WHERE n.workspace_id IS NOT DISTINCT FROM $1
            GROUP BY n.id
            HAVING n.summary_updated_at IS NULL
                OR n.summary_updated_at < MAX(o.created_at)
            ORDER BY last_observation_at DESC NULLS LAST
            """,
            workspace_id,
        )

    return [
        {
            "name": r["name"],
            "entity_type": r["entity_type"],
            "observation_count": r["observation_count"],
            "last_observation_at": r["last_observation_at"].isoformat() if r["last_observation_at"] else None,
            "summary_updated_at": r["summary_updated_at"].isoformat() if r["summary_updated_at"] else None,
        }
        for r in rows
    ]


async def get_stats(
    workspace: str | None = None,
) -> dict:
    """Summary statistics for the workspace.

    Returns {node_count, observation_count, relation_count, embedding_coverage, workspace}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)

        row = await conn.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM nodes
                 WHERE workspace_id IS NOT DISTINCT FROM $1) AS node_count,

                (SELECT COUNT(*)
                 FROM observations o
                 JOIN nodes n ON n.id = o.node_id
                 WHERE n.workspace_id IS NOT DISTINCT FROM $1) AS observation_count,

                (SELECT COUNT(*) FROM relations
                 WHERE workspace_id IS NOT DISTINCT FROM $1) AS relation_count,

                -- embedding coverage: fraction of observations with at least one embedding
                CASE WHEN obs_total.cnt = 0 THEN NULL
                     ELSE ROUND(obs_embedded.cnt::numeric / obs_total.cnt, 3)
                END AS embedding_coverage
            FROM
                (SELECT COUNT(*) AS cnt
                 FROM observations o
                 JOIN nodes n ON n.id = o.node_id
                 WHERE n.workspace_id IS NOT DISTINCT FROM $1) AS obs_total,

                (SELECT COUNT(DISTINCT e.observation_id) AS cnt
                 FROM embeddings e
                 JOIN observations o ON o.id = e.observation_id
                 JOIN nodes n ON n.id = o.node_id
                 WHERE n.workspace_id IS NOT DISTINCT FROM $1) AS obs_embedded
            """,
            workspace_id,
        )

    return {
        "node_count": row["node_count"],
        "observation_count": row["observation_count"],
        "relation_count": row["relation_count"],
        "embedding_coverage": float(row["embedding_coverage"]) if row["embedding_coverage"] is not None else None,
        "workspace": workspace,
    }
