"""Tests for search tools.

Embedding-mode search requires the model to be loaded (slow on first call,
fast after). Text-mode search is instant. Both are tested.
"""

import pytest

from memory_mcp.tools.nodes import create_entities
from memory_mcp.tools.search import search_nodes


async def _populate(ws):
    await create_entities([
        {
            "name": "rust_project",
            "entity_type": "project",
            "observations": [
                "A systems programming project written in Rust.",
                "Uses the Tokio async runtime for concurrency.",
            ],
        },
        {
            "name": "python_project",
            "entity_type": "project",
            "observations": [
                "A data science project written in Python.",
                "Uses pandas and numpy for data manipulation.",
            ],
        },
        {
            "name": "cooking_notes",
            "entity_type": "note",
            "observations": [
                "Recipe for sourdough bread with long fermentation.",
            ],
        },
    ], workspace=ws)


async def test_search_text_mode(ws):
    await _populate(ws)
    results = await search_nodes("Tokio async runtime", mode="text", workspace=ws)
    assert len(results) >= 1
    assert results[0]["name"] == "rust_project"


async def test_search_text_mode_no_match(ws):
    await _populate(ws)
    results = await search_nodes("quantum entanglement", mode="text", workspace=ws)
    assert results == []


async def test_search_embedding_mode(ws):
    """Semantic search should rank rust_project above cooking_notes for a systems query."""
    await _populate(ws)
    results = await search_nodes("concurrent systems programming", mode="embedding", workspace=ws)
    assert len(results) >= 1
    names = [r["name"] for r in results]
    assert "rust_project" in names
    # rust_project should rank above cooking_notes
    if "cooking_notes" in names:
        assert names.index("rust_project") < names.index("cooking_notes")


async def test_search_embedding_returns_matched_perspective(ws):
    await _populate(ws)
    results = await search_nodes("programming language", mode="embedding", workspace=ws)
    for r in results:
        assert r["matched_perspective"] is not None


async def test_search_limit(ws):
    await _populate(ws)
    results = await search_nodes("project", mode="text", workspace=ws)
    assert len(results) <= 10

    results_1 = await search_nodes("project", mode="text", limit=1, workspace=ws)
    assert len(results_1) <= 1


async def test_search_workspace_isolation(ws, other_ws):
    """Results from one workspace do not bleed into another workspace."""
    await _populate(ws)
    results = await search_nodes("Tokio async runtime", mode="text", workspace=other_ws)
    names = [r["name"] for r in results]
    assert "rust_project" not in names
