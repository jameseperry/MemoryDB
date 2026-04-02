"""Non-DB tests for the v3 MCP wrapper layer."""

import inspect
import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from memory_v3 import mcp_tools
from memory_v3.config import settings
from memory_v3.db import (
    record_event,
    resolve_effective_session_id,
    resolve_effective_workspace_name,
)
from memory_v3 import tools as tools_module


@pytest_asyncio.fixture(scope="session")
async def db_pool():
    """Override the global DB fixture: these tests do not touch Postgres."""
    yield


@pytest_asyncio.fixture(autouse=True)
async def isolated_workspace():
    """Override the global isolation fixture: these tests do not touch Postgres."""
    yield


def test_v3_resolve_effective_workspace_uses_header(monkeypatch):
    monkeypatch.setattr(
        "memory_v3.db.get_http_headers",
        lambda: {settings.mcp_workspace_header.lower(): "james/gpt"},
    )
    assert resolve_effective_workspace_name(None) == "james/gpt"


def test_v3_resolve_effective_session_uses_header(monkeypatch):
    monkeypatch.setattr(
        "memory_v3.db.get_http_headers",
        lambda: {settings.mcp_session_header.lower(): "conversation-42"},
    )
    assert resolve_effective_session_id() == "conversation-42"


def test_v3_resolve_effective_session_rejects_mismatch(monkeypatch):
    monkeypatch.setattr(
        "memory_v3.db.get_http_headers",
        lambda: {settings.mcp_session_header.lower(): "conversation-42"},
    )
    with pytest.raises(ValueError, match="does not match"):
        resolve_effective_session_id("other-session")


def test_v3_wrappers_do_not_expose_workspace_or_session():
    wrappers = [
        mcp_tools.get_status,
        mcp_tools.orient,
        mcp_tools.bring_to_mind,
        mcp_tools.recall,
        mcp_tools.reset_seen,
        mcp_tools.set_session_model_tier,
        mcp_tools.set_workspace_documents,
        mcp_tools.remember,
        mcp_tools.update_understanding,
        mcp_tools.mark_useful,
        mcp_tools.mark_questionable,
        mcp_tools.create_subjects,
        mcp_tools.get_subjects,
        mcp_tools.set_subject_summary,
        mcp_tools.set_subject_tags,
        mcp_tools.set_structural_understanding,
        mcp_tools.get_subjects_by_tag,
        mcp_tools.add_observations,
        mcp_tools.delete_observations,
        mcp_tools.query_observations,
        mcp_tools.create_understanding,
        mcp_tools.get_understandings,
        mcp_tools.get_understanding_history,
        mcp_tools.search,
        mcp_tools.open_intersection,
        mcp_tools.open_around,
        mcp_tools.get_consolidation_report,
        mcp_tools.get_pending_consolidation,
        mcp_tools.find_similar_subjects,
        mcp_tools.merge_subjects,
        mcp_tools.get_stats,
    ]

    for wrapper in wrappers:
        parameters = inspect.signature(wrapper).parameters
        assert "workspace" not in parameters
        assert "session_id" not in parameters


def test_v3_wrapper_logs_workspace_and_sessions(monkeypatch, caplog):
    class FakeContext:
        session_id = "transport-session"

    monkeypatch.setattr(
        "memory_v3.mcp_tools.resolve_effective_workspace_name",
        lambda workspace: "james/gpt",
    )
    monkeypatch.setattr(
        "memory_v3.mcp_tools.resolve_effective_session_id",
        lambda session_id=None: "conversation-42",
    )
    monkeypatch.setattr(
        "memory_v3.mcp_tools.get_context",
        lambda: FakeContext(),
    )

    with caplog.at_level("INFO"):
        mcp_tools._log_tool_call("orient")

    assert "tool=orient" in caplog.text
    assert "workspace=james/gpt" in caplog.text
    assert "session_id=conversation-42" in caplog.text
    assert "transport_session_id=transport-session" in caplog.text


