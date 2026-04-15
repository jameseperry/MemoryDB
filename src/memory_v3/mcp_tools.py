"""MCP-facing wrappers for the v3 subject/understanding API.

The MCP surface resolves workspace and provenance session from transport
headers. These wrappers intentionally omit `workspace` and `session_id`
parameters from the exposed schema.
"""

from __future__ import annotations

import logging
from typing import Literal

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


async def orient(
    model_tier: str | None = None,
    mode: Literal["interaction", "consolidation"] = "interaction",
) -> dict:
    """Load the workspace's special documents and reset the session seen-set.

    Use this at session start.

    Interaction mode returns:
    - `soul`: durable stance / attractor document
    - `protocol`: operating rules for using the memory system
    - `orientation`: current task and context, if present

    Consolidation mode returns:
    - `soul`
    - `consolidation`: guidance for maintenance / synthesis passes
    - `orientation`

    This call also:
    - records the active `model_tier` for session provenance when provided
    - clears the `surfaced_in_session` set, so retrieval starts fresh
    - returns `pending_consolidation_count` and `recent_activity`

    Args:
        model_tier: Optional model identifier to attach to new writes in this session.
        mode: `interaction` for normal live work, `consolidation` for maintenance passes.
    """
    _log_tool_call("orient")
    return await tools.orient(model_tier=model_tier, mode=mode)


async def bring_to_mind(
    topic_or_context: str,
    last_token: int | None = None,
    include_seen: bool = False,
) -> dict:
    """Surface ambient memory that may matter even if you do not know what to ask for.

    This is the associative retrieval verb.

    Use it proactively:
    - at topic shifts
    - before consequential design / code changes
    - when a person, project, or concept likely has prior history
    - when you suspect there is relevant context outside the current window

    Best prompt style:
    - broad but anchored
    - describe the current topic, decision, or context
    - do not phrase it as a narrow factual question unless that is truly what you need

    The server maintains a per-session seen-set so repeated calls do not keep returning
    the same items. `last_token` is the continuity token returned by the previous call;
    if it is missing or stale, the server may detect compaction / discontinuity and reset
    the seen-set. `include_seen=True` bypasses that filter.

    Returns:
    - `results`: suggested memory items with summaries and relevance scores
    - `heartbeat_token`: pass this as `last_token` on the next call
    - `compaction_detected`: whether the server reset the seen-set
    - `usage_hint`: reminder that surfaced items are candidates, not truth
    """
    _log_tool_call("bring_to_mind")
    return await tools.bring_to_mind(
        topic_or_context,
        last_token=last_token,
        include_seen=include_seen,
    )


async def recall(question_or_subject_name: str) -> dict:
    """Do directed retrieval for either a subject name or a natural-language question.

    If the input exactly matches a subject in the current workspace, this returns a
    subject-centered bundle:
    - subject metadata
    - active single-subject understanding, if any
    - structural understanding, if any
    - recent observations for that subject

    Otherwise it treats the input as a question and returns a best-answer view built
    from search:
    - `best_answer`
    - `supporting`
    - `provenance`

    Use this when you know what you are trying to answer. Use `bring_to_mind` when the
    problem is that you may not know what prior context exists.
    """
    _log_tool_call("recall")
    return await tools.recall(question_or_subject_name)


async def reset_seen() -> dict:
    """Clear the current session's surfaced-memory seen-set."""
    _log_tool_call("reset_seen")
    return await tools.reset_seen()


async def set_session_model_tier(model_tier: str | None = None) -> dict:
    """Set or clear the model tier associated with the active session."""
    _log_tool_call("set_session_model_tier")
    return await tools.set_session_model_tier(model_tier=model_tier)


async def get_workspace_documents() -> dict:
    """Return the current workspace document pointers by understanding ID."""
    _log_tool_call("get_workspace_documents")
    return await tools.get_workspace_documents()


async def get_named_understandings(names: list[str] | None = None) -> dict:
    """Return named understanding IDs for the current workspace.

    Use this to resolve stable document-like names to active understanding IDs. If
    `names` is omitted, all names in the current workspace are returned. If `names`
    is provided, the result includes those exact names with `null` for any missing
    mapping.
    """
    _log_tool_call("get_named_understandings")
    return await tools.get_named_understandings(names=names)


async def set_workspace_documents(
    soul_understanding_id: int | None = None,
    protocol_understanding_id: int | None = None,
    orientation_understanding_id: int | None = None,
    consolidation_understanding_id: int | None = None,
) -> dict:
    """Set one or more workspace special-document pointers.

    This is mainly for bootstrapping or repair. Each provided ID must reference an active
    understanding in the current workspace.
    """
    _log_tool_call("set_workspace_documents")
    return await tools.set_workspace_documents(
        soul_understanding_id=soul_understanding_id,
        protocol_understanding_id=protocol_understanding_id,
        orientation_understanding_id=orientation_understanding_id,
        consolidation_understanding_id=consolidation_understanding_id,
    )


