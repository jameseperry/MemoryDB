"""MCP-facing tool wrappers.

These wrappers intentionally omit the `workspace` parameter from the exposed
tool schema. The effective workspace is resolved from the HTTP transport
context by the underlying implementation.
"""

from memory_mcp.tools import (
    consolidation,
    graph,
    nodes,
    observations as observations_tools,
    relations as relations_tools,
    search,
)


async def create_entities(entities: list[dict]) -> list[dict]:
    """Create one or more nodes.

    Each entity dict: {name, entity_type, observations?, summary?, tags?}
    Returns list of {name, entity_type, created_at}.
    """
    return await nodes.create_entities(entities)


async def delete_entities(entity_names: list[str]) -> dict:
    """Delete nodes by name (cascades to observations, relations, embeddings, events).

    Returns {deleted: [...], not_found: [...]}.
    """
    return await nodes.delete_entities(entity_names)


async def open_nodes(names: list[str]) -> dict:
    """Retrieve full node content: observations (ordered), summary, tags, relation stubs.

    Returns {entities: [...], relations: [...], not_found: [...]}.
    """
    return await nodes.open_nodes(names)


async def get_nodes_by_type(entity_type: str) -> list[dict]:
    """List all nodes of a given entity type.

    Returns list of {name, entity_type, summary, tags, updated_at}.
    """
    return await nodes.get_nodes_by_type(entity_type)


async def get_recently_modified(days: int = 7, limit: int = 20) -> list[dict]:
    """Return nodes modified in the last N days, newest first."""
    return await nodes.get_recently_modified(days=days, limit=limit)


async def set_summary(name: str, summary: str) -> dict:
    """Set or replace the summary field on a node."""
    return await nodes.set_summary(name, summary)


async def set_tags(name: str, tags: list[str]) -> dict:
    """Replace the tag set on a node."""
    return await nodes.set_tags(name, tags)


async def add_observations(observations: list[dict]) -> list[dict]:
    """Append observations to existing nodes.

    Each item: {entity_name, contents: [str, ...]}.
    Returns list of {entity_name, added: [{ordinal, content}], not_found: bool}.
    """
    return await observations_tools.add_observations(observations)


async def replace_observation(entity_name: str, ordinal: int, new_content: str) -> dict:
    """Replace a single observation in-place by its ordinal."""
    return await observations_tools.replace_observation(entity_name, ordinal, new_content)


async def delete_observations(deletions: list[dict]) -> list[dict]:
    """Delete specific observations by ordinal. Remaining ordinals are not renumbered."""
    return await observations_tools.delete_observations(deletions)


async def query_observations(
    entity_name: str,
    query: str,
    mode: str = "embedding",
) -> list[dict]:
    """Search within a single node's observations."""
    return await observations_tools.query_observations(
        entity_name,
        query,
        mode=mode,
    )


async def create_relations(relations: list[dict]) -> dict:
    """Create directed typed edges between nodes."""
    return await relations_tools.create_relations(relations)


async def delete_relations(relations: list[dict]) -> dict:
    """Delete specific relations."""
    return await relations_tools.delete_relations(relations)


async def update_relation_type(
    from_entity: str,
    to_entity: str,
    old_type: str,
    new_type: str,
) -> dict:
    """Rename the type string on an existing relation."""
    return await relations_tools.update_relation_type(
        from_entity,
        to_entity,
        old_type,
        new_type,
    )


async def get_relations_between(entity_a: str, entity_b: str) -> list[dict]:
    """Return all relations between two nodes (both directions)."""
    return await relations_tools.get_relations_between(entity_a, entity_b)


async def get_neighborhood(name: str, depth: int = 1) -> dict:
    """Return a node and all nodes within N hops, with the connecting subgraph."""
    return await graph.get_neighborhood(name, depth=depth)


async def get_path(from_entity: str, to_entity: str) -> dict:
    """Find shortest relation path between two nodes (treats relations as undirected)."""
    return await graph.get_path(from_entity, to_entity)


async def get_orphans() -> list[dict]:
    """Return nodes with no relations."""
    return await graph.get_orphans()


async def get_relation_gaps() -> list[dict]:
    """Find nodes referenced by name in other nodes' observations but with no formal relation."""
    return await graph.get_relation_gaps()


async def find_similar_nodes(limit: int = 20, min_score: float = 0.75) -> list[dict]:
    """Find pairs of nodes that are semantically similar but have no existing relation."""
    return await graph.find_similar_nodes(limit=limit, min_score=min_score)


async def search_nodes(
    query: str,
    limit: int = 10,
    mode: str = "embedding",
) -> list[dict]:
    """Search across all nodes."""
    return await search.search_nodes(query, limit=limit, mode=mode)


async def get_consolidation_report() -> dict:
    """Full consolidation report: stale summaries, relation candidates, orphans, event log."""
    return await consolidation.get_consolidation_report()


async def get_pending_consolidation() -> list[dict]:
    """Nodes that have no summary, or whose summary predates their last observation."""
    return await consolidation.get_pending_consolidation()


async def get_stats() -> dict:
    """Summary statistics for the active workspace."""
    return await consolidation.get_stats()