@pytest.mark.asyncio
async def test_v3_get_status_reports_ready(monkeypatch):
    async def fake_get_stats():
        return {
            "subject_count": 1,
            "observation_count": 2,
            "understanding_count": 3,
            "embedding_coverage": 1.0,
            "current_generation": 4,
            "workspace": "james/gpt",
        }

    monkeypatch.setattr("memory_v3.mcp_tools.tools.get_stats", fake_get_stats)
    monkeypatch.setattr("memory_v3.mcp_tools._log_tool_call", lambda name: None)

    result = await mcp_tools.get_status()

    assert result["status"] == "ready"
    assert result["api_version"] == "v3"
    assert result["workspace"] == "james/gpt"


@pytest.mark.asyncio
async def test_v3_get_status_reports_starting(monkeypatch):
    async def fake_get_stats():
        raise RuntimeError("pool not initialised")

    monkeypatch.setattr("memory_v3.mcp_tools.tools.get_stats", fake_get_stats)
    monkeypatch.setattr("memory_v3.mcp_tools._log_tool_call", lambda name: None)

    result = await mcp_tools.get_status()

    assert result["status"] == "starting"
    assert result["api_version"] == "v3"
    assert "pool not initialised" in result["error"]


@pytest.mark.asyncio
async def test_v3_record_event_serializes_detail_json():
    captured = {}

    class FakeConn:
        async def fetchrow(self, query, *args):
            assert "INSERT INTO sessions" in query
            assert args == (7, "conversation-42")
            return {"session_id": 99}

        async def execute(self, query, *args):
            captured["query"] = query
            captured["args"] = args

    await record_event(
        FakeConn(),
        workspace_id=7,
        session_id="conversation-42",
        operation="orient",
        detail={"session_reset": True},
    )

    assert "INSERT INTO events" in captured["query"]
    assert captured["args"] == (
        7,
        99,
        "orient",
        json.dumps({"session_reset": True}),
    )


@pytest.mark.asyncio
async def test_v3_bring_to_mind_idle_gap_query_uses_typed_interval(monkeypatch):
    captured = {"fetchval_calls": []}

    class FakeConn:
        async def fetchrow(self, query, *args):
            if "INSERT INTO sessions" in query:
                assert args == (7, "conversation-42")
                return {"session_id": 99}
            if "FROM sessions" in query:
                return {
                    "seen_set_token": 123,
                    "updated_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                }
            raise AssertionError(query)

        async def fetchval(self, query, *args):
            captured["fetchval_calls"].append((query, args))
            if "make_interval" in query:
                return False
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "FROM surfaced_in_session" in query:
                assert args == (7, "conversation-42")
                return []
            raise AssertionError(query)

        async def execute(self, query, *args):
            return None

        async def executemany(self, query, args):
            return None

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    async def fake_get_pool():
        return FakePool()

    async def fake_resolve_workspace_id(_conn, workspace):
        assert workspace == "james/gpt"
        return 7

    async def fake_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_optional_session_id", lambda session_id=None: "conversation-42")
    monkeypatch.setattr("memory_v3.tools.resolve_effective_workspace_name", lambda workspace: "james/gpt")
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr("memory_v3.tools.search", fake_search)

    result = await tools_module.bring_to_mind("continuity", last_token=123)

    assert result["compaction_detected"] is False
    interval_call = next(
        call for call in captured["fetchval_calls"] if "make_interval" in call[0]
    )
    assert interval_call[1][1] == 30


@pytest.mark.asyncio
async def test_v3_set_session_model_tier_wrapper_forwards_argument(monkeypatch):
    async def fake_set_session_model_tier(model_tier=None):
        return {
            "session_id": "conversation-42",
            "model_tier": model_tier,
        }

    monkeypatch.setattr(
        "memory_v3.mcp_tools.tools.set_session_model_tier",
        fake_set_session_model_tier,
    )
    monkeypatch.setattr("memory_v3.mcp_tools._log_tool_call", lambda name: None)

    result = await mcp_tools.set_session_model_tier("claude-sonnet-4.5")

    assert result == {
        "session_id": "conversation-42",
        "model_tier": "claude-sonnet-4.5",
    }


@pytest.mark.asyncio
async def test_v3_orient_wrapper_forwards_model_tier(monkeypatch):
    async def fake_orient(model_tier=None):
        return {"model_tier": model_tier}

    monkeypatch.setattr("memory_v3.mcp_tools.tools.orient", fake_orient)
    monkeypatch.setattr("memory_v3.mcp_tools._log_tool_call", lambda name: None)

    result = await mcp_tools.orient("gpt-5.4")

    assert result == {"model_tier": "gpt-5.4"}


@pytest.mark.asyncio
async def test_v3_set_session_model_tier_tool_stores_nullable_model_tier(monkeypatch):
    captured = {}

    class FakeConn:
        async def fetchval(self, query, *args):
            if "SELECT id FROM workspaces" in query:
                return 7
            raise AssertionError(query)

        async def fetchrow(self, query, *args):
            if "RETURNING model_tier" in query:
                captured["session_args"] = args
                return {"model_tier": "claude-opus-4.1"}
            if "RETURNING session_id" in query:
                assert args == (7, "conversation-42")
                return {"session_id": 99}
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "INSERT INTO events" in query:
                captured["event_args"] = args
                return None
            raise AssertionError(query)

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    async def fake_get_pool():
        return FakePool()

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )
    async def fake_resolve_workspace_id(_conn, workspace):
        assert workspace == "james/gpt"
        return 7

    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)

    result = await tools_module.set_session_model_tier(
        " claude-opus-4.1 ",
        workspace="james/gpt",
    )

    assert result == {
        "session_id": "conversation-42",
        "model_tier": "claude-opus-4.1",
    }
    assert captured["session_args"] == (7, "conversation-42", "claude-opus-4.1")
    assert captured["event_args"] == (
        7,
        99,
        "set_session_model_tier",
        json.dumps({"model_tier": "claude-opus-4.1"}),
    )


