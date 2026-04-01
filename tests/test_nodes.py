"""Tests for node CRUD tools."""

import pytest

from memory_mcp.tools.nodes import (
    create_entities,
    delete_entities,
    get_nodes_by_type,
    get_recently_modified,
    open_nodes,
    set_summary,
    set_tags,
)


async def test_create_and_open(ws):
    result = await create_entities([
        {"name": "alpha", "entity_type": "concept", "observations": ["obs one", "obs two"], "tags": ["a"]},
        {"name": "beta",  "entity_type": "concept"},
    ], workspace=ws)

    assert [r["name"] for r in result] == ["alpha", "beta"]

    opened = await open_nodes(["alpha", "beta", "missing"], workspace=ws)
    assert {e["name"] for e in opened["entities"]} == {"alpha", "beta"}
    assert opened["not_found"] == ["missing"]

    alpha = next(e for e in opened["entities"] if e["name"] == "alpha")
    assert len(alpha["observations"]) == 2
    assert alpha["observations"][0] == {"ordinal": 0, "content": "obs one"}
    assert alpha["observations"][1] == {"ordinal": 1, "content": "obs two"}
    assert alpha["tags"] == ["a"]


async def test_create_is_idempotent(ws):
    """Re-creating an existing node updates it rather than duplicating."""
    await create_entities([{"name": "node", "entity_type": "x", "observations": ["first"]}], workspace=ws)
    await create_entities([{"name": "node", "entity_type": "y", "observations": ["second"]}], workspace=ws)

    opened = await open_nodes(["node"], workspace=ws)
    node = opened["entities"][0]
    assert node["entity_type"] == "y"
    # Second create appends observations, not replaces
    assert len(node["observations"]) == 2


async def test_delete_entities(ws):
    await create_entities([{"name": "to_delete", "entity_type": "x"}], workspace=ws)
    result = await delete_entities(["to_delete", "nonexistent"], workspace=ws)
    assert result["deleted"] == ["to_delete"]
    assert result["not_found"] == ["nonexistent"]

    opened = await open_nodes(["to_delete"], workspace=ws)
    assert opened["not_found"] == ["to_delete"]


async def test_get_nodes_by_type(ws):
    await create_entities([
        {"name": "a", "entity_type": "fruit"},
        {"name": "b", "entity_type": "fruit"},
        {"name": "c", "entity_type": "vegetable"},
    ], workspace=ws)

    fruits = await get_nodes_by_type("fruit", workspace=ws)
    assert {r["name"] for r in fruits} == {"a", "b"}

    veg = await get_nodes_by_type("vegetable", workspace=ws)
    assert {r["name"] for r in veg} == {"c"}

    empty = await get_nodes_by_type("mineral", workspace=ws)
    assert empty == []


async def test_get_recently_modified(ws):
    await create_entities([{"name": "recent", "entity_type": "x"}], workspace=ws)
    result = await get_recently_modified(days=1, workspace=ws)
    assert any(r["name"] == "recent" for r in result)


async def test_set_summary(ws):
    await create_entities([{"name": "n", "entity_type": "x"}], workspace=ws)
    result = await set_summary("n", "A fine summary.", workspace=ws)
    assert result["summary"] == "A fine summary."

    opened = await open_nodes(["n"], workspace=ws)
    assert opened["entities"][0]["summary"] == "A fine summary."


async def test_set_summary_missing_node(ws):
    with pytest.raises(ValueError, match="not found"):
        await set_summary("ghost", "irrelevant", workspace=ws)


async def test_set_tags(ws):
    await create_entities([{"name": "n", "entity_type": "x"}], workspace=ws)
    result = await set_tags("n", ["x", "y", "z"], workspace=ws)
    assert set(result["tags"]) == {"x", "y", "z"}

    # Replace tags entirely
    result = await set_tags("n", ["only"], workspace=ws)
    assert result["tags"] == ["only"]


async def test_workspace_isolation(ws, other_ws):
    """Nodes in one workspace are invisible to a different workspace."""
    await create_entities([{"name": "secret", "entity_type": "x"}], workspace=ws)

    opened = await open_nodes(["secret"], workspace=other_ws)
    assert "secret" in opened["not_found"]
