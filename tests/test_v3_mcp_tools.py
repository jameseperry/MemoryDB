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
    resolve_effective_readonly,
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


def test_v3_resolve_effective_readonly_uses_header(monkeypatch):
    monkeypatch.setattr(
        "memory_v3.db.get_http_headers",
        lambda: {settings.mcp_readonly_header.lower(): "true"},
    )
    assert resolve_effective_readonly() is True


def test_v3_resolve_effective_readonly_rejects_mismatch(monkeypatch):
    monkeypatch.setattr(
        "memory_v3.db.get_http_headers",
        lambda: {settings.mcp_readonly_header.lower(): "true"},
    )
    with pytest.raises(ValueError, match="Readonly parameter does not match"):
        resolve_effective_readonly(False)


def test_v3_wrappers_do_not_expose_workspace_or_session():
    wrappers = [
        mcp_tools.get_status,
        mcp_tools.orient,
        mcp_tools.bring_to_mind,
        mcp_tools.recall,
        mcp_tools.reset_seen,
        mcp_tools.set_session_model_tier,
        mcp_tools.set_workspace_documents,
        mcp_tools.get_named_understandings,
        mcp_tools.set_named_understanding,
        mcp_tools.remember,
        mcp_tools.update_understanding,
        mcp_tools.finalize_consolidation,
        mcp_tools.rewrite_understanding,
        mcp_tools.delete_understanding,
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
        assert "readonly" not in parameters


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
async def test_v3_orient_wrapper_forwards_model_tier_and_mode(monkeypatch):
    async def fake_orient(model_tier=None, mode="interaction"):
        return {"model_tier": model_tier, "mode": mode}

    monkeypatch.setattr("memory_v3.mcp_tools.tools.orient", fake_orient)
    monkeypatch.setattr("memory_v3.mcp_tools._log_tool_call", lambda name: None)

    result = await mcp_tools.orient("gpt-5.4", "consolidation")

    assert result == {"model_tier": "gpt-5.4", "mode": "consolidation"}


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
        async def fetch(self, query, *args):
            if "SELECT id" in query and "FROM observations" in query:
                assert args == (7, [88])
                return [{"id": 88}]
            raise AssertionError(query)

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
            if "INSERT INTO observation_links" in query:
                captured["observation_links"] = args
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
                "points_to": [88],
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
            "points_to": [88],
            "pointed_to_by": [],
        }
    ]
    assert captured["insert_args"][-2:] == (99, "gpt-5.4")
    assert captured["subject_links"] == [(77, 101)]
    assert captured["observation_links"] == [(77, 88)]


@pytest.mark.asyncio
async def test_v3_add_observations_validates_points_to_ids(monkeypatch):
    class FakeConn:
        async def fetch(self, query, *args):
            if "SELECT id" in query and "FROM observations" in query:
                assert args == (7, [88, 99])
                return [{"id": 88}]
            raise AssertionError(query)

        async def fetchrow(self, query, *args):
            if "INSERT INTO sessions" in query:
                return {"session_id": 99}
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
        return 7

    async def fake_get_workspace_generation(_conn, workspace_id):
        return 3

    async def fake_get_session_model_tier(_conn, workspace_id, session_id):
        return "gpt-5.4"

    async def fake_ensure_subjects(_conn, workspace_id, subject_names):
        return ([{"id": 101, "name": "memory_system_v3"}], [])

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )
    monkeypatch.setattr("memory_v3.tools.get_workspace_generation", fake_get_workspace_generation)
    monkeypatch.setattr("memory_v3.tools._get_session_model_tier", fake_get_session_model_tier)
    monkeypatch.setattr("memory_v3.tools._ensure_subjects", fake_ensure_subjects)

    with pytest.raises(ValueError, match=r"Observations not found: \[99\]"):
        await tools_module.add_observations(
            [
                {
                    "subject_names": ["memory_system_v3"],
                    "content": "memory3 keeps session provenance",
                    "points_to": [88, 99],
                }
            ],
            workspace="james/gpt",
        )


