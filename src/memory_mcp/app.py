"""v1 Memory MCP app definition."""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastmcp import FastMCP

from memory_mcp import mcp_tools
from memory_mcp.db import close_pool, init_pool


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[None]:
    await init_pool()
    try:
        yield
    finally:
        await close_pool()


def create_mcp_server() -> FastMCP:
    """Build the v1 Memory MCP FastMCP app."""
    mcp = FastMCP(
        name="memory",
        instructions=(
            "Persistent memory graph backed by Postgres + pgvector. "
            "Stores named nodes with typed observations and directed relations. "
            "Supports semantic search, graph traversal, and consolidation workflows."
        ),
        lifespan=lifespan,
    )

    mcp.add_tool(mcp_tools.create_entities)
    mcp.add_tool(mcp_tools.delete_entities)
    mcp.add_tool(mcp_tools.open_nodes)
    mcp.add_tool(mcp_tools.get_nodes_by_type)
    mcp.add_tool(mcp_tools.get_recently_modified)
    mcp.add_tool(mcp_tools.set_summary)
    mcp.add_tool(mcp_tools.set_tags)

    mcp.add_tool(mcp_tools.add_observations)
    mcp.add_tool(mcp_tools.replace_observation)
    mcp.add_tool(mcp_tools.delete_observations)
    mcp.add_tool(mcp_tools.query_observations)

    mcp.add_tool(mcp_tools.create_relations)
    mcp.add_tool(mcp_tools.delete_relations)
    mcp.add_tool(mcp_tools.update_relation_type)
    mcp.add_tool(mcp_tools.get_relations_between)

    mcp.add_tool(mcp_tools.get_neighborhood)
    mcp.add_tool(mcp_tools.get_path)
    mcp.add_tool(mcp_tools.get_orphans)
    mcp.add_tool(mcp_tools.get_relation_gaps)
    mcp.add_tool(mcp_tools.find_similar_nodes)

    mcp.add_tool(mcp_tools.search_nodes)

    mcp.add_tool(mcp_tools.get_consolidation_report)
    mcp.add_tool(mcp_tools.get_pending_consolidation)
    mcp.add_tool(mcp_tools.get_stats)

    return mcp
