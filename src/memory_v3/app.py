"""v3 Memory MCP app definition."""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastmcp import FastMCP

from memory_v3 import mcp_tools
from memory_v3.db import close_pool, init_pool


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[None]:
    await init_pool()
    try:
        yield
    finally:
        await close_pool()


def create_mcp_server() -> FastMCP:
    """Build the v3 Memory MCP FastMCP app."""
    mcp = FastMCP(
        name="memory-v3",
        instructions=(
            "Memory MCP v3 implements the subject/understanding model. "
            "Use orient at session start, remember for new observations, "
            "recall or bring_to_mind for retrieval, and Layer 1 tools when "
            "the higher-level verbs are insufficient."
        ),
        lifespan=lifespan,
    )

    mcp.add_tool(mcp_tools.orient)
    mcp.add_tool(mcp_tools.bring_to_mind)
    mcp.add_tool(mcp_tools.recall)
    mcp.add_tool(mcp_tools.reset_seen)
    mcp.add_tool(mcp_tools.set_session_model_tier)
    mcp.add_tool(mcp_tools.set_workspace_documents)
    mcp.add_tool(mcp_tools.remember)
    mcp.add_tool(mcp_tools.update_understanding)
    mcp.add_tool(mcp_tools.mark_useful)
    mcp.add_tool(mcp_tools.mark_questionable)
    mcp.add_tool(mcp_tools.create_subjects)
    mcp.add_tool(mcp_tools.get_subjects)
    mcp.add_tool(mcp_tools.set_subject_summary)
    mcp.add_tool(mcp_tools.set_subject_tags)
    mcp.add_tool(mcp_tools.set_structural_understanding)
    mcp.add_tool(mcp_tools.get_subjects_by_tag)
    mcp.add_tool(mcp_tools.add_observations)
    mcp.add_tool(mcp_tools.delete_observations)
    mcp.add_tool(mcp_tools.query_observations)
    mcp.add_tool(mcp_tools.create_understanding)
    mcp.add_tool(mcp_tools.get_understandings)
    mcp.add_tool(mcp_tools.get_understanding_history)
    mcp.add_tool(mcp_tools.search)
    mcp.add_tool(mcp_tools.open_intersection)
    mcp.add_tool(mcp_tools.open_around)
    mcp.add_tool(mcp_tools.get_consolidation_report)
    mcp.add_tool(mcp_tools.get_pending_consolidation)
    mcp.add_tool(mcp_tools.find_similar_subjects)
    mcp.add_tool(mcp_tools.merge_subjects)
    mcp.add_tool(mcp_tools.get_stats)
    mcp.add_tool(mcp_tools.get_status)
    return mcp