async def set_named_understanding(
    name: str,
    understanding_id: int | None = None,
) -> dict:
    """Assign or clear a stable name for an active understanding.

    This generalizes the magic workspace documents. The special names `soul`,
    `protocol`, `orientation`, and `consolidation` are also maintained here, and
    `orient()` resolves them through this naming layer.

    Passing `understanding_id=null` clears the name.
    """
    _log_tool_call("set_named_understanding")
    return await tools.set_named_understanding(
        name=name,
        understanding_id=understanding_id,
    )


async def remember(
    subject_names: list[str],
    content: str,
    kind: str | None = None,
    confidence: float | None = None,
    related_to: list[int] | None = None,
    points_to: list[int] | None = None,
) -> dict:
    """Write one observation into memory.

    This is the main ergonomic write tool for live conversation.

    Use it for:
    - factual observations that are likely to matter later
    - user preferences, project facts, design decisions, corrections, and open questions
    - small, specific, evidence-like statements

    Prefer:
    - atomic observations over multi-claim blobs
    - accurate subject tagging over broad tagging
    - writing during the conversation, not batching everything at the end

    Args:
        subject_names: Subjects this observation is genuinely about.
        content: The observation text.
        kind: Optional observation category such as `fact`, `preference`, `reflection`.
        confidence: Optional confidence score if you have a reason to supply one.
        related_to: Optional active understanding IDs this observation directly supports.
            Use this strictly for evidential dependence, not vague relevance.
        points_to: Optional observation IDs this observation elaborates on or points at.
            Use this for light observation-to-observation threading where the relationship
            itself is expressed in the new observation's text.

    Returns the created or deduplicated observation, including its ID and any created
    subjects.
    """
    _log_tool_call("remember")
    return await tools.remember(
        subject_names,
        content,
        kind=kind,
        confidence=confidence,
        related_to=related_to,
        points_to=points_to,
    )


async def update_understanding(
    understanding_id: int,
    new_content: str,
    new_summary: str,
    subject_names: list[str] | None = None,
    reason: str | None = None,
) -> dict:
    """Revise an existing understanding by superseding it with a new version.

    Use this when an existing understanding is still the right conceptual object but
    needs to be rewritten because:
    - new observations changed the conclusion
    - the synthesis is stale
    - the summary is wrong or incomplete
    - the understanding should keep its role (`single_subject`, `relationship`, `soul`,
      `protocol`, `orientation`, `consolidation`, etc.) but its content should change

    Important behavior:
    - the old understanding is not edited in place
    - a new understanding row is created
    - the old row's `superseded_by` pointer is updated
    - any special workspace pointer or subject pointer is moved to the new row
    - updating a superseded understanding is rejected; update the active head instead

    Args:
        understanding_id: The active understanding to supersede.
        new_content: Full replacement text.
        new_summary: Replacement short summary.
        subject_names: Optional replacement subject set. If omitted, keeps existing subjects.
        reason: Optional explanation of why this revision was made.
    """
    _log_tool_call("update_understanding")
    return await tools.update_understanding(
        understanding_id,
        new_content,
        new_summary,
        subject_names=subject_names,
        reason=reason,
    )


async def finalize_consolidation(
    expected_generation: int,
    summary: str,
    updated_understanding_ids: list[int] | None = None,
    created_understanding_ids: list[int] | None = None,
    reviewed_subject_names: list[str] | None = None,
) -> dict:
    """Finalize a consolidation pass and advance the workspace generation.

    Use this at the end of a maintenance pass after writing any new or updated
    understandings for the current generation. The call is optimistic-concurrency
    guarded by `expected_generation`, so it fails cleanly if another pass already
    advanced the workspace.
    """
    _log_tool_call("finalize_consolidation")
    return await tools.finalize_consolidation(
        expected_generation,
        summary,
        updated_understanding_ids=updated_understanding_ids,
        created_understanding_ids=created_understanding_ids,
        reviewed_subject_names=reviewed_subject_names,
    )


async def rewrite_understanding(
    understanding_id: int,
    new_content: str,
    new_summary: str,
) -> dict:
    """Rewrite an understanding in place.

    Use this only for same-session correction or iterative drafting before the
    understanding has been carried forward into a later consolidation generation.
    Unlike `update_understanding`, this preserves the same understanding ID.
    """
    _log_tool_call("rewrite_understanding")
    return await tools.rewrite_understanding(
        understanding_id,
        new_content,
        new_summary,
    )