@pytest.mark.asyncio
async def test_v3_add_observations_uses_session_model_tier(monkeypatch):
    captured = {}

    class FakeConn:
        async def fetchrow(self, query, *args):
            if "INSERT INTO sessions" in query:
                assert args == (7, "conversation-42")
                return {"session_id": 99}
            if "SELECT id, content" in query and "FROM observations" in query:
                return None
            if "INSERT INTO observations" in query:
                captured["insert_args"] = args
                return {
                    "id": 77,
                    "content": "memory3 keeps session provenance",
                    "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                }
            raise AssertionError(query)

        async def executemany(self, query, args):
            if "INSERT INTO observation_subjects" in query:
                captured["subject_links"] = args
                return None
            raise AssertionError(query)

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    async def fake_get_pool():
        return FakePool()

    async def fake_resolve_workspace_id(_conn, workspace):
        assert workspace == "james/gpt"
        return 7

    async def fake_get_workspace_generation(_conn, workspace_id):
        assert workspace_id == 7
        return 3

    async def fake_get_session_model_tier(_conn, workspace_id, session_id):
        assert workspace_id == 7
        assert session_id == "conversation-42"
        return "gpt-5.4"

    async def fake_ensure_subjects(_conn, workspace_id, subject_names):
        assert workspace_id == 7
        assert subject_names == ["memory_system_v3"]
        return ([{"id": 101, "name": "memory_system_v3"}], [])

    async def fake_embed_targets(_conn, *, workspace_id, targets, model_version=None):
        assert workspace_id == 7
        assert targets == [(77, "memory3 keeps session provenance")]

    async def fake_record_event(_conn, *, workspace_id, session_id, operation, detail):
        assert workspace_id == 7
        assert session_id == "conversation-42"
        assert operation == "add_observations"
        assert detail == {"count": 1}

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )
    monkeypatch.setattr(
        "memory_v3.tools.get_workspace_generation",
        fake_get_workspace_generation,
    )
    monkeypatch.setattr(
        "memory_v3.tools._get_session_model_tier",
        fake_get_session_model_tier,
    )
    monkeypatch.setattr("memory_v3.tools._ensure_subjects", fake_ensure_subjects)
    monkeypatch.setattr("memory_v3.tools.embed_targets", fake_embed_targets)
    monkeypatch.setattr("memory_v3.tools.record_event", fake_record_event)

    result = await tools_module.add_observations(
        [
            {
                "subject_names": ["memory_system_v3"],
                "content": "memory3 keeps session provenance",
            }
        ],
        workspace="james/gpt",
    )

    assert result == [
        {
            "id": 77,
            "content": "memory3 keeps session provenance",
            "subject_names": ["memory_system_v3"],
            "subjects_created": [],
        }
    ]
    assert captured["insert_args"][-2:] == (99, "gpt-5.4")
    assert captured["subject_links"] == [(77, 101)]


