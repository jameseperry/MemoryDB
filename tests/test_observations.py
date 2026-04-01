"""Tests for observation management tools."""

import pytest

from memory_mcp.tools.nodes import create_entities
from memory_mcp.tools.observations import (
    add_observations,
    delete_observations,
    query_observations,
    replace_observation,
)


async def test_add_observations_ordinals(ws):
    """Ordinals are assigned sequentially and gap-free from the current max."""
    await create_entities([{"name": "n", "entity_type": "x", "observations": ["a", "b"]}], workspace=ws)

    result = await add_observations([
        {"entity_name": "n", "contents": ["c", "d"]},
    ], workspace=ws)

    added = result[0]["added"]
    assert added[0] == {"ordinal": 2, "content": "c"}
    assert added[1] == {"ordinal": 3, "content": "d"}


async def test_add_observations_missing_node(ws):
    result = await add_observations([
        {"entity_name": "ghost", "contents": ["irrelevant"]},
    ], workspace=ws)
    assert result[0]["not_found"] is True
    assert result[0]["added"] == []


async def test_replace_observation(ws):
    await create_entities([{"name": "n", "entity_type": "x", "observations": ["original"]}], workspace=ws)

    result = await replace_observation("n", ordinal=0, new_content="replaced", workspace=ws)
    assert result["ordinal"] == 0
    assert result["new_content"] == "replaced"

    # Verify in DB
    from memory_mcp.tools.nodes import open_nodes
    opened = await open_nodes(["n"], workspace=ws)
    assert opened["entities"][0]["observations"][0]["content"] == "replaced"


async def test_replace_observation_bad_ordinal(ws):
    await create_entities([{"name": "n", "entity_type": "x", "observations": ["x"]}], workspace=ws)
    with pytest.raises(ValueError, match="not found"):
        await replace_observation("n", ordinal=99, new_content="y", workspace=ws)


async def test_delete_observations_stable_ordinals(ws):
    """Deleting observations does not renumber remaining ones."""
    await create_entities([
        {"name": "n", "entity_type": "x", "observations": ["a", "b", "c", "d"]},
    ], workspace=ws)

    result = await delete_observations([{"entity_name": "n", "ordinals": [1, 3]}], workspace=ws)
    assert sorted(result[0]["deleted_ordinals"]) == [1, 3]
    assert result[0]["not_found_ordinals"] == []

    from memory_mcp.tools.nodes import open_nodes
    opened = await open_nodes(["n"], workspace=ws)
    remaining = opened["entities"][0]["observations"]
    assert [o["ordinal"] for o in remaining] == [0, 2]
    assert [o["content"] for o in remaining] == ["a", "c"]


async def test_delete_observations_not_found_ordinals(ws):
    await create_entities([{"name": "n", "entity_type": "x", "observations": ["a"]}], workspace=ws)
    result = await delete_observations([{"entity_name": "n", "ordinals": [0, 99]}], workspace=ws)
    assert result[0]["deleted_ordinals"] == [0]
    assert result[0]["not_found_ordinals"] == [99]


async def test_query_observations_text_mode(ws):
    await create_entities([
        {"name": "n", "entity_type": "x", "observations": [
            "the quick brown fox",
            "jumped over the lazy dog",
            "completely unrelated content about databases",
        ]},
    ], workspace=ws)

    results = await query_observations("n", "fox", mode="text", workspace=ws)
    assert len(results) >= 1
    assert any("fox" in r["content"] for r in results)


async def test_query_observations_embedding_mode(ws):
    await create_entities([
        {"name": "n", "entity_type": "x", "observations": [
            "Rust is a systems programming language focused on safety and performance.",
            "Python is popular for data science and machine learning.",
            "Sourdough bread requires a live fermentation culture.",
        ]},
    ], workspace=ws)

    results = await query_observations("n", "low-level memory safe programming", mode="embedding", workspace=ws)
    assert len(results) >= 1
    # Rust observation should rank above sourdough
    contents = [r["content"] for r in results]
    assert any("Rust" in c for c in contents)
    if any("Sourdough" in c for c in contents):
        rust_idx = next(i for i, c in enumerate(contents) if "Rust" in c)
        bread_idx = next(i for i, c in enumerate(contents) if "Sourdough" in c)
        assert rust_idx < bread_idx