async def delete_understanding(understanding_id: int) -> dict:
    """Delete an understanding written in the current session and generation."""
    _log_tool_call("delete_understanding")
    return await tools.delete_understanding(understanding_id)


async def mark_useful(id: int) -> dict:
    """Attach a `useful` signal to an active observation or understanding."""
    _log_tool_call("mark_useful")
    return await tools.mark_useful(id)


async def mark_questionable(id: int, reason: str | None = None) -> dict:
    """Attach a `questionable` signal to an active observation or understanding."""
    _log_tool_call("mark_questionable")
    return await tools.mark_questionable(id, reason=reason)


async def create_subjects(subjects: list[dict]) -> list[dict]:
    """Create one or more named subjects."""
    _log_tool_call("create_subjects")
    return await tools.create_subjects(subjects)


async def get_subjects(names: list[str]) -> list[dict]:
    """Return full subject records for the requested subject names."""
    _log_tool_call("get_subjects")
    return await tools.get_subjects(names)


async def set_subject_summary(name: str, summary: str) -> dict:
    """Replace a subject's summary text."""
    _log_tool_call("set_subject_summary")
    return await tools.set_subject_summary(name, summary)


async def set_subject_tags(name: str, tags: list[str]) -> dict:
    """Replace a subject's tag list."""
    _log_tool_call("set_subject_tags")
    return await tools.set_subject_tags(name, tags)


async def set_structural_understanding(subject_name: str, content: str) -> dict:
    """Create or replace a subject's structural understanding."""
    _log_tool_call("set_structural_understanding")
    return await tools.set_structural_understanding(subject_name, content)


async def get_subjects_by_tag(tag: str) -> list[dict]:
    """List subjects carrying a given tag."""
    _log_tool_call("get_subjects_by_tag")
    return await tools.get_subjects_by_tag(tag)


async def add_observations(observations: list[dict]) -> list[dict]:
    """Batch-write observations.

    Prefer `remember` for normal live use. Use this when you already have a batch of
    observation objects to insert together.
    """
    _log_tool_call("add_observations")
    return await tools.add_observations(observations)


async def delete_observations(ids: list[int]) -> dict:
    """Delete observations written in the current session."""
    _log_tool_call("delete_observations")
    return await tools.delete_observations(ids)


async def query_observations(
    subject_names: list[str],
    query: str,
    mode: str = "text",
) -> list[dict]:
    """Search observations tagged with all provided subjects.

    Use this when you know the subject scope you want and you need raw observation
    evidence rather than synthesized understandings.
    """
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
    """Create a new understanding from scratch."""
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
    """Return active understandings tagged with all provided subjects."""
    _log_tool_call("get_understandings")
    return await tools.get_understandings(subject_names)


async def get_understanding_history(understanding_id: int) -> list[dict]:
    """Return the full supersession history connected to an understanding."""
    _log_tool_call("get_understanding_history")
    return await tools.get_understanding_history(understanding_id)


async def search(
    query: str,
    limit: int = 10,
    mode: str = "embedding",
) -> list[dict]:
    """Search across observations and understandings."""
    _log_tool_call("search")
    return await tools.search(query, limit=limit, mode=mode)


async def open_intersection(subject_a: str, subject_b: str) -> dict:
    """Open the full active overlap between two subjects."""
    _log_tool_call("open_intersection")
    return await tools.open_intersection(subject_a, subject_b)


async def open_around(subject_name: str) -> dict:
    """Return a subject's neighborhood ordered by overlap strength."""
    _log_tool_call("open_around")
    return await tools.open_around(subject_name)


async def get_consolidation_report() -> dict:
    """Return detailed signals about consolidation opportunities and staleness."""
    _log_tool_call("get_consolidation_report")
    return await tools.get_consolidation_report()


async def get_pending_consolidation() -> list[dict]:
    """Return a flattened queue-like view of pending consolidation work."""
    _log_tool_call("get_pending_consolidation")
    return await tools.get_pending_consolidation()


async def find_similar_subjects(
    limit: int = 20,
    min_score: float = 0.75,
) -> list[dict]:
    """Find subjects that look semantically similar and may warrant review."""
    _log_tool_call("find_similar_subjects")
    return await tools.find_similar_subjects(limit=limit, min_score=min_score)


async def merge_subjects(primary: str, duplicate: str) -> dict:
    """Merge one subject into another."""
    _log_tool_call("merge_subjects")
    return await tools.merge_subjects(primary, duplicate)


async def get_stats() -> dict:
    """Return workspace-level counts and embedding coverage statistics."""
    _log_tool_call("get_stats")
    return await tools.get_stats()