@pytest.mark.asyncio
async def test_v3_get_consolidation_report_uses_strict_generation_staleness(monkeypatch):
    captured = {}

    class FakeConn:
        async def fetch(self, query, *args):
            if "SELECT s.name, COUNT(os.observation_id) AS observation_count" in query:
                return []
            if "SELECT u.id, u.summary, u.generation, u.created_at" in query:
                captured["stale_query"] = query
                return []
            if "WITH current_pairs AS" in query:
                return []
            if "WITH general_perspective AS" in query:
                return []
            if "SELECT target_id AS id, signal_type AS kind" in query:
                return []
            if "LEFT JOIN understanding_sources us ON us.observation_id = o.id" in query:
                return []
            raise AssertionError(query)

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    async def fake_get_pool():
        return FakePool()

    async def fake_resolve_workspace_id(_conn, workspace):
        assert workspace == "james/gpt"
        return 7

    async def fake_get_workspace_generation(_conn, workspace_id):
        assert workspace_id == 7
        return 0

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr(
        "memory_v3.tools.get_workspace_generation",
        fake_get_workspace_generation,
    )

    result = await tools_module.get_consolidation_report(workspace="james/gpt")

    assert result["stale_understandings"] == []
    assert "o.generation > u.generation" in captured["stale_query"]
    assert "o.generation >= u.generation" not in captured["stale_query"]


@pytest.mark.asyncio
async def test_v3_get_pending_consolidation_uses_generation_not_intersection_count(
    monkeypatch,
):
    async def fake_get_consolidation_report(workspace=None):
        assert workspace == "james/gpt"
        return {
            "subjects_needing_understanding": [
                {
                    "name": "James",
                    "observation_count": 4,
                    "generation": 0,
                }
            ],
            "stale_understandings": [],
            "intersections_needing_synthesis": [
                {
                    "subject_a": "James",
                    "subject_b": "memory_system_v3",
                    "generation": 0,
                    "intersection_size": 1,
                    "new_generation_count": 1,
                    "existing_understanding": None,
                }
            ],
            "semantically_dense_intersections": [],
            "unlinked_observations": [],
            "questionable_items": [],
        }

    monkeypatch.setattr(
        "memory_v3.tools.get_consolidation_report",
        fake_get_consolidation_report,
    )

    result = await tools_module.get_pending_consolidation(workspace="james/gpt")

    assert result == [
        {
            "item_type": "subject",
            "subject_names": ["James"],
            "generation": 0,
            "priority": 4,
        },
        {
            "item_type": "intersection",
            "subject_names": ["James", "memory_system_v3"],
            "generation": 0,
            "priority": 1,
        },
    ]


@pytest.mark.asyncio
async def test_v3_query_observations_embedding_mode_filters_semantic_hits_by_subject(
    monkeypatch,
):
    class FakeConn:
        async def fetch(self, query, *args):
            if "o.id = ANY($2::bigint[])" in query:
                assert args == (7, [8, 9], [101])
                return [{"id": 8}]
            raise AssertionError(query)

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    async def fake_get_pool():
        return FakePool()

    async def fake_resolve_workspace_id(_conn, workspace):
        assert workspace == "james/gpt"
        return 7

    async def fake_require_subjects(_conn, workspace_id, subject_names):
        assert workspace_id == 7
        assert subject_names == ["memory_system_v3"]
        return [{"id": 101, "name": "memory_system_v3"}]

    async def fake_search_embeddings(
        _conn,
        *,
        workspace_id,
        query,
        target_kind=None,
        limit=10,
    ):
        assert workspace_id == 7
        assert query == "relationship understanding"
        assert target_kind == "observation"
        assert limit == 100
        return [
            {"id": 8, "matched_content": "memory3 uses intersection content", "score": 0.765},
            {"id": 9, "matched_content": "different subject", "score": 0.7},
        ]

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr("memory_v3.tools._require_subjects", fake_require_subjects)
    monkeypatch.setattr("memory_v3.tools.search_embeddings", fake_search_embeddings)

    result = await tools_module.query_observations(
        ["memory_system_v3"],
        "relationship understanding",
        mode="embedding",
        workspace="james/gpt",
    )

    assert result == [
        {
            "id": 8,
            "content": "memory3 uses intersection content",
            "score": 0.765,
        }
    ]