@pytest.mark.asyncio
async def test_v3_create_understanding_uses_session_model_tier(monkeypatch):
    class FakeConn:
        async def fetchrow(self, query, *args):
            if "INSERT INTO sessions" in query:
                assert args == (7, "conversation-42")
                return {"session_id": 99}
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "SELECT id" in query and "FROM observations" in query:
                assert args == (7, [55])
                return [{"id": 55}]
            raise AssertionError(query)

        async def fetchval(self, query, *args):
            if "SELECT model_tier" in query and "FROM sessions" in query:
                assert args == (7, "conversation-42")
                return "claude-sonnet-4-6"
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
        assert workspace == "james/claude"
        return 7

    async def fake_require_subjects(_conn, workspace_id, subject_names):
        assert workspace_id == 7
        assert subject_names == ["memory_system_v3"]
        return [{"id": 101, "name": "memory_system_v3"}]

    async def fake_get_workspace_generation(_conn, workspace_id):
        assert workspace_id == 7
        return 3

    async def fake_create_understanding_record(
        _conn,
        *,
        workspace_id,
        subject_rows,
        content,
        summary,
        kind,
        generation,
        session_id,
        source_observation_ids=None,
        reason=None,
        model_tier=None,
    ):
        assert workspace_id == 7
        assert subject_rows == [{"id": 101, "name": "memory_system_v3"}]
        assert content == "Memory3 prefers consolidated prose edges."
        assert summary == "prose edges"
        assert kind == "single_subject"
        assert generation == 3
        assert session_id == "conversation-42"
        assert source_observation_ids == [55]
        assert reason == "manual synthesis"
        assert model_tier == "claude-sonnet-4-6"
        return {
            "id": 88,
            "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
        }

    async def fake_record_event(_conn, *, workspace_id, session_id, operation, detail):
        assert workspace_id == 7
        assert session_id == "conversation-42"
        assert operation == "create_understanding"
        assert detail == {
            "understanding_id": 88,
            "kind": "single_subject",
            "subject_names": ["memory_system_v3"],
        }

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )
    monkeypatch.setattr("memory_v3.tools._require_subjects", fake_require_subjects)
    monkeypatch.setattr(
        "memory_v3.tools.get_workspace_generation",
        fake_get_workspace_generation,
    )
    monkeypatch.setattr(
        "memory_v3.tools._create_understanding_record",
        fake_create_understanding_record,
    )
    monkeypatch.setattr("memory_v3.tools.record_event", fake_record_event)

    result = await tools_module.create_understanding(
        ["memory_system_v3"],
        "Memory3 prefers consolidated prose edges.",
        "prose edges",
        source_observation_ids=[55],
        workspace="james/claude",
        reason="manual synthesis",
    )

    assert result == {
        "id": 88,
        "subject_names": ["memory_system_v3"],
        "kind": "single_subject",
        "created_at": "2026-04-02T00:00:00+00:00",
    }


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
async def test_v3_create_understanding_validates_source_observation_ids(monkeypatch):
    class FakeConn:
        async def fetchrow(self, query, *args):
            if "INSERT INTO sessions" in query:
                assert args == (7, "conversation-42")
                return {"session_id": 99}
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "SELECT id\n                FROM observations" in query:
                assert args == (7, [55, 88])
                return [{"id": 55}]
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
        assert workspace == "james/claude"
        return 7

    async def fake_require_subjects(_conn, workspace_id, subject_names):
        assert workspace_id == 7
        assert subject_names == ["memory_system_v3"]
        return [{"id": 101, "name": "memory_system_v3"}]

    async def fake_get_session_model_tier(_conn, workspace_id, session_id):
        assert workspace_id == 7
        assert session_id == "conversation-42"
        return "claude-sonnet-4-6"

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )
    monkeypatch.setattr("memory_v3.tools._require_subjects", fake_require_subjects)
    monkeypatch.setattr(
        "memory_v3.tools._get_session_model_tier",
        fake_get_session_model_tier,
    )

    with pytest.raises(ValueError, match=r"Observations not found: \[88\]"):
        await tools_module.create_understanding(
            ["memory_system_v3"],
            "Memory3 prefers consolidated prose edges.",
            "prose edges",
            source_observation_ids=[55, 88],
            workspace="james/claude",
            reason="manual synthesis",
        )


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
            if "FROM observation_links" in query:
                assert args == ([8],)
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
            "points_to": [],
            "pointed_to_by": [],
        }
    ]


@pytest.mark.asyncio
async def test_v3_query_observations_text_mode_groups_by_content_tsv(monkeypatch):
    captured = {}

    class FakeConn:
        async def fetch(self, query, *args):
            if "ts_rank(o.content_tsv" in query:
                captured["query"] = query
                assert args == (7, "relationship understanding", [101])
                return []
            if "FROM observation_links" in query:
                assert args == ([],)
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

    async def fake_require_subjects(_conn, workspace_id, subject_names):
        assert workspace_id == 7
        assert subject_names == ["memory_system_v3"]
        return [{"id": 101, "name": "memory_system_v3"}]

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr("memory_v3.tools._require_subjects", fake_require_subjects)

    result = await tools_module.query_observations(
        ["memory_system_v3"],
        "relationship understanding",
        mode="text",
        workspace="james/gpt",
    )

    assert result == []
    assert "GROUP BY o.id, o.content, o.content_tsv, o.created_at" in captured["query"]


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
async def test_v3_get_workspace_documents_wrapper(monkeypatch):
    async def fake_get_workspace_documents():
        return {
            "soul_understanding_id": 11,
            "protocol_understanding_id": 12,
            "orientation_understanding_id": None,
            "consolidation_understanding_id": 13,
        }

    monkeypatch.setattr(
        "memory_v3.mcp_tools.tools.get_workspace_documents",
        fake_get_workspace_documents,
    )
    monkeypatch.setattr("memory_v3.mcp_tools._log_tool_call", lambda name: None)

    result = await mcp_tools.get_workspace_documents()

    assert result == {
        "soul_understanding_id": 11,
        "protocol_understanding_id": 12,
        "orientation_understanding_id": None,
        "consolidation_understanding_id": 13,
    }


