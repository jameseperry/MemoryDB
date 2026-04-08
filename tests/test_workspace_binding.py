"""Tests for workspace binding via X-Memory-Workspace."""

import inspect

import pytest
import pytest_asyncio
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from memory_common.server_host import (
    RequireWorkspaceHeaderMiddleware,
    _drop_duplicate_response_start,
)
from memory_v3 import mcp_tools
from memory_v3.config import settings
from memory_v3.db import resolve_effective_workspace_name
from memory_v3 import tools


@pytest_asyncio.fixture(scope="session")
async def db_pool():
    """Override the global DB fixture: these tests do not touch Postgres."""
    yield


@pytest_asyncio.fixture(autouse=True)
async def isolated_workspace():
    """Override the global isolation fixture: these tests do not touch Postgres."""
    yield


def test_resolve_effective_workspace_uses_header(monkeypatch):
    monkeypatch.setattr(
        "memory_v3.db.get_http_headers",
        lambda: {settings.mcp_workspace_header.lower(): "james/codex"},
    )
    assert resolve_effective_workspace_name(None) == "james/codex"


def test_resolve_effective_workspace_rejects_mismatch(monkeypatch):
    monkeypatch.setattr(
        "memory_v3.db.get_http_headers",
        lambda: {settings.mcp_workspace_header.lower(): "james/codex"},
    )
    with pytest.raises(ValueError, match="does not match"):
        resolve_effective_workspace_name("audrey/claude")


def test_resolve_effective_workspace_allows_direct_calls_without_header(monkeypatch):
    monkeypatch.setattr("memory_v3.db.get_http_headers", lambda: {})
    assert resolve_effective_workspace_name("james/codex") == "james/codex"
    with pytest.raises(ValueError, match="Workspace is required"):
        resolve_effective_workspace_name(None)


def test_resolve_effective_workspace_rejects_empty_header(monkeypatch):
    monkeypatch.setattr(
        "memory_v3.db.get_http_headers",
        lambda: {settings.mcp_workspace_header.lower(): "   "},
    )
    with pytest.raises(ValueError, match="cannot be empty"):
        resolve_effective_workspace_name(None)


async def _ok(_request):
    return PlainTextResponse("ok")


async def _invoke_http_app(
    app,
    headers: dict[str, str] | None = None,
    query_string: bytes = b"",
) -> tuple[int, bytes]:
    messages = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/sse",
        "raw_path": b"/sse",
        "query_string": query_string,
        "headers": [
            (k.lower().encode("latin-1"), v.encode("latin-1"))
            for k, v in (headers or {}).items()
        ],
        "client": ("testclient", 12345),
        "server": ("testserver", 80),
        "app": app,
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)

    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, body


@pytest.mark.asyncio
async def test_workspace_header_required_by_http_middleware():
    app = Starlette(
        routes=[Route("/sse", _ok)],
        middleware=[Middleware(RequireWorkspaceHeaderMiddleware)],
    )
    status, body = await _invoke_http_app(app)

    assert status == 400
    assert settings.mcp_workspace_header.encode("utf-8") in body


@pytest.mark.asyncio
async def test_workspace_header_allows_request():
    app = Starlette(
        routes=[Route("/sse", _ok)],
        middleware=[Middleware(RequireWorkspaceHeaderMiddleware)],
    )
    status, body = await _invoke_http_app(
        app,
        headers={settings.mcp_workspace_header: "james/codex"},
    )

    assert status == 200
    assert body == b"ok"


@pytest.mark.asyncio
async def test_workspace_query_parameter_allows_request():
    app = Starlette(
        routes=[Route("/sse", _ok)],
        middleware=[Middleware(RequireWorkspaceHeaderMiddleware)],
    )
    status, body = await _invoke_http_app(
        app,
        query_string=b"workspace=james%2Fcodex",
    )

    assert status == 200
    assert body == b"ok"


@pytest.mark.asyncio
async def test_workspace_header_and_query_parameter_must_match():
    app = Starlette(
        routes=[Route("/sse", _ok)],
        middleware=[Middleware(RequireWorkspaceHeaderMiddleware)],
    )
    status, body = await _invoke_http_app(
        app,
        headers={settings.mcp_workspace_header: "james/gpt"},
        query_string=b"workspace=audrey%2Fclaude",
    )

    assert status == 400
    assert b"does not match" in body


def test_mcp_wrappers_do_not_expose_workspace():
    wrappers = [
        mcp_tools.orient,
        mcp_tools.bring_to_mind,
        mcp_tools.recall,
        mcp_tools.reset_seen,
        mcp_tools.set_session_model_tier,
        mcp_tools.get_workspace_documents,
        mcp_tools.set_workspace_documents,
        mcp_tools.remember,
        mcp_tools.update_understanding,
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
        mcp_tools.get_status,
    ]

    for wrapper in wrappers:
        assert "workspace" not in inspect.signature(wrapper).parameters


def test_mcp_wrapper_logs_tool_workspace_and_session(monkeypatch, caplog):
    class FakeContext:
        session_id = "session-123"

    monkeypatch.setattr(
        "memory_v3.mcp_tools.resolve_effective_workspace_name",
        lambda workspace: "james/gpt",
    )
    monkeypatch.setattr(
        "memory_v3.mcp_tools.get_context",
        lambda: FakeContext(),
    )

    with caplog.at_level("INFO"):
        mcp_tools._log_tool_call("get_stats")

    assert "tool=get_stats" in caplog.text
    assert "workspace=james/gpt" in caplog.text
    assert "session_id=session-123" in caplog.text


@pytest.mark.asyncio
async def test_get_stats_returns_effective_workspace(monkeypatch):
    class FakeConn:
        async def fetchrow(self, _query, _workspace_id):
            return {
                "subject_count": 0,
                "observation_count": 0,
                "understanding_count": 0,
                "embedding_coverage": None,
                "current_generation": 0,
            }

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

    monkeypatch.setattr(
        "memory_v3.tools.resolve_effective_workspace_name",
        lambda workspace: "james/gpt",
    )
    monkeypatch.setattr(
        "memory_v3.tools.resolve_workspace_id",
        fake_resolve_workspace_id,
    )
    monkeypatch.setattr(
        "memory_v3.tools.get_pool",
        fake_get_pool,
    )

    result = await tools.get_stats()

    assert result["workspace"] == "james/gpt"


@pytest.mark.asyncio
async def test_drop_duplicate_response_start_suppresses_sse_teardown_tail():
    sent = []

    async def send(message):
        sent.append(message)

    guarded_send = _drop_duplicate_response_start(send)

    await guarded_send({"type": "http.response.start", "status": 200, "headers": []})
    await guarded_send({"type": "http.response.body", "body": b"chunk", "more_body": True})
    await guarded_send({"type": "http.response.start", "status": 200, "headers": []})
    await guarded_send({"type": "http.response.body", "body": b"", "more_body": False})

    assert sent == [
        {"type": "http.response.start", "status": 200, "headers": []},
        {"type": "http.response.body", "body": b"chunk", "more_body": True},
    ]
