"""Memory MCP server entry point."""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastmcp import FastMCP

from memory_mcp.db import close_pool, init_pool
from memory_mcp.tools import (
    consolidation,
    graph,
    nodes,
    observations,
    relations,
    search,
)
from memory_mcp.config import settings


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[None]:
    await init_pool()
    try:
        yield
    finally:
        await close_pool()


mcp = FastMCP(
    name="memory",
    instructions=(
        "Persistent memory graph backed by Postgres + pgvector. "
        "Stores named nodes with typed observations and directed relations. "
        "Supports semantic search, graph traversal, and consolidation workflows."
    ),
    lifespan=lifespan,
)

# --- Register all tools ---

mcp.add_tool(nodes.create_entities)
mcp.add_tool(nodes.delete_entities)
mcp.add_tool(nodes.open_nodes)
mcp.add_tool(nodes.get_nodes_by_type)
mcp.add_tool(nodes.get_recently_modified)
mcp.add_tool(nodes.set_summary)
mcp.add_tool(nodes.set_tags)

mcp.add_tool(observations.add_observations)
mcp.add_tool(observations.replace_observation)
mcp.add_tool(observations.delete_observations)
mcp.add_tool(observations.query_observations)

mcp.add_tool(relations.create_relations)
mcp.add_tool(relations.delete_relations)
mcp.add_tool(relations.update_relation_type)
mcp.add_tool(relations.get_relations_between)

mcp.add_tool(graph.get_neighborhood)
mcp.add_tool(graph.get_path)
mcp.add_tool(graph.get_orphans)
mcp.add_tool(graph.get_relation_gaps)
mcp.add_tool(graph.find_similar_nodes)

mcp.add_tool(search.search_nodes)

mcp.add_tool(consolidation.get_consolidation_report)
mcp.add_tool(consolidation.get_pending_consolidation)
mcp.add_tool(consolidation.get_stats)


def main() -> None:
    mcp.run(transport="sse", port=settings.mcp_port)


if __name__ == "__main__":
    main()