@pytest.mark.asyncio
async def test_v3_set_workspace_documents_wrapper_forwards_ids(monkeypatch):
    async def fake_set_workspace_documents(
        soul_understanding_id=None,
        protocol_understanding_id=None,
        orientation_understanding_id=None,
        consolidation_understanding_id=None,
    ):
        return {
            "soul_understanding_id": soul_understanding_id,
            "protocol_understanding_id": protocol_understanding_id,
            "orientation_understanding_id": orientation_understanding_id,
            "consolidation_understanding_id": consolidation_understanding_id,
        }

    monkeypatch.setattr(
        "memory_v3.mcp_tools.tools.set_workspace_documents",
        fake_set_workspace_documents,
    )
    monkeypatch.setattr("memory_v3.mcp_tools._log_tool_call", lambda name: None)

    result = await mcp_tools.set_workspace_documents(
        soul_understanding_id=11,
        protocol_understanding_id=12,
        consolidation_understanding_id=13,
    )

    assert result == {
        "soul_understanding_id": 11,
        "protocol_understanding_id": 12,
        "orientation_understanding_id": None,
        "consolidation_understanding_id": 13,
    }


@pytest.mark.asyncio
async def test_v3_get_named_understandings_wrapper(monkeypatch):
    async def fake_get_named_understandings(names=None):
        assert names == ["design_note", "protocol"]
        return {"design_note": 41, "protocol": 12}

    monkeypatch.setattr(
        "memory_v3.mcp_tools.tools.get_named_understandings",
        fake_get_named_understandings,
    )
    monkeypatch.setattr("memory_v3.mcp_tools._log_tool_call", lambda name: None)

    result = await mcp_tools.get_named_understandings(["design_note", "protocol"])

    assert result == {"design_note": 41, "protocol": 12}


@pytest.mark.asyncio
async def test_v3_set_named_understanding_wrapper(monkeypatch):
    async def fake_set_named_understanding(name, understanding_id=None):
        return {"name": name, "understanding_id": understanding_id}

    monkeypatch.setattr(
        "memory_v3.mcp_tools.tools.set_named_understanding",
        fake_set_named_understanding,
    )
    monkeypatch.setattr("memory_v3.mcp_tools._log_tool_call", lambda name: None)

    result = await mcp_tools.set_named_understanding(
        name="design_note",
        understanding_id=41,
    )

    assert result == {"name": "design_note", "understanding_id": 41}


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
async def test_v3_finalize_consolidation_wrapper_uses_new_api(monkeypatch):
    async def fake_finalize_consolidation(
        expected_generation,
        summary,
        updated_understanding_ids=None,
        created_understanding_ids=None,
    ):
        return {
            "expected_generation": expected_generation,
            "summary": summary,
            "updated_understanding_ids": updated_understanding_ids,
            "created_understanding_ids": created_understanding_ids,
        }

    monkeypatch.setattr(
        "memory_v3.mcp_tools.tools.finalize_consolidation",
        fake_finalize_consolidation,
    )
    monkeypatch.setattr("memory_v3.mcp_tools._log_tool_call", lambda name: None)

    result = await mcp_tools.finalize_consolidation(
        3,
        "Consolidated test subject",
        updated_understanding_ids=[10],
        created_understanding_ids=[11],
    )

    assert result == {
        "expected_generation": 3,
        "summary": "Consolidated test subject",
        "updated_understanding_ids": [10],
        "created_understanding_ids": [11],
    }


@pytest.mark.asyncio
async def test_v3_rewrite_understanding_wrapper_uses_new_api(monkeypatch):
    async def fake_rewrite_understanding(
        understanding_id,
        new_content,
        new_summary,
    ):
        return {
            "understanding_id": understanding_id,
            "new_content": new_content,
            "new_summary": new_summary,
        }

    monkeypatch.setattr(
        "memory_v3.mcp_tools.tools.rewrite_understanding",
        fake_rewrite_understanding,
    )
    monkeypatch.setattr("memory_v3.mcp_tools._log_tool_call", lambda name: None)

    result = await mcp_tools.rewrite_understanding(
        33,
        "draft two",
        "better summary",
    )

    assert result == {
        "understanding_id": 33,
        "new_content": "draft two",
        "new_summary": "better summary",
    }


