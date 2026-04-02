"""MCP-facing wrappers for the v3 subject/understanding API.

The MCP surface resolves workspace and provenance session from transport
headers. These wrappers intentionally omit `workspace` and `session_id`
parameters from the exposed schema.
"""

from __future__ import annotations

import logging

from fastmcp.server.dependencies import get_context

from memory_v3.db import (
    resolve_effective_session_id,
    resolve_effective_workspace_name,
)
from memory_v3 import tools

logger = logging.getLogger(__name__)


def _log_tool_call(tool_name: str) -> None:
    """Log the active v3 MCP tool invocation with workspace/session context."""
    try:
        workspace = resolve_effective_workspace_name(None)
    except Exception:
        workspace = "<unresolved>"

    try:
        memory_session_id = resolve_effective_session_id()
    except Exception:
        memory_session_id = "<unresolved>"

    try:
        transport_session_id = get_context().session_id
    except Exception:
        transport_session_id = "<unresolved>"

    logger.info(
        "mcp_tool_call tool=%s workspace=%s session_id=%s transport_session_id=%s",
        tool_name,
        workspace,
        memory_session_id,
        transport_session_id,
    )


async def get_status() -> dict:
    """Return v3 server status."""
    _log_tool_call("get_status")
    try:
        stats = await tools.get_stats()
    except Exception as exc:
        return {
            "status": "starting",
            "api_version": "v3",
            "error": str(exc),
        }
    return {
        "status": "ready",
        "api_version": "v3",
        **stats,
    }


async def orient(model_tier: str | None = None) -> dict:
    _log_tool_call("orient")
    return await tools.orient(model_tier=model_tier)


async def bring_to_mind(
    topic_or_context: str,
    last_token: int | None = None,
    include_seen: bool = False,
) -> dict:
    _log_tool_call("bring_to_mind")
    return await tools.bring_to_mind(
        topic_or_context,
        last_token=last_token,
        include_seen=include_seen,
    )


async def recall(question_or_subject_name: str) -> dict:
    _log_tool_call("recall")
    return await tools.recall(question_or_subject_name)


async def reset_seen() -> dict:
    _log_tool_call("reset_seen")
    return await tools.reset_seen()


async def set_session_model_tier(model_tier: str | None = None) -> dict:
    _log_tool_call("set_session_model_tier")
    return await tools.set_session_model_tier(model_tier=model_tier)


async def set_workspace_documents(
    soul_understanding_id: int | None = None,
    protocol_understanding_id: int | None = None,
    orientation_understanding_id: int | None = None,
) -> dict:
    _log_tool_call("set_workspace_documents")
    return await tools.set_workspace_documents(
        soul_understanding_id=soul_understanding_id,
        protocol_understanding_id=protocol_understanding_id,
        orientation_understanding_id=orientation_understanding_id,
    )


async def remember(
    subject_names: list[str],
    content: str,
    kind: str | None = None,
    confidence: float | None = None,
    related_to: list[int] | None = None,
) -> dict:
    _log_tool_call("remember")
    return await tools.remember(
        subject_names,
        content,
        kind=kind,
        confidence=confidence,
        related_to=related_to,
    )


async def update_understanding(
    understanding_id: int,
    new_content: str,
    new_summary: str,
    subject_names: list[str] | None = None,
    reason: str | None = None,
) -> dict:
    _log_tool_call("update_understanding")
    return await tools.update_understanding(
        understanding_id,
        new_content,
        new_summary,
        subject_names=subject_names,
        reason=reason,
    )


async def mark_useful(id: int) -> dict:
    _log_tool_call("mark_useful")
    return await tools.mark_useful(id)


async def mark_questionable(id: int, reason: str | None = None) -> dict:
    _log_tool_call("mark_questionable")
    return await tools.mark_questionable(id, reason=reason)


async def create_subjects(subjects: list[dict]) -> list[dict]:
    _log_tool_call("create_subjects")
    return await tools.create_subjects(subjects)


async def get_subjects(names: list[str]) -> list[dict]:
    _log_tool_call("get_subjects")
    return await tools.get_subjects(names)


async def set_subject_summary(name: str, summary: str) -> dict:
    _log_tool_call("set_subject_summary")
    return await tools.set_subject_summary(name, summary)


async def set_subject_tags(name: str, tags: list[str]) -> dict:
    _log_tool_call("set_subject_tags")
    return await tools.set_subject_tags(name, tags)


async def set_structural_understanding(subject_name: str, content: str) -> dict:
    _log_tool_call("set_structural_understanding")
    return await tools.set_structural_understanding(subject_name, content)


async def get_subjects_by_tag(tag: str) -> list[dict]:
    _log_tool_call("get_subjects_by_tag")
    return await tools.get_subjects_by_tag(tag)


async def add_observations(observations: list[dict]) -> list[dict]:
    _log_tool_call("add_observations")
    return await tools.add_observations(observations)


async def delete_observations(ids: list[int]) -> dict:
    _log_tool_call("delete_observations")
    return await tools.delete_observations(ids)


async def query_observations(
    subject_names: list[str],
    query: str,
    mode: str = "text",
) -> list[dict]:
    _log_tool_call("query_observations")
    return await tools.query_observations(subject_names, query, mode=mode)


async def create_understanding(
    subject_names: list[str],
    content: str,
    summary: str,
    kind: str | None = None,
    source_observation_ids: list[int] | None = None,
    reason: str | None = None,
) -> dict:
    _log_tool_call("create_understanding")
    return await tools.create_understanding(
        subject_names,
        content,
        summary,
        kind=kind,
        source_observation_ids=source_observation_ids,
        reason=reason,
    )


async def get_understandings(subject_names: list[str]) -> list[dict]:
    _log_tool_call("get_understandings")
    return await tools.get_understandings(subject_names)


async def get_understanding_history(understanding_id: int) -> list[dict]:
    _log_tool_call("get_understanding_history")
    return await tools.get_understanding_history(understanding_id)


async def search(
    query: str,
    limit: int = 10,
    mode: str = "embedding",
) -> list[dict]:
    _log_tool_call("search")
    return await tools.search(query, limit=limit, mode=mode)


async def open_intersection(subject_a: str, subject_b: str) -> dict:
    _log_tool_call("open_intersection")
    return await tools.open_intersection(subject_a, subject_b)


async def open_around(subject_name: str) -> dict:
    _log_tool_call("open_around")
    return await tools.open_around(subject_name)


async def get_consolidation_report() -> dict:
    _log_tool_call("get_consolidation_report")
    return await tools.get_consolidation_report()


async def get_pending_consolidation() -> list[dict]:
    _log_tool_call("get_pending_consolidation")
    return await tools.get_pending_consolidation()


async def find_similar_subjects(
    limit: int = 20,
    min_score: float = 0.75,
) -> list[dict]:
    _log_tool_call("find_similar_subjects")
    return await tools.find_similar_subjects(limit=limit, min_score=min_score)


async def merge_subjects(primary: str, duplicate: str) -> dict:
    _log_tool_call("merge_subjects")
    return await tools.merge_subjects(primary, duplicate)


async def get_stats() -> dict:
    _log_tool_call("get_stats")
    return await tools.get_stats()