@pytest.mark.asyncio
async def test_v3_get_understanding_history_walks_back_from_current_head(monkeypatch):
    stamp_old = datetime(2026, 4, 1, tzinfo=timezone.utc)
    stamp_new = datetime(2026, 4, 2, tzinfo=timezone.utc)

    class FakeConn:
        async def fetch(self, query, *args):
            if "WITH RECURSIVE history AS" in query:
                assert "u.id = h.superseded_by" in query
                assert "u.superseded_by = h.id" in query
                assert args == (7, 40)
                return [
                    {
                        "id": 33,
                        "content": "older content",
                        "summary": "older",
                        "kind": "single_subject",
                        "generation": 1,
                        "created_at": stamp_old,
                        "superseded_by": 40,
                    },
                    {
                        "id": 40,
                        "content": "newer content",
                        "summary": "newer",
                        "kind": "single_subject",
                        "generation": 2,
                        "created_at": stamp_new,
                        "superseded_by": None,
                    },
                ]
            raise AssertionError(query)

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    async def fake_get_pool():
        return FakePool()

    async def fake_resolve_workspace_id(_conn, workspace):
        assert workspace == "james/gpt"
        return 7

    async def fake_get_subject_names_for_targets(_conn, _observation_ids, understanding_ids):
        assert understanding_ids == [33, 40]
        return {33: ["memory_system_v3"], 40: ["memory_system_v3"]}

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr(
        "memory_v3.tools._get_subject_names_for_targets",
        fake_get_subject_names_for_targets,
    )

    result = await tools_module.get_understanding_history(
        40,
        workspace="james/gpt",
    )

    assert result == [
        {
            "id": 33,
            "content": "older content",
            "summary": "older",
            "kind": "single_subject",
            "generation": 1,
            "created_at": stamp_old.isoformat(),
            "superseded_by": 40,
            "subject_names": ["memory_system_v3"],
        },
        {
            "id": 40,
            "content": "newer content",
            "summary": "newer",
            "kind": "single_subject",
            "generation": 2,
            "created_at": stamp_new.isoformat(),
            "superseded_by": None,
            "subject_names": ["memory_system_v3"],
        },
    ]


@pytest.mark.asyncio
async def test_v3_recall_question_mode_returns_best_answer_provenance(monkeypatch):
    class FakeConn:
        async def fetchrow(self, query, *args):
            if "FROM subjects" in query:
                assert args == (7, "What motivated James to build memory3?")
                return None
            raise AssertionError(query)

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    async def fake_get_pool():
        return FakePool()

    async def fake_resolve_workspace_id(_conn, workspace):
        assert workspace == "james/gpt"
        return 7

    async def fake_search(query, limit=10, mode="embedding", workspace=None):
        assert query == "What motivated James to build memory3?"
        assert workspace == "james/gpt"
        return [
            {
                "id": 47,
                "kind": "understanding",
                "subject_names": ["James", "memory_system_v3"],
                "matched_content": "James wanted durable continuity across sessions.",
                "score": 0.84,
                "created_at": "2026-04-02T00:00:00+00:00",
                "session_id": "conversation-42",
                "model_tier": "gpt-5.4",
            }
        ]

    async def fake_mark_targets_surfaced(_conn, *, workspace_id, session_id, target_ids):
        assert workspace_id == 7
        assert session_id == "conversation-42"
        assert target_ids == [47]

    async def fake_record_event(_conn, *, workspace_id, session_id, operation, detail):
        assert workspace_id == 7
        assert session_id == "conversation-42"
        assert operation == "recall"
        assert detail == {"mode": "question", "result_count": 1}

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr("memory_v3.tools.resolve_optional_session_id", lambda session_id=None: "conversation-42")
    monkeypatch.setattr("memory_v3.tools.search", fake_search)
    monkeypatch.setattr("memory_v3.tools._mark_targets_surfaced", fake_mark_targets_surfaced)
    monkeypatch.setattr("memory_v3.tools.record_event", fake_record_event)

    result = await tools_module.recall(
        "What motivated James to build memory3?",
        workspace="james/gpt",
    )

    assert result == {
        "best_answer": {
            "subject_names": ["James", "memory_system_v3"],
            "content": "James wanted durable continuity across sessions.",
            "confidence": 0.84,
            "kind": "understanding",
            "source": "understanding",
        },
        "supporting": [],
        "provenance": {
            "session_id": "conversation-42",
            "model_tier": "gpt-5.4",
            "created_at": "2026-04-02T00:00:00+00:00",
        },
    }