@pytest.mark.asyncio
async def test_v3_delete_understanding_wrapper_uses_new_api(monkeypatch):
    async def fake_delete_understanding(understanding_id):
        return {
            "id": understanding_id,
            "deleted": True,
        }

    monkeypatch.setattr(
        "memory_v3.mcp_tools.tools.delete_understanding",
        fake_delete_understanding,
    )
    monkeypatch.setattr("memory_v3.mcp_tools._log_tool_call", lambda name: None)

    result = await mcp_tools.delete_understanding(33)

    assert result == {
        "id": 33,
        "deleted": True,
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
            if "INSERT INTO sessions" in query:
                assert args == (7, "conversation-42")
                return {"session_id": 99}
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
async def test_v3_delete_observations_rejects_already_consolidated(monkeypatch):
    class FakeConn:
        async def fetch(self, query, *args):
            if "SELECT o.id, o.generation, s.session_token AS session_id" in query:
                assert args == (7, [10])
                return [
                    {
                        "id": 10,
                        "generation": 2,
                        "session_id": "conversation-42",
                    }
                ]
            raise AssertionError(query)

        async def execute(self, query, *args):
            raise AssertionError(f"unexpected execute: {query} {args}")

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

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr("memory_v3.tools.get_workspace_generation", fake_get_workspace_generation)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )

    result = await tools_module.delete_observations([10], workspace="james/gpt")

    assert result == {
        "deleted": [],
        "rejected": [{"id": 10, "reason": "already consolidated"}],
    }


@pytest.mark.asyncio
async def test_v3_remember_rejects_readonly_header(monkeypatch):
    monkeypatch.setattr(
        "memory_v3.db.get_http_headers",
        lambda: {settings.mcp_readonly_header.lower(): "true"},
    )

    with pytest.raises(
        PermissionError,
        match=f"{settings.mcp_readonly_header} forbids mutation",
    ):
        await tools_module.remember(
            ["memory_system_v3"],
            "Readonly write should fail.",
            workspace="james/gpt",
        )


@pytest.mark.asyncio
async def test_v3_rewrite_understanding_updates_in_place(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConn:
        async def fetchrow(self, query, *args):
            if "SELECT u.id, u.kind, u.generation, u.superseded_by, s.session_token AS session_id" in query:
                assert args == (7, 33)
                return {
                    "id": 33,
                    "kind": "single_subject",
                    "generation": 4,
                    "superseded_by": None,
                    "session_id": "conversation-42",
                }
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "UPDATE records" in query:
                captured["records_update"] = args
                return "UPDATE 1"
            if "UPDATE understanding_records" in query:
                captured["understanding_update"] = args
                return "UPDATE 1"
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
        return 4

    async def fake_embed_targets(conn, *, workspace_id, targets):
        captured["embed_targets"] = (workspace_id, targets)

    async def fake_record_event(conn, *, workspace_id, session_id, operation, detail):
        captured["event"] = {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "operation": operation,
            "detail": detail,
        }

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr("memory_v3.tools.get_workspace_generation", fake_get_workspace_generation)
    monkeypatch.setattr("memory_v3.tools.embed_targets", fake_embed_targets)
    monkeypatch.setattr("memory_v3.tools.record_event", fake_record_event)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )

    result = await tools_module.rewrite_understanding(
        33,
        "draft two",
        "better summary",
        workspace="james/gpt",
    )

    assert result == {
        "understanding_id": 33,
        "rewritten": True,
        "new_content": "draft two",
        "new_summary": "better summary",
    }
    assert captured["records_update"] == (33, 7, "draft two")
    assert captured["understanding_update"] == (33, 7, "better summary")
    assert captured["embed_targets"] == (7, [(33, "draft two")])
    assert captured["event"] == {
        "workspace_id": 7,
        "session_id": "conversation-42",
        "operation": "rewrite_understanding",
        "detail": {"understanding_id": 33},
    }


@pytest.mark.asyncio
async def test_v3_rewrite_understanding_rejects_already_consolidated(monkeypatch):
    class FakeConn:
        async def fetchrow(self, query, *args):
            if "SELECT u.id, u.kind, u.generation, u.superseded_by, s.session_token AS session_id" in query:
                assert args == (7, 33)
                return {
                    "id": 33,
                    "kind": "single_subject",
                    "generation": 1,
                    "superseded_by": None,
                    "session_id": "conversation-42",
                }
            raise AssertionError(query)

        async def execute(self, query, *args):
            raise AssertionError(f"unexpected execute: {query} {args}")

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
        return 2

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr("memory_v3.tools.get_workspace_generation", fake_get_workspace_generation)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )

    with pytest.raises(
        ValueError,
        match="Understanding 33 cannot be rewritten: already consolidated",
    ):
        await tools_module.rewrite_understanding(
            33,
            "draft two",
            "better summary",
            workspace="james/gpt",
        )


