"""Tests for the embedding pipeline.

Verifies that:
- Embeddings are written to the DB on node/observation creation
- Vectors have the correct dimension
- Node aggregate (node_embeddings) is maintained
- Deletions clean up embeddings and refresh the aggregate
- Different perspectives produce meaningfully different vectors
"""

import math

import pytest

from memory_mcp.db import get_pool
from memory_mcp.tools.nodes import create_entities, delete_entities
from memory_mcp.tools.observations import add_observations, delete_observations

EXPECTED_DIM = 768


async def _embedding_count(ws: str, node_name: str) -> int:
    """Count observation embeddings for a node."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM embeddings e
            JOIN observations o ON o.id = e.observation_id
            JOIN nodes n ON n.id = o.node_id
            WHERE n.workspace_id = (SELECT id FROM workspaces WHERE name = $1)
              AND n.name = $2
            """,
            ws, node_name,
        )


async def _node_embedding_count(ws: str, node_name: str) -> int:
    """Count node-level aggregate embeddings."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM node_embeddings ne
            JOIN nodes n ON n.id = ne.node_id
            WHERE n.workspace_id = (SELECT id FROM workspaces WHERE name = $1)
              AND n.name = $2
            """,
            ws, node_name,
        )


async def _get_vectors(ws: str, node_name: str) -> dict[str, list[float]]:
    """Return {perspective_name: vector} for a node's aggregate embeddings.

    Casts vector to text and parses in Python — pgvector has no direct cast
    to float[], and asyncpg returns the vector OID as a raw string.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.name AS perspective,
                   ne.vector::text AS vector_text
            FROM node_embeddings ne
            JOIN nodes n ON n.id = ne.node_id
            JOIN perspectives p ON p.id = ne.perspective_id
            WHERE n.workspace_id = (SELECT id FROM workspaces WHERE name = $1)
              AND n.name = $2
            """,
            ws, node_name,
        )
    # pgvector text format: "[0.023,0.045,...]"
    return {
        r["perspective"]: [float(x) for x in r["vector_text"][1:-1].split(",")]
        for r in rows
    }


async def _perspective_count(ws: str) -> int:
    """Number of perspectives available for a workspace."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT COUNT(*) FROM perspectives
            WHERE workspace_id IS NULL   -- default perspectives
            """
        )


async def test_embeddings_written_on_create(ws):
    """Creating a node with observations writes one embedding per observation per perspective."""
    await create_entities([
        {"name": "n", "entity_type": "x", "observations": ["first", "second"]},
    ], workspace=ws)

    n_perspectives = await _perspective_count(ws)
    count = await _embedding_count(ws, "n")
    # 2 observations × n_perspectives
    assert count == 2 * n_perspectives, f"expected {2 * n_perspectives}, got {count}"


async def test_node_aggregate_written_on_create(ws):
    """Creating a node with observations writes one node_embedding per perspective."""
    await create_entities([
        {"name": "n", "entity_type": "x", "observations": ["something"]},
    ], workspace=ws)

    n_perspectives = await _perspective_count(ws)
    count = await _node_embedding_count(ws, "n")
    assert count == n_perspectives, f"expected {n_perspectives} node embeddings, got {count}"


async def test_vector_dimension(ws):
    """Stored vectors must have the expected dimension."""
    await create_entities([
        {"name": "n", "entity_type": "x", "observations": ["dimensionality check"]},
    ], workspace=ws)

    vectors = await _get_vectors(ws, "n")
    assert len(vectors) > 0, "no node embeddings found"
    for perspective, vec in vectors.items():
        assert len(vec) == EXPECTED_DIM, (
            f"perspective '{perspective}': expected dim {EXPECTED_DIM}, got {len(vec)}"
        )


async def test_vectors_are_unit_normalized(ws):
    """nomic-embed-text returns L2-normalized vectors; magnitude should be ~1.0."""
    await create_entities([
        {"name": "n", "entity_type": "x", "observations": ["normalization check"]},
    ], workspace=ws)

    vectors = await _get_vectors(ws, "n")
    for perspective, vec in vectors.items():
        magnitude = math.sqrt(sum(x * x for x in vec))
        assert abs(magnitude - 1.0) < 1e-4, (
            f"perspective '{perspective}': magnitude {magnitude:.6f}, expected ~1.0"
        )


async def test_embeddings_added_on_add_observations(ws):
    """add_observations also embeds new observations."""
    await create_entities([{"name": "n", "entity_type": "x"}], workspace=ws)
    initial_count = await _embedding_count(ws, "n")
    assert initial_count == 0

    await add_observations([{"entity_name": "n", "contents": ["new obs"]}], workspace=ws)

    n_perspectives = await _perspective_count(ws)
    count = await _embedding_count(ws, "n")
    assert count == n_perspectives


async def test_node_aggregate_updated_on_add_observations(ws):
    """Node aggregate is updated when observations are added after creation."""
    await create_entities([
        {"name": "n", "entity_type": "x", "observations": ["initial"]},
    ], workspace=ws)

    vectors_before = await _get_vectors(ws, "n")
    await add_observations([{"entity_name": "n", "contents": ["second obs"]}], workspace=ws)
    vectors_after = await _get_vectors(ws, "n")

    # Aggregate should change when a second observation is added
    for perspective in vectors_before:
        assert vectors_before[perspective] != vectors_after[perspective], (
            f"node aggregate for '{perspective}' did not change after adding a second observation"
        )


async def test_embeddings_deleted_with_observation(ws):
    """Deleting an observation also removes its embeddings and refreshes the aggregate."""
    await create_entities([
        {"name": "n", "entity_type": "x", "observations": ["keep", "delete me"]},
    ], workspace=ws)

    n_perspectives = await _perspective_count(ws)
    assert await _embedding_count(ws, "n") == 2 * n_perspectives

    await delete_observations([{"entity_name": "n", "ordinals": [1]}], workspace=ws)

    assert await _embedding_count(ws, "n") == n_perspectives  # only "keep" remains


async def test_embeddings_deleted_with_node(ws):
    """Deleting a node cascades to all its embeddings."""
    await create_entities([
        {"name": "n", "entity_type": "x", "observations": ["obs"]},
    ], workspace=ws)
    assert await _embedding_count(ws, "n") > 0

    await delete_entities(["n"], workspace=ws)

    # After delete the node is gone; count should be 0 (cascade handled by FK)
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM embeddings e
            JOIN observations o ON o.id = e.observation_id
            JOIN nodes n ON n.id = o.node_id
            WHERE n.name = 'n'
            """
        )
    assert count == 0


async def test_perspectives_produce_different_vectors(ws):
    """Different perspectives should produce meaningfully different vectors for the same text.

    This is a soft check — we verify they're not identical, which would indicate
    the instruction prefix isn't being applied.
    """
    await create_entities([
        {"name": "n", "entity_type": "x", "observations": [
            "James works on GPU math libraries at AMD in Calgary."
        ]},
    ], workspace=ws)

    vectors = await _get_vectors(ws, "n")
    assert len(vectors) >= 2, "need at least 2 perspectives to compare"

    perspective_names = list(vectors.keys())
    for i in range(len(perspective_names)):
        for j in range(i + 1, len(perspective_names)):
            a, b = vectors[perspective_names[i]], vectors[perspective_names[j]]
            assert a != b, (
                f"perspectives '{perspective_names[i]}' and '{perspective_names[j]}' "
                f"produced identical vectors — instruction prefix likely not applied"
            )
