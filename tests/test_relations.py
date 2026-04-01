"""Tests for relation management tools."""

from memory_mcp.tools.nodes import create_entities
from memory_mcp.tools.relations import (
    create_relations,
    delete_relations,
    get_relations_between,
    update_relation_type,
)


async def _make_nodes(ws):
    await create_entities([
        {"name": "a", "entity_type": "x"},
        {"name": "b", "entity_type": "x"},
        {"name": "c", "entity_type": "x"},
    ], workspace=ws)


async def test_create_relations(ws):
    await _make_nodes(ws)
    result = await create_relations([
        {"from_entity": "a", "to_entity": "b", "relation_type": "knows"},
    ], workspace=ws)
    assert len(result["created"]) == 1
    assert result["already_existed"] == []
    assert result["not_found"] == []


async def test_create_relation_duplicate(ws):
    await _make_nodes(ws)
    await create_relations([{"from_entity": "a", "to_entity": "b", "relation_type": "knows"}], workspace=ws)
    result = await create_relations([{"from_entity": "a", "to_entity": "b", "relation_type": "knows"}], workspace=ws)
    assert result["created"] == []
    assert len(result["already_existed"]) == 1


async def test_create_relation_missing_node(ws):
    await _make_nodes(ws)
    result = await create_relations([
        {"from_entity": "a", "to_entity": "ghost", "relation_type": "knows"},
    ], workspace=ws)
    assert "ghost" in result["not_found"]
    assert result["created"] == []


async def test_get_relations_between_both_directions(ws):
    await _make_nodes(ws)
    await create_relations([
        {"from_entity": "a", "to_entity": "b", "relation_type": "knows"},
        {"from_entity": "b", "to_entity": "a", "relation_type": "trusts"},
    ], workspace=ws)

    result = await get_relations_between("a", "b", workspace=ws)
    types = {r["relation_type"] for r in result}
    assert types == {"knows", "trusts"}


async def test_delete_relations(ws):
    await _make_nodes(ws)
    await create_relations([{"from_entity": "a", "to_entity": "b", "relation_type": "knows"}], workspace=ws)

    result = await delete_relations([{"from_entity": "a", "to_entity": "b", "relation_type": "knows"}], workspace=ws)
    assert result["deleted"] == 1

    remaining = await get_relations_between("a", "b", workspace=ws)
    assert remaining == []


async def test_delete_relation_not_found(ws):
    await _make_nodes(ws)
    result = await delete_relations([{"from_entity": "a", "to_entity": "b", "relation_type": "ghost"}], workspace=ws)
    assert result["not_found"] == 1


async def test_update_relation_type(ws):
    await _make_nodes(ws)
    await create_relations([{"from_entity": "a", "to_entity": "b", "relation_type": "old"}], workspace=ws)

    result = await update_relation_type("a", "b", "old", "new", workspace=ws)
    assert result["new_type"] == "new"

    rels = await get_relations_between("a", "b", workspace=ws)
    assert rels[0]["relation_type"] == "new"
