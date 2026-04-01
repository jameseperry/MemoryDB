"""Tests for consolidation and stats tools."""

from memory_mcp.tools.nodes import create_entities, set_summary
from memory_mcp.tools.observations import add_observations
from memory_mcp.tools.relations import create_relations
from memory_mcp.tools.consolidation import (
    get_consolidation_report,
    get_pending_consolidation,
    get_stats,
)


async def test_get_stats_empty(ws):
    result = await get_stats(workspace=ws)
    assert result["node_count"] == 0
    assert result["observation_count"] == 0
    assert result["relation_count"] == 0
    assert result["embedding_coverage"] is None


async def test_get_stats_populated(ws):
    await create_entities([
        {"name": "a", "entity_type": "x", "observations": ["obs1"]},
        {"name": "b", "entity_type": "x", "observations": ["obs2"]},
    ], workspace=ws)
    await create_relations([{"from_entity": "a", "to_entity": "b", "relation_type": "r"}], workspace=ws)

    result = await get_stats(workspace=ws)
    assert result["node_count"] == 2
    assert result["observation_count"] == 2
    assert result["relation_count"] == 1
    assert result["embedding_coverage"] == 1.0


async def test_get_pending_consolidation_no_summary(ws):
    await create_entities([{"name": "unsummarized", "entity_type": "x", "observations": ["obs"]}], workspace=ws)

    result = await get_pending_consolidation(workspace=ws)
    names = {r["name"] for r in result}
    assert "unsummarized" in names


async def test_get_pending_consolidation_fresh_summary(ws):
    """A node whose summary was set after its last observation is NOT pending."""
    await create_entities([{"name": "n", "entity_type": "x", "observations": ["obs"]}], workspace=ws)
    await set_summary("n", "Up to date.", workspace=ws)

    result = await get_pending_consolidation(workspace=ws)
    names = {r["name"] for r in result}
    assert "n" not in names


async def test_get_pending_consolidation_stale_summary(ws):
    """A node with a summary that predates a new observation IS pending."""
    await create_entities([{"name": "n", "entity_type": "x", "observations": ["original"]}], workspace=ws)
    await set_summary("n", "Old summary.", workspace=ws)
    await add_observations([{"entity_name": "n", "contents": ["new observation"]}], workspace=ws)

    result = await get_pending_consolidation(workspace=ws)
    names = {r["name"] for r in result}
    assert "n" in names


async def test_consolidation_report_structure(ws):
    await create_entities([
        {"name": "orphan_node", "entity_type": "x", "observations": ["something"]},
        {"name": "linked_node", "entity_type": "x", "observations": ["something else"]},
    ], workspace=ws)
    await create_relations([
        {"from_entity": "orphan_node", "to_entity": "linked_node", "relation_type": "r"}
    ], workspace=ws)
    # Make orphan_node actually orphaned by removing its only relation
    from memory_mcp.tools.relations import delete_relations
    await delete_relations([
        {"from_entity": "orphan_node", "to_entity": "linked_node", "relation_type": "r"}
    ], workspace=ws)

    report = await get_consolidation_report(workspace=ws)

    assert "stale_summaries" in report
    assert "relation_candidates" in report
    assert "orphaned_nodes" in report
    assert "event_summary" in report

    orphan_names = {n["name"] for n in report["orphaned_nodes"]}
    assert "orphan_node" in orphan_names

    stale_names = {n["name"] for n in report["stale_summaries"]}
    assert "orphan_node" in stale_names
    assert "linked_node" in stale_names


async def test_stats_workspace_isolation(ws):
    """Stats for the test workspace don't include default workspace data."""
    await create_entities([{"name": "test_only", "entity_type": "x"}], workspace=ws)
    stats = await get_stats(workspace=ws)
    default_stats = await get_stats(workspace=None)

    # Test workspace has exactly what we put in
    assert stats["node_count"] == 1
    # Default workspace is independent (may be 0 or contain real data)
    assert "test_only" not in str(default_stats)
