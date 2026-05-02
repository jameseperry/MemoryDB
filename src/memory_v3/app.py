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
            "Memory MCP v3 implements a subject/observation/understanding memory model. "
            "Subjects are durable regions of aboutness. Observations are atomic evidence-like "
            "memory items. Understandings are synthesized, revisable summaries built over time. "
            "Use Layer 2 tools first: orient at session start, remember for new observations, "
            "bring_to_mind for associative surfacing of possibly relevant context, recall for "
            "directed retrieval, update_understanding when an existing synthesis should be "
            "superseded, and finalize_consolidation when a maintenance pass is complete. "
            "Use Layer 1 inspection tools when the higher-level verbs are not enough. "
            "The special documents have distinct roles: soul is durable stance and attractor, "
            "protocol is operating guidance for using memory well, orientation is current context, "
            "and consolidation is guidance for maintenance and synthesis passes. "
            "In normal interaction mode, orient returns soul, protocol, and orientation. "
            "In consolidation mode, orient returns soul, consolidation, orientation, "
            "and the latest consolidation log event."
        ),
        lifespan=lifespan,
    )

    mcp.add_tool(mcp_tools.orient)
    mcp.add_tool(mcp_tools.bring_to_mind)
    mcp.add_tool(mcp_tools.recall)
    mcp.add_tool(mcp_tools.reset_seen)
    mcp.add_tool(mcp_tools.set_session_model_tier)
    mcp.add_tool(mcp_tools.get_workspace_documents)
    mcp.add_tool(mcp_tools.get_named_understandings)
    mcp.add_tool(mcp_tools.set_workspace_documents)
    mcp.add_tool(mcp_tools.set_named_understanding)
    mcp.add_tool(mcp_tools.remember)
    mcp.add_tool(mcp_tools.update_understanding)
    mcp.add_tool(mcp_tools.finalize_consolidation)
    mcp.add_tool(mcp_tools.rewrite_understanding)
    mcp.add_tool(mcp_tools.delete_understanding)
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
    mcp.add_tool(mcp_tools.find_similar_subjects)
    mcp.add_tool(mcp_tools.merge_subjects)
    mcp.add_tool(mcp_tools.get_stats)
    mcp.add_tool(mcp_tools.get_status)

    # Session entity tools
    mcp.add_tool(mcp_tools.rejoin_session)
    mcp.add_tool(mcp_tools.merge_sessions)
    mcp.add_tool(mcp_tools.describe_session)
    mcp.add_tool(mcp_tools.what_happened)
    mcp.add_tool(mcp_tools.sessions)
    mcp.add_tool(mcp_tools.review_sessions)
    mcp.add_tool(mcp_tools.review_subjects)
    mcp.add_tool(mcp_tools.review_intersections)
    mcp.add_tool(mcp_tools.check_in)

    return mcp