@pytest.mark.asyncio
async def test_v3_delete_understanding_deletes_current_session_draft(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConn:
        async def fetchrow(self, query, *args):
            if "SELECT u.id, u.kind, u.generation, u.superseded_by, s.session_token AS session_id" in query:
                assert args == (7, 33)
                return {
                    "id": 33,
                    "kind": "protocol",
                    "generation": 4,
                    "superseded_by": None,
                    "session_id": "conversation-42",
                }
            raise AssertionError(query)

        async def fetchval(self, query, *args):
            if "SELECT COUNT(*)" in query and "superseded_by = $2" in query:
                assert args == (7, 33)
                return 0
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "DELETE FROM named_understandings" in query:
                captured["clear_named_understandings"] = args
                return "DELETE 0"
            if "UPDATE subjects" in query:
                captured["clear_subjects"] = args
                return "UPDATE 0"
            if "UPDATE workspaces" in query:
                captured["clear_workspaces"] = args
                return "UPDATE 1"
            if "DELETE FROM understandings" in query:
                captured["delete"] = args
                return "DELETE 1"
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
        return 4

    async def fake_record_event(conn, *, workspace_id, session_id, operation, detail):
        captured["event"] = {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "operation": operation,
            "detail": detail,
        }

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr("memory_v3.tools.get_workspace_generation", fake_get_workspace_generation)
    monkeypatch.setattr("memory_v3.tools.record_event", fake_record_event)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )

    result = await tools_module.delete_understanding(33, workspace="james/gpt")

    assert result == {"id": 33, "deleted": True}
    assert captured["clear_named_understandings"] == (33,)
    assert captured["clear_subjects"] == (33,)
    assert captured["clear_workspaces"] == (33,)
    assert captured["delete"] == (33,)
    assert captured["event"] == {
        "workspace_id": 7,
        "session_id": "conversation-42",
        "operation": "delete_understanding",
        "detail": {"understanding_id": 33},
    }


