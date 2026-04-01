"""Embedding service using nomic-embed-text via sentence-transformers.

nomic-embed-text-v1.5 uses instruction prefixes:
  - "search_document: ..." when indexing text
  - "search_query: ..."   when embedding a query

Our perspectives supply the semantic angle; the document/query prefix is
determined by call site (index vs. search).
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import TYPE_CHECKING

import asyncpg

logger = logging.getLogger(__name__)

_model = None  # SentenceTransformer instance, loaded lazily at first use


def get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("Loading nomic-embed-text-v1.5...")
        _model = SentenceTransformer(
            "nomic-ai/nomic-embed-text-v1.5",
            trust_remote_code=True,
        )
        logger.info("Model loaded.")
    return _model


def _prepend(instruction: str, texts: list[str]) -> list[str]:
    """Prepend instruction prefix to each text."""
    return [f"{instruction} {t}" for t in texts]


def embed_documents(texts: list[str], instruction: str) -> list[list[float]]:
    """Embed a batch of documents for indexing.

    Prepends 'search_document: <perspective_instruction> <text>'.
    Returns list of 768-dim float vectors.
    """
    model = get_model()
    prefixed = _prepend(f"search_document: {instruction}", texts)
    vectors = model.encode(prefixed, normalize_embeddings=True)
    return vectors.tolist()


def embed_query(text: str, instruction: str) -> list[float]:
    """Embed a single query for retrieval.

    Prepends 'search_query: <perspective_instruction> <text>'.
    Returns a single 768-dim float vector.
    """
    model = get_model()
    prefixed = f"search_query: {instruction} {text}"
    vector = model.encode([prefixed], normalize_embeddings=True)
    return vector[0].tolist()


async def get_perspectives(conn: asyncpg.Connection, workspace_id: int | None) -> list[dict]:
    """Return all perspectives for a workspace (falls back to global defaults)."""
    rows = await conn.fetch(
        """
        SELECT id, name, instruction
        FROM perspectives
        WHERE workspace_id IS NOT DISTINCT FROM $1
           OR workspace_id IS NULL
        ORDER BY workspace_id NULLS LAST, name
        """,
        workspace_id,
    )
    # If workspace has its own perspectives, prefer those over global defaults.
    # Deduplicate by name, workspace-specific wins.
    seen: dict[str, dict] = {}
    for row in rows:
        name = row["name"]
        if name not in seen or row["workspace_id"] is not None:
            seen[name] = {"id": row["id"], "name": name, "instruction": row["instruction"]}
    return list(seen.values())


async def embed_observations(
    conn: asyncpg.Connection,
    node_id: int,
    workspace_id: int | None,
    observation_ids: list[int],
) -> None:
    """Compute and store embeddings for a set of observations, then update node aggregate.

    Runs in a thread pool to avoid blocking the event loop during model inference.
    """
    if not observation_ids:
        return

    perspectives = await get_perspectives(conn, workspace_id)
    if not perspectives:
        return

    obs_rows = await conn.fetch(
        "SELECT id, content FROM observations WHERE id = ANY($1) ORDER BY ordinal",
        observation_ids,
    )
    if not obs_rows:
        return

    obs_ids = [r["id"] for r in obs_rows]
    obs_texts = [r["content"] for r in obs_rows]

    loop = asyncio.get_event_loop()

    for perspective in perspectives:
        vectors = await loop.run_in_executor(
            None, embed_documents, obs_texts, perspective["instruction"]
        )
        await conn.executemany(
            """
            INSERT INTO embeddings (observation_id, perspective_id, vector)
            VALUES ($1, $2, $3::vector)
            ON CONFLICT (observation_id, perspective_id)
                DO UPDATE SET vector = EXCLUDED.vector, embedded_at = NOW()
            """,
            [
                (oid, perspective["id"], str(vec))
                for oid, vec in zip(obs_ids, vectors)
            ],
        )

    await _update_node_embedding(conn, node_id, workspace_id, perspectives)


async def _update_node_embedding(
    conn: asyncpg.Connection,
    node_id: int,
    workspace_id: int | None,
    perspectives: list[dict],
) -> None:
    """Recompute the mean-pooled node-level embedding for each perspective."""
    for perspective in perspectives:
        # Mean-pool all observation embeddings for this node+perspective.
        # pgvector supports avg() over vectors.
        row = await conn.fetchrow(
            """
            SELECT avg(e.vector)::vector AS mean_vec
            FROM embeddings e
            JOIN observations o ON o.id = e.observation_id
            WHERE o.node_id = $1 AND e.perspective_id = $2
            """,
            node_id, perspective["id"],
        )
        if row is None or row["mean_vec"] is None:
            continue

        await conn.execute(
            """
            INSERT INTO node_embeddings (node_id, perspective_id, vector)
            VALUES ($1, $2, $3::vector)
            ON CONFLICT (node_id, perspective_id)
                DO UPDATE SET vector = EXCLUDED.vector, updated_at = NOW()
            """,
            node_id, perspective["id"], row["mean_vec"],
        )


async def delete_observation_embeddings(
    conn: asyncpg.Connection,
    node_id: int,
    workspace_id: int | None,
    observation_ids: list[int],
) -> None:
    """Remove embeddings for deleted observations, then refresh node aggregate."""
    await conn.execute(
        "DELETE FROM embeddings WHERE observation_id = ANY($1)",
        observation_ids,
    )
    perspectives = await get_perspectives(conn, workspace_id)
    await _update_node_embedding(conn, node_id, workspace_id, perspectives)