@pytest.mark.asyncio
async def test_v3_set_workspace_documents_wrapper_forwards_ids(monkeypatch):
    async def fake_set_workspace_documents(
        soul_understanding_id=None,
        protocol_understanding_id=None,
        orientation_understanding_id=None,
    ):
        return {
            "soul_understanding_id": soul_understanding_id,
            "protocol_understanding_id": protocol_understanding_id,
            "orientation_understanding_id": orientation_understanding_id,
        }

    monkeypatch.setattr(
        "memory_v3.mcp_tools.tools.set_workspace_documents",
        fake_set_workspace_documents,
    )
    monkeypatch.setattr("memory_v3.mcp_tools._log_tool_call", lambda name: None)

    result = await mcp_tools.set_workspace_documents(
        soul_understanding_id=11,
        protocol_understanding_id=12,
    )

    assert result == {
        "soul_understanding_id": 11,
        "protocol_understanding_id": 12,
        "orientation_understanding_id": None,
    }


@pytest.mark.asyncio
async def test_v3_update_understanding_wrapper_uses_new_api(monkeypatch):
    async def fake_update_understanding(
        understanding_id,
        new_content,
        new_summary,
        subject_names=None,
        reason=None,
    ):
        return {
            "old_understanding_id": understanding_id,
            "new_content": new_content,
            "new_summary": new_summary,
            "subject_names": subject_names,
            "reason": reason,
        }

    monkeypatch.setattr(
        "memory_v3.mcp_tools.tools.update_understanding",
        fake_update_understanding,
    )
    monkeypatch.setattr("memory_v3.mcp_tools._log_tool_call", lambda name: None)

    result = await mcp_tools.update_understanding(
        33,
        "new content",
        "new summary",
        subject_names=["memory_system_v3"],
        reason="manual correction",
    )

    assert result == {
        "old_understanding_id": 33,
        "new_content": "new content",
        "new_summary": "new summary",
        "subject_names": ["memory_system_v3"],
        "reason": "manual correction",
    }


@pytest.mark.asyncio
async def test_v3_update_understanding_rejects_superseded_understanding(monkeypatch):
    class FakeConn:
        async def fetchval(self, query, *args):
            if "SELECT model_tier" in query and "FROM sessions" in query:
                assert args == (7, "conversation-42")
                return None
            raise AssertionError(query)

        async def fetchrow(self, query, *args):
            if "SELECT id, kind, superseded_by" in query:
                assert args == (7, 33)
                return {
                    "id": 33,
                    "kind": "single_subject",
                    "superseded_by": 40,
                }
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "WITH RECURSIVE successors AS" in query:
                assert args == (7, 33)
                return [
                    {"id": 33, "superseded_by": 40},
                    {"id": 40, "superseded_by": None},
                ]
            raise AssertionError(query)

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    async def fake_get_pool():
        return FakePool()

    async def fake_resolve_workspace_id(_conn, workspace):
        assert workspace == "james/gpt"
        return 7

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )

    with pytest.raises(
        ValueError,
        match="Understanding 33 is superseded. Current understanding is 40",
    ):
        await tools_module.update_understanding(
            33,
            "new content",
            "new summary",
            workspace="james/gpt",
        )