@pytest.mark.asyncio
async def test_v3_delete_understanding_rejects_already_consolidated(monkeypatch):
    class FakeConn:
        async def fetchrow(self, query, *args):
            if "SELECT u.id, u.kind, u.generation, u.superseded_by, s.session_token AS session_id" in query:
                assert args == (7, 33)
                return {
                    "id": 33,
                    "kind": "protocol",
                    "generation": 0,
                    "superseded_by": None,
                    "session_id": "conversation-42",
                }
            raise AssertionError(query)

        async def fetchval(self, query, *args):
            raise AssertionError(f"unexpected fetchval: {query} {args}")

        async def execute(self, query, *args):
            raise AssertionError(f"unexpected execute: {query} {args}")

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
        return 1

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr("memory_v3.tools.get_workspace_generation", fake_get_workspace_generation)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )

    with pytest.raises(
        ValueError,
        match="Understanding 33 cannot be deleted: already consolidated",
    ):
        await tools_module.delete_understanding(33, workspace="james/gpt")


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
                    "consolidation_understanding_id": 13,
                    "last_consolidated_at": None,
                }
            if "INSERT INTO sessions" in query:
                return {"model_tier": None}
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "UPDATE sessions" in query and "seen_set_token = 0" in query:
                return None
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "DELETE FROM surfaced_in_session" in query:
                assert args == (7, "conversation-42")
                return []
            if "FROM named_understandings" in query:
                assert args == (7, ["soul", "protocol", "orientation", "consolidation"])
                return [
                    {"name": "soul", "understanding_id": 11},
                    {"name": "consolidation", "understanding_id": 13},
                ]
            if "SELECT id, content, summary, kind, generation, created_at, superseded_by" in query:
                assert args == ([11, 13],)
                return [
                    {
                        "id": 11,
                        "content": "old soul",
                        "summary": "old soul",
                        "kind": "soul",
                        "generation": 0,
                        "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                        "superseded_by": 12,
                    },
                    {
                        "id": 13,
                        "content": "consolidation",
                        "summary": "consolidation",
                        "kind": "consolidation",
                        "generation": 0,
                        "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                        "superseded_by": None,
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
async def test_v3_orient_uses_strict_generation_for_pending_subjects(monkeypatch):
    captured = {}

    class FakeConn:
        async def fetchrow(self, query, *args):
            if "FROM workspaces" in query:
                assert args == ("james/gpt",)
                return {
                    "id": 7,
                    "soul_understanding_id": None,
                    "protocol_understanding_id": None,
                    "orientation_understanding_id": None,
                    "consolidation_understanding_id": None,
                    "last_consolidated_at": None,
                }
            if "INSERT INTO sessions" in query and "RETURNING model_tier" in query:
                assert args == (7, "conversation-42", None)
                return {"model_tier": None}
            if "INSERT INTO sessions" in query and "RETURNING session_id" in query:
                assert args == (7, "conversation-42")
                return {"session_id": 99}
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "UPDATE sessions" in query and "seen_set_token = 0" in query:
                assert args == (7, "conversation-42")
                captured["reset_seen_set_token"] = True
                return None
            if "INSERT INTO events" in query:
                assert args == (
                    7,
                    99,
                    "orient",
                    json.dumps({"session_reset": True}),
                )
                return None
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "DELETE FROM surfaced_in_session" in query:
                assert args == (7, "conversation-42")
                return []
            if "FROM named_understandings" in query:
                assert args == (7, ["soul", "protocol", "orientation", "consolidation"])
                return []
            raise AssertionError(query)

        async def fetchval(self, query, *args):
            if "SELECT COUNT(*)" in query and "FROM subjects s" in query:
                captured["pending_subjects_query"] = query
                assert args == (7,)
                return 0
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

    result = await tools_module.orient(workspace="james/gpt")

    assert result["pending_consolidation_count"] == 0
    assert captured["reset_seen_set_token"] is True
    assert "o.generation > u.generation" in captured["pending_subjects_query"]
    assert "o.generation >= u.generation" not in captured["pending_subjects_query"]


@pytest.mark.asyncio
async def test_v3_get_workspace_documents_reads_pointer_ids(monkeypatch):
    class FakeConn:
        async def fetchval(self, query, *args):
            if "SELECT id FROM workspaces" in query:
                assert args == ("james/gpt",)
                return 7
            raise AssertionError(query)

        async def fetchrow(self, query, *args):
            if "SELECT" in query and "soul_understanding_id" in query:
                assert args == (7,)
                return {
                    "soul_understanding_id": 91,
                    "protocol_understanding_id": 92,
                    "orientation_understanding_id": 93,
                    "consolidation_understanding_id": 94,
                }
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "FROM named_understandings" in query:
                assert args == (7, ["soul", "protocol", "orientation", "consolidation"])
                return [
                    {"name": "soul", "understanding_id": 11},
                    {"name": "protocol", "understanding_id": 12},
                    {"name": "consolidation", "understanding_id": 13},
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

    result = await tools_module.get_workspace_documents(workspace="james/gpt")

    assert result == {
        "soul_understanding_id": 11,
        "protocol_understanding_id": 12,
        "orientation_understanding_id": 93,
        "consolidation_understanding_id": 13,
    }


@pytest.mark.asyncio
async def test_v3_set_workspace_documents_validates_active_understandings(monkeypatch):
    captured = {"named": {}}

    class FakeConn:
        async def fetchval(self, query, *args):
            if "SELECT id FROM workspaces" in query:
                assert args == ("james/gpt",)
                return 7
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "SELECT id, content, summary, kind, generation, created_at, superseded_by" in query:
                assert args == ([11, 12, 13],)
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
                    {
                        "id": 13,
                        "content": "consolidation",
                        "summary": "consolidation",
                        "kind": "consolidation",
                        "generation": 0,
                        "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                        "superseded_by": None,
                    },
                ]
            if "FROM named_understandings" in query:
                assert args == (7, ["soul", "protocol", "orientation", "consolidation"])
                return [
                    {"name": name, "understanding_id": understanding_id}
                    for name, understanding_id in sorted(captured["named"].items())
                ]
            raise AssertionError(query)

        async def fetchrow(self, query, *args):
            if "INSERT INTO sessions" in query:
                assert args == (7, "conversation-42")
                return {"session_id": 99}
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "INSERT INTO named_understandings" in query:
                _, name, understanding_id = args
                captured["named"][name] = understanding_id
                return None
            if "DELETE FROM named_understandings" in query:
                _, name = args
                captured["named"].pop(name, None)
                return None
            if "UPDATE workspaces" in query and "soul_understanding_id = (" in query:
                captured["synced_workspace_columns"] = True
                return None
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
        consolidation_understanding_id=13,
        workspace="james/gpt",
    )

    assert result == {
        "soul_understanding_id": 11,
        "protocol_understanding_id": 12,
        "orientation_understanding_id": None,
        "consolidation_understanding_id": 13,
    }
    assert captured["named"] == {
        "consolidation": 13,
        "protocol": 12,
        "soul": 11,
    }
    assert captured["synced_workspace_columns"] is True
    assert captured["event_args"] == (
        7,
        99,
        "set_workspace_documents",
        json.dumps(
            {
                "soul_understanding_id": 11,
                "protocol_understanding_id": 12,
                "consolidation_understanding_id": 13,
            }
        ),
    )


@pytest.mark.asyncio
async def test_v3_get_named_understandings_reads_requested_names(monkeypatch):
    class FakeConn:
        async def fetchval(self, query, *args):
            if "SELECT id FROM workspaces" in query:
                assert args == ("james/gpt",)
                return 7
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "FROM named_understandings" in query:
                assert args == (7, ["design_note", "protocol"])
                return [{"name": "protocol", "understanding_id": 12}]
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

    result = await tools_module.get_named_understandings(
        names=["design_note", "protocol"],
        workspace="james/gpt",
    )

    assert result == {
        "design_note": None,
        "protocol": 12,
    }


@pytest.mark.asyncio
async def test_v3_set_named_understanding_sets_and_clears_name(monkeypatch):
    captured = {"named": {}}

    class FakeConn:
        async def fetchval(self, query, *args):
            if "SELECT id FROM workspaces" in query:
                assert args == ("james/gpt",)
                return 7
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "SELECT id, content, summary, kind, generation, created_at, superseded_by" in query:
                assert args == ([41],)
                return [
                    {
                        "id": 41,
                        "content": "design note",
                        "summary": "design note",
                        "kind": "relationship",
                        "generation": 0,
                        "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                        "superseded_by": None,
                    }
                ]
            raise AssertionError(query)

        async def fetchrow(self, query, *args):
            if "INSERT INTO sessions" in query:
                assert args == (7, "conversation-42")
                return {"session_id": 99}
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "INSERT INTO named_understandings" in query:
                _, name, understanding_id = args
                captured["named"][name] = understanding_id
                return None
            if "DELETE FROM named_understandings" in query:
                _, name = args
                captured["named"].pop(name, None)
                return None
            if "UPDATE workspaces" in query and "soul_understanding_id = (" in query:
                return None
            if "INSERT INTO events" in query:
                captured.setdefault("events", []).append(args)
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

    created = await tools_module.set_named_understanding(
        "design_note",
        41,
        workspace="james/gpt",
    )
    cleared = await tools_module.set_named_understanding(
        "design_note",
        None,
        workspace="james/gpt",
    )

    assert created == {"name": "design_note", "understanding_id": 41}
    assert cleared == {"name": "design_note", "understanding_id": None}
    assert captured["named"] == {}


@pytest.mark.asyncio
async def test_v3_orient_consolidation_mode_returns_consolidation_document(monkeypatch):
    class FakeConn:
        async def fetchrow(self, query, *args):
            if "FROM workspaces" in query:
                return {
                    "id": 7,
                    "soul_understanding_id": 11,
                    "protocol_understanding_id": 12,
                    "orientation_understanding_id": 14,
                    "consolidation_understanding_id": 13,
                    "last_consolidated_at": None,
                }
            if "INSERT INTO sessions" in query and "RETURNING model_tier" in query:
                return {"model_tier": "gpt-5.4"}
            if "INSERT INTO sessions" in query and "RETURNING session_id" in query:
                return {"session_id": 99}
            if "FROM events e" in query and "finalize_consolidation" in query:
                assert args == (7,)
                return {
                    "timestamp": datetime(2026, 4, 8, 17, 55, tzinfo=timezone.utc),
                    "detail": {
                        "summary": "Consolidated validation subject",
                        "expected_generation": 2,
                        "new_generation": 3,
                        "updated_understanding_ids": [40],
                        "created_understanding_ids": [41],
                    },
                    "session_token": "conversation-41",
                }
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "UPDATE sessions" in query and "seen_set_token = 0" in query:
                return None
            if "INSERT INTO events" in query:
                return None
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "DELETE FROM surfaced_in_session" in query:
                assert args == (7, "conversation-42")
                return []
            if "FROM named_understandings" in query:
                assert args == (7, ["soul", "protocol", "orientation", "consolidation"])
                return [
                    {"name": "soul", "understanding_id": 11},
                    {"name": "protocol", "understanding_id": 12},
                    {"name": "orientation", "understanding_id": 14},
                    {"name": "consolidation", "understanding_id": 13},
                ]
            if "SELECT id, content, summary, kind, generation, created_at, superseded_by" in query:
                assert args == ([11, 12, 14, 13],)
                return [
                    {
                        "id": 11,
                        "content": "soul content",
                        "summary": "soul summary",
                        "kind": "soul",
                        "generation": 0,
                        "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                        "superseded_by": None,
                    },
                    {
                        "id": 12,
                        "content": "protocol content",
                        "summary": "protocol summary",
                        "kind": "protocol",
                        "generation": 0,
                        "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                        "superseded_by": None,
                    },
                    {
                        "id": 13,
                        "content": "consolidation content",
                        "summary": "consolidation summary",
                        "kind": "consolidation",
                        "generation": 0,
                        "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                        "superseded_by": None,
                    },
                    {
                        "id": 14,
                        "content": "orientation content",
                        "summary": "orientation summary",
                        "kind": "orientation",
                        "generation": 0,
                        "created_at": datetime(2026, 4, 2, tzinfo=timezone.utc),
                        "superseded_by": None,
                    },
                ]
            raise AssertionError(query)

        async def fetchval(self, query, *args):
            if "SELECT COUNT(*)" in query and "FROM subjects s" in query:
                return 0
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

    result = await tools_module.orient(
        workspace="james/gpt",
        mode="consolidation",
    )

    assert list(result.keys())[:4] == [
        "soul",
        "consolidation",
        "orientation",
        "last_consolidation_event",
    ]
    assert result["soul"]["content"] == "soul content"
    assert result["consolidation"]["content"] == "consolidation content"
    assert result["orientation"]["content"] == "orientation content"
    assert result["last_consolidation_event"] == {
        "timestamp": "2026-04-08T17:55:00+00:00",
        "summary": "Consolidated validation subject",
        "expected_generation": 2,
        "new_generation": 3,
        "updated_understanding_ids": [40],
        "created_understanding_ids": [41],
        "session_id": "conversation-41",
    }
    assert "protocol" not in result


@pytest.mark.asyncio
async def test_v3_orient_consolidation_mode_parses_string_event_detail(monkeypatch):
    class FakeConn:
        async def fetchrow(self, query, *args):
            if "FROM workspaces" in query:
                return {
                    "id": 7,
                    "soul_understanding_id": None,
                    "protocol_understanding_id": None,
                    "orientation_understanding_id": None,
                    "consolidation_understanding_id": None,
                    "last_consolidated_at": None,
                }
            if "INSERT INTO sessions" in query and "RETURNING model_tier" in query:
                return {"model_tier": "gpt-5.4"}
            if "INSERT INTO sessions" in query and "RETURNING session_id" in query:
                return {"session_id": 99}
            if "FROM events e" in query and "finalize_consolidation" in query:
                assert args == (7,)
                return {
                    "timestamp": datetime(2026, 4, 8, 17, 55, tzinfo=timezone.utc),
                    "detail": json.dumps(
                        {
                            "summary": "String-backed consolidation event",
                            "expected_generation": 0,
                            "new_generation": 1,
                            "updated_understanding_ids": [214],
                            "created_understanding_ids": [],
                        }
                    ),
                    "session_token": "conversation-42",
                }
            raise AssertionError(query)

        async def execute(self, query, *args):
            if "UPDATE sessions" in query and "seen_set_token = 0" in query:
                return None
            if "INSERT INTO events" in query:
                return None
            raise AssertionError(query)

        async def fetch(self, query, *args):
            if "DELETE FROM surfaced_in_session" in query:
                assert args == (7, "conversation-42")
                return []
            if "FROM named_understandings" in query:
                assert args == (7, ["soul", "protocol", "orientation", "consolidation"])
                return []
            if "SELECT id, content, summary, kind, generation, created_at, superseded_by" in query:
                assert args == ([],)
                return []
            raise AssertionError(query)

        async def fetchval(self, query, *args):
            if "SELECT COUNT(*)" in query and "FROM subjects s" in query:
                return 0
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

    result = await tools_module.orient(
        workspace="james/gpt",
        mode="consolidation",
    )

    assert result["last_consolidation_event"] == {
        "timestamp": "2026-04-08T17:55:00+00:00",
        "summary": "String-backed consolidation event",
        "expected_generation": 0,
        "new_generation": 1,
        "updated_understanding_ids": [214],
        "created_understanding_ids": [],
        "session_id": "conversation-42",
    }


@pytest.mark.asyncio
async def test_v3_finalize_consolidation_advances_generation_and_records_event(monkeypatch):
    captured: dict[str, object] = {}

    class FakeConn:
        async def fetchrow(self, query, *args):
            if "UPDATE workspaces" in query and "RETURNING current_generation, last_consolidated_at" in query:
                assert args == (7, 3)
                return {
                    "current_generation": 4,
                    "last_consolidated_at": datetime(2026, 4, 8, 18, 0, tzinfo=timezone.utc),
                }
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

    async def fake_record_event(conn, *, workspace_id, session_id, operation, detail):
        captured["event"] = {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "operation": operation,
            "detail": detail,
        }

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr("memory_v3.tools.get_workspace_generation", fake_get_workspace_generation)
    monkeypatch.setattr("memory_v3.tools.record_event", fake_record_event)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )

    result = await tools_module.finalize_consolidation(
        3,
        "Consolidated memory_system_v3",
        updated_understanding_ids=[12],
        created_understanding_ids=[13, 14],
        workspace="james/gpt",
    )

    assert result == {
        "summary": "Consolidated memory_system_v3",
        "expected_generation": 3,
        "new_generation": 4,
        "updated_understanding_ids": [12],
        "created_understanding_ids": [13, 14],
        "last_consolidated_at": "2026-04-08T18:00:00+00:00",
    }
    assert captured["event"] == {
        "workspace_id": 7,
        "session_id": "conversation-42",
        "operation": "finalize_consolidation",
        "detail": {
            "summary": "Consolidated memory_system_v3",
            "expected_generation": 3,
            "new_generation": 4,
            "updated_understanding_ids": [12],
            "created_understanding_ids": [13, 14],
        },
    }


@pytest.mark.asyncio
async def test_v3_finalize_consolidation_rejects_generation_mismatch(monkeypatch):
    class FakeConn:
        async def fetchrow(self, query, *args):
            raise AssertionError(f"unexpected fetchrow: {query} {args}")

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
        return 4

    monkeypatch.setattr("memory_v3.tools.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.tools.resolve_workspace_id", fake_resolve_workspace_id)
    monkeypatch.setattr("memory_v3.tools.get_workspace_generation", fake_get_workspace_generation)
    monkeypatch.setattr(
        "memory_v3.tools.resolve_optional_session_id",
        lambda session_id=None: "conversation-42",
    )

    with pytest.raises(
        ValueError,
        match="Consolidation generation mismatch: expected 3, current 4",
    ):
        await tools_module.finalize_consolidation(
            3,
            "Consolidated memory_system_v3",
            workspace="james/gpt",
        )
