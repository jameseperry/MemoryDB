"""Search tools."""

import asyncio

from memory_mcp.db import get_pool, resolve_workspace_id
from memory_mcp.embeddings import embed_query, get_perspectives


async def search_nodes(
    query: str,
    limit: int = 10,
    mode: str = "embedding",
    workspace: str | None = None,
) -> list[dict]:
    """Search across all nodes.

    mode: 'embedding' (multi-perspective semantic, default) or 'text' (Postgres FTS).
    Returns list of {name, entity_type, summary, matched_observation, matched_perspective, score}.
    """
    pool = await get_pool()

    async with pool.acquire() as conn:
        workspace_id = await resolve_workspace_id(conn, workspace)

        if mode == "text":
            return await _search_text(conn, workspace_id, query, limit)
        else:
            return await _search_embedding(conn, workspace_id, query, limit)


async def _search_text(conn, workspace_id, query: str, limit: int) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT
            n.name,
            n.entity_type,
            n.summary,
            o.content AS matched_observation,
            ts_rank(o.content_tsv, plainto_tsquery('english', $2), 1) AS score
        FROM observations o
        JOIN nodes n ON n.id = o.node_id
        WHERE n.workspace_id IS NOT DISTINCT FROM $1
          AND o.content_tsv @@ plainto_tsquery('english', $2)
        ORDER BY score DESC
        LIMIT $3
        """,
        workspace_id, query, limit,
    )
    return [
        {
            "name": r["name"],
            "entity_type": r["entity_type"],
            "summary": r["summary"],
            "matched_observation": r["matched_observation"],
            "matched_perspective": None,
            "score": float(r["score"]),
        }
        for r in rows
    ]


async def _search_embedding(conn, workspace_id, query: str, limit: int) -> list[dict]:
    perspectives = await get_perspectives(conn, workspace_id)
    if not perspectives:
        return []

    loop = asyncio.get_event_loop()

    # Embed query from each perspective in a thread pool, run concurrently.
    vectors = await asyncio.gather(*[
        loop.run_in_executor(None, embed_query, query, p["instruction"])
        for p in perspectives
    ])

    # Query each perspective and collect candidates.
    # We over-fetch per perspective then deduplicate, keeping best score per observation.
    per_perspective_limit = limit * 2
    candidates: dict[int, dict] = {}  # observation_id → best result

    for perspective, vector in zip(perspectives, vectors):
        rows = await conn.fetch(
            """
            SELECT
                n.name,
                n.entity_type,
                n.summary,
                o.id AS obs_id,
                o.content AS matched_observation,
                1 - (e.vector <=> $3::vector) AS score
            FROM embeddings e
            JOIN observations o ON o.id = e.observation_id
            JOIN nodes n ON n.id = o.node_id
            WHERE e.perspective_id = $2
              AND n.workspace_id IS NOT DISTINCT FROM $1
            ORDER BY e.vector <=> $3::vector
            LIMIT $4
            """,
            workspace_id, perspective["id"], str(vector), per_perspective_limit,
        )
        for row in rows:
            obs_id = row["obs_id"]
            score = float(row["score"])
            if obs_id not in candidates or score > candidates[obs_id]["score"]:
                candidates[obs_id] = {
                    "name": row["name"],
                    "entity_type": row["entity_type"],
                    "summary": row["summary"],
                    "matched_observation": row["matched_observation"],
                    "matched_perspective": perspective["name"],
                    "score": score,
                }

    # Sort by score, deduplicate by node name (keep best per node), return top N.
    sorted_results = sorted(candidates.values(), key=lambda r: r["score"], reverse=True)
    seen_nodes: set[str] = set()
    deduped = []
    for result in sorted_results:
        if result["name"] not in seen_nodes:
            seen_nodes.add(result["name"])
            deduped.append(result)
        if len(deduped) >= limit:
            break

    return deduped
