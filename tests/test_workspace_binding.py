"""Tests for workspace binding via X-Memory-Workspace."""

import inspect

import pytest
import pytest_asyncio
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from memory_mcp import mcp_tools
from memory_mcp.config import settings
from memory_mcp.db import resolve_effective_workspace_name
from memory_mcp.server import RequireWorkspaceHeaderMiddleware
from memory_mcp.tools import consolidation


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
        "memory_mcp.db.get_http_headers",
        lambda: {settings.mcp_workspace_header.lower(): "james/codex"},
    )
    assert resolve_effective_workspace_name(None) == "james/codex"


def test_resolve_effective_workspace_rejects_mismatch(monkeypatch):
    monkeypatch.setattr(
        "memory_mcp.db.get_http_headers",
        lambda: {settings.mcp_workspace_header.lower(): "james/codex"},
    )
    with pytest.raises(ValueError, match="does not match"):
        resolve_effective_workspace_name("audrey/claude")


def test_resolve_effective_workspace_allows_direct_calls_without_header(monkeypatch):
    monkeypatch.setattr("memory_mcp.db.get_http_headers", lambda: {})
    assert resolve_effective_workspace_name("james/codex") == "james/codex"
    with pytest.raises(ValueError, match="Workspace is required"):
        resolve_effective_workspace_name(None)


def test_resolve_effective_workspace_rejects_empty_header(monkeypatch):
    monkeypatch.setattr(
        "memory_mcp.db.get_http_headers",
        lambda: {settings.mcp_workspace_header.lower(): "   "},
    )
    with pytest.raises(ValueError, match="cannot be empty"):
        resolve_effective_workspace_name(None)


async def _ok(_request):
    return PlainTextResponse("ok")


async def _invoke_http_app(app, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    messages = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/sse",
        "raw_path": b"/sse",
        "query_string": b"",
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


def test_mcp_wrappers_do_not_expose_workspace():
    wrappers = [
        mcp_tools.create_entities,
        mcp_tools.delete_entities,
        mcp_tools.open_nodes,
        mcp_tools.get_nodes_by_type,
        mcp_tools.get_recently_modified,
        mcp_tools.set_summary,
        mcp_tools.set_tags,
        mcp_tools.add_observations,
        mcp_tools.replace_observation,
        mcp_tools.delete_observations,
        mcp_tools.query_observations,
        mcp_tools.create_relations,
        mcp_tools.delete_relations,
        mcp_tools.update_relation_type,
        mcp_tools.get_relations_between,
        mcp_tools.get_neighborhood,
        mcp_tools.get_path,
        mcp_tools.get_orphans,
        mcp_tools.get_relation_gaps,
        mcp_tools.find_similar_nodes,
        mcp_tools.search_nodes,
        mcp_tools.get_consolidation_report,
        mcp_tools.get_pending_consolidation,
        mcp_tools.get_stats,
    ]

    for wrapper in wrappers:
        assert "workspace" not in inspect.signature(wrapper).parameters


@pytest.mark.asyncio
async def test_get_stats_returns_effective_workspace(monkeypatch):
    class FakeConn:
        async def fetchrow(self, _query, _workspace_id):
            return {
                "node_count": 0,
                "observation_count": 0,
                "relation_count": 0,
                "embedding_coverage": None,
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
        "memory_mcp.tools.consolidation.resolve_effective_workspace_name",
        lambda workspace: "james/gpt",
    )
    monkeypatch.setattr(
        "memory_mcp.tools.consolidation.resolve_workspace_id",
        fake_resolve_workspace_id,
    )
    monkeypatch.setattr(
        "memory_mcp.tools.consolidation.get_pool",
        fake_get_pool,
    )

    result = await consolidation.get_stats()

    assert result["workspace"] == "james/gpt"
