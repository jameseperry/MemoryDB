"""Search tools."""


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
    raise NotImplementedError
