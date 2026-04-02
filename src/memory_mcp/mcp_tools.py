"""MCP-facing tool wrappers.

These wrappers intentionally omit the `workspace` parameter from the exposed
tool schema. The effective workspace is resolved from the HTTP transport
context by the underlying implementation.
"""

import logging

from fastmcp.server.dependencies import get_context

from memory_mcp.db import resolve_effective_workspace_name
from memory_mcp.tools import (
    consolidation,
    graph,
    nodes,
    observations as observations_tools,
    relations as relations_tools,
    search,
)

logger = logging.getLogger(__name__)


def _log_tool_call(tool_name: str) -> None:
    """Log the active MCP tool invocation with workspace and session context."""
    try:
        workspace = resolve_effective_workspace_name(None)
    except Exception:
        workspace = "<unresolved>"

    try:
        session_id = get_context().session_id
    except Exception:
        session_id = "<unresolved>"

    logger.info(
        "mcp_tool_call tool=%s workspace=%s session_id=%s",
        tool_name,
        workspace,
        session_id,
    )


async def create_entities(entities: list[dict]) -> list[dict]:
    """Create one or more nodes.

    Each entity dict: {name, entity_type, observations?, summary?, tags?}
    Returns list of {name, entity_type, created_at}.
    """
    _log_tool_call("create_entities")
    return await nodes.create_entities(entities)


async def delete_entities(entity_names: list[str]) -> dict:
    """Delete nodes by name (cascades to observations, relations, embeddings, events).

    Returns {deleted: [...], not_found: [...]}.
    """
    _log_tool_call("delete_entities")
    return await nodes.delete_entities(entity_names)


async def open_nodes(names: list[str]) -> dict:
    """Retrieve full node content: observations (ordered), summary, tags, relation stubs.

    Returns {entities: [...], relations: [...], not_found: [...]}.
    """
    _log_tool_call("open_nodes")
    return await nodes.open_nodes(names)


async def get_nodes_by_type(entity_type: str) -> list[dict]:
    """List all nodes of a given entity type.

    Returns list of {name, entity_type, summary, tags, updated_at}.
    """
    _log_tool_call("get_nodes_by_type")
    return await nodes.get_nodes_by_type(entity_type)


async def get_recently_modified(days: int = 7, limit: int = 20) -> list[dict]:
    """Return nodes modified in the last N days, newest first."""
    _log_tool_call("get_recently_modified")
    return await nodes.get_recently_modified(days=days, limit=limit)


async def set_summary(name: str, summary: str) -> dict:
    """Set or replace the summary field on a node."""
    _log_tool_call("set_summary")
    return await nodes.set_summary(name, summary)


async def set_tags(name: str, tags: list[str]) -> dict:
    """Replace the tag set on a node."""
    _log_tool_call("set_tags")
    return await nodes.set_tags(name, tags)


async def add_observations(observations: list[dict]) -> list[dict]:
    """Append observations to existing nodes.

    Each item: {entity_name, contents: [str, ...]}.
    Returns list of {entity_name, added: [{ordinal, content}], not_found: bool}.
    """
    _log_tool_call("add_observations")
    return await observations_tools.add_observations(observations)


async def replace_observation(entity_name: str, ordinal: int, new_content: str) -> dict:
    """Replace a single observation in-place by its ordinal."""
    _log_tool_call("replace_observation")
    return await observations_tools.replace_observation(entity_name, ordinal, new_content)


async def delete_observations(deletions: list[dict]) -> list[dict]:
    """Delete specific observations by ordinal. Remaining ordinals are not renumbered."""
    _log_tool_call("delete_observations")
    return await observations_tools.delete_observations(deletions)


async def query_observations(
    entity_name: str,
    query: str,
    mode: str = "embedding",
) -> list[dict]:
    """Search within a single node's observations."""
    _log_tool_call("query_observations")
    return await observations_tools.query_observations(
        entity_name,
        query,
        mode=mode,
    )


async def create_relations(relations: list[dict]) -> dict:
    """Create directed typed edges between nodes."""
    _log_tool_call("create_relations")
    return await relations_tools.create_relations(relations)


async def delete_relations(relations: list[dict]) -> dict:
    """Delete specific relations."""
    _log_tool_call("delete_relations")
    return await relations_tools.delete_relations(relations)


async def update_relation_type(
    from_entity: str,
    to_entity: str,
    old_type: str,
    new_type: str,
) -> dict:
    """Rename the type string on an existing relation."""
    _log_tool_call("update_relation_type")
    return await relations_tools.update_relation_type(
        from_entity,
        to_entity,
        old_type,
        new_type,
    )


async def get_relations_between(entity_a: str, entity_b: str) -> list[dict]:
    """Return all relations between two nodes (both directions)."""
    _log_tool_call("get_relations_between")
    return await relations_tools.get_relations_between(entity_a, entity_b)


async def get_neighborhood(name: str, depth: int = 1) -> dict:
    """Return a node and all nodes within N hops, with the connecting subgraph."""
    _log_tool_call("get_neighborhood")
    return await graph.get_neighborhood(name, depth=depth)


async def get_path(from_entity: str, to_entity: str) -> dict:
    """Find shortest relation path between two nodes (treats relations as undirected)."""
    _log_tool_call("get_path")
    return await graph.get_path(from_entity, to_entity)


async def get_orphans() -> list[dict]:
    """Return nodes with no relations."""
    _log_tool_call("get_orphans")
    return await graph.get_orphans()


async def get_relation_gaps() -> list[dict]:
    """Find nodes referenced by name in other nodes' observations but with no formal relation."""
    _log_tool_call("get_relation_gaps")
    return await graph.get_relation_gaps()


async def find_similar_nodes(limit: int = 20, min_score: float = 0.75) -> list[dict]:
    """Find pairs of nodes that are semantically similar but have no existing relation."""
    _log_tool_call("find_similar_nodes")
    return await graph.find_similar_nodes(limit=limit, min_score=min_score)


async def search_nodes(
    query: str,
    limit: int = 10,
    mode: str = "embedding",
) -> list[dict]:
    """Search across all nodes."""
    _log_tool_call("search_nodes")
    return await search.search_nodes(query, limit=limit, mode=mode)


async def get_consolidation_report() -> dict:
    """Full consolidation report: stale summaries, relation candidates, orphans, event log."""
    _log_tool_call("get_consolidation_report")
    return await consolidation.get_consolidation_report()


async def get_pending_consolidation() -> list[dict]:
    """Nodes that have no summary, or whose summary predates their last observation."""
    _log_tool_call("get_pending_consolidation")
    return await consolidation.get_pending_consolidation()


async def get_stats() -> dict:
    """Summary statistics for the active workspace."""
    _log_tool_call("get_stats")
    return await consolidation.get_stats()