@pytest.mark.asyncio
async def test_v3_recall_subject_rejects_superseded_pointer(monkeypatch):
    class FakeConn:
        async def fetchrow(self, query, *args):
            if "FROM subjects" in query:
                return {
                    "id": 101,
                    "name": "memory_system_v3",
                    "summary": "summary",
                    "tags": [],
                    "single_subject_understanding_id": 33,
                    "structural_understanding_id": None,
                }
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "SELECT id, content, summary, kind, generation, created_at, superseded_by" in query:
                assert args == ([33],)
                return [
                    {
                        "id": 33,
                        "content": "old",
                        "summary": "old",
                        "kind": "single_subject",
                        "generation": 1,
                        "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                        "superseded_by": 40,
                    }
                ]
            raise AssertionError(query)

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    async def fake_get_pool():
        return FakePool()

    async def fake_resolve_workspace_id(_conn, workspace):
        assert workspace == "james/gpt"
        return 7

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )

    with pytest.raises(
        ValueError,
        match="Single-subject understanding pointer for memory_system_v3 33 is superseded by 40",
    ):
        await tools_module.recall("memory_system_v3", workspace="james/gpt")


@pytest.mark.asyncio
async def test_v3_orient_rejects_superseded_special_pointer(monkeypatch):
    class FakeConn:
        async def fetchrow(self, query, *args):
            if "FROM workspaces" in query:
                return {
                    "id": 7,
                    "soul_understanding_id": 11,
                    "protocol_understanding_id": None,
                    "orientation_understanding_id": None,
                    "last_consolidated_at": None,
                }
            if "INSERT INTO sessions" in query:
                return {"model_tier": None}
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "DELETE FROM surfaced_in_session" in query:
                return None
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "SELECT id, content, summary, kind, generation, created_at, superseded_by" in query:
                assert args == ([11],)
                return [
                    {
                        "id": 11,
                        "content": "old soul",
                        "summary": "old soul",
                        "kind": "soul",
                        "generation": 0,
                        "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                        "superseded_by": 12,
                    }
                ]
            raise AssertionError(query)

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    async def fake_get_pool():
        return FakePool()

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )
    monkeypatch.setattr(
        "memory_v3.tools.resolve_effective_workspace_name",
        lambda workspace=None: "james/gpt",
    )

    with pytest.raises(
        ValueError,
        match="Workspace special understanding pointer 11 is superseded by 12",
    ):
        await tools_module.orient(workspace="james/gpt")


@pytest.mark.asyncio
async def test_v3_set_workspace_documents_validates_active_understandings(monkeypatch):
    captured = {}

    class FakeConn:
        async def fetchval(self, query, *args):
            if "SELECT id FROM workspaces" in query:
                assert args == ("james/gpt",)
                return 7
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "SELECT id, content, summary, kind, generation, created_at, superseded_by" in query:
                assert args == ([11, 12],)
                return [
                    {
                        "id": 11,
                        "content": "soul",
                        "summary": "soul",
                        "kind": "soul",
                        "generation": 0,
                        "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                        "superseded_by": None,
                    },
                    {
                        "id": 12,
                        "content": "protocol",
                        "summary": "protocol",
                        "kind": "protocol",
                        "generation": 0,
                        "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                        "superseded_by": None,
                    },
                ]
            raise AssertionError(query)

        async def fetchrow(self, query, *args):
            if "UPDATE workspaces" in query:
                captured["update_query"] = query
                captured["update_args"] = args
                return {
                    "soul_understanding_id": 11,
                    "protocol_understanding_id": 12,
                    "orientation_understanding_id": None,
                }
            if "INSERT INTO sessions" in query:
                assert args == (7, "conversation-42")
                return {"session_id": 99}
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "INSERT INTO events" in query:
                captured["event_args"] = args
                return None
            raise AssertionError(query)

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    async def fake_get_pool():
        return FakePool()

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )

    result = await tools_module.set_workspace_documents(
        soul_understanding_id=11,
        protocol_understanding_id=12,
        workspace="james/gpt",
    )

    assert result == {
        "soul_understanding_id": 11,
        "protocol_understanding_id": 12,
        "orientation_understanding_id": None,
    }
    assert "soul_understanding_id = $2" in captured["update_query"]
    assert "protocol_understanding_id = $3" in captured["update_query"]
    assert captured["update_args"] == (7, 11, 12)
    assert captured["event_args"] == (
        7,
        99,
        "set_workspace_documents",
        json.dumps(
            {
                "soul_understanding_id": 11,
                "protocol_understanding_id": 12,
            }
        ),
    )
