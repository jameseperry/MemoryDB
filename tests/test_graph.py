"""Tests for graph traversal tools."""

from memory_mcp.tools.nodes import create_entities
from memory_mcp.tools.observations import add_observations
from memory_mcp.tools.relations import create_relations
from memory_mcp.tools.graph import (
    find_similar_nodes,
    get_neighborhood,
    get_orphans,
    get_path,
    get_relation_gaps,
)


async def _chain(ws):
    """Create a simple chain: a → b → c, plus an isolated orphan d."""
    await create_entities([
        {"name": "a", "entity_type": "x"},
        {"name": "b", "entity_type": "x"},
        {"name": "c", "entity_type": "x"},
        {"name": "d", "entity_type": "x"},   # orphan
    ], workspace=ws)
    await create_relations([
        {"from_entity": "a", "to_entity": "b", "relation_type": "next"},
        {"from_entity": "b", "to_entity": "c", "relation_type": "next"},
    ], workspace=ws)


async def test_neighborhood_depth_1(ws):
    await _chain(ws)
    result = await get_neighborhood("b", depth=1, workspace=ws)
    names = {n["name"] for n in result["nodes"]}
    # b plus its immediate neighbours a and c
    assert names == {"a", "b", "c"}


async def test_neighborhood_depth_0(ws):
    await _chain(ws)
    result = await get_neighborhood("a", depth=0, workspace=ws)
    assert {n["name"] for n in result["nodes"]} == {"a"}


async def test_neighborhood_depth_2(ws):
    await _chain(ws)
    result = await get_neighborhood("a", depth=2, workspace=ws)
    names = {n["name"] for n in result["nodes"]}
    assert names == {"a", "b", "c"}


async def test_get_path_direct(ws):
    await _chain(ws)
    result = await get_path("a", "b", workspace=ws)
    assert result["found"] is True
    assert result["path"] == ["a", "b"]


async def test_get_path_indirect(ws):
    await _chain(ws)
    result = await get_path("a", "c", workspace=ws)
    assert result["found"] is True
    assert result["path"] == ["a", "b", "c"]


async def test_get_path_no_path(ws):
    await _chain(ws)
    result = await get_path("a", "d", workspace=ws)
    assert result["found"] is False
    assert result["path"] == []


async def test_get_orphans(ws):
    await _chain(ws)
    result = await get_orphans(workspace=ws)
    names = {r["name"] for r in result}
    assert "d" in names
    assert "a" not in names
    assert "b" not in names


async def test_get_relation_gaps(ws):
    await create_entities([
        {"name": "project_alpha", "entity_type": "project"},
        {"name": "context_node",  "entity_type": "context"},
    ], workspace=ws)
    # context_node mentions project_alpha by name but has no formal relation
    await add_observations([
        {"entity_name": "context_node", "contents": ["This relates to project_alpha somehow"]},
    ], workspace=ws)

    result = await get_relation_gaps(workspace=ws)
    gaps = {(r["node"], r["referenced_name"]) for r in result}
    assert ("context_node", "project_alpha") in gaps


async def test_get_relation_gaps_suppressed_by_formal_relation(ws):
    """Once a formal relation exists, the gap should disappear."""
    await create_entities([
        {"name": "p", "entity_type": "project"},
        {"name": "q", "entity_type": "context"},
    ], workspace=ws)
    await add_observations([{"entity_name": "q", "contents": ["references p"]}], workspace=ws)
    await create_relations([{"from_entity": "q", "to_entity": "p", "relation_type": "references"}], workspace=ws)

    result = await get_relation_gaps(workspace=ws)
    gaps = {(r["node"], r["referenced_name"]) for r in result}
    assert ("q", "p") not in gaps


async def test_find_similar_nodes_requires_embeddings(ws):
    """find_similar_nodes returns an empty list when no node_embeddings exist yet
    (e.g., nodes created without observations). Confirms it doesn't error."""
    await create_entities([{"name": "x", "entity_type": "t"}, {"name": "y", "entity_type": "t"}], workspace=ws)
    result = await find_similar_nodes(workspace=ws)
    # No observations → no embeddings → no candidates
    assert isinstance(result, list)
