"""Memory MCP server entry point."""

import copy
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastmcp import FastMCP
from fastmcp.server.http import create_sse_app
from fastmcp.utilities.lifespan import combine_lifespans
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send
import uvicorn

from memory_mcp.db import close_pool, init_pool
from memory_mcp.config import settings
from memory_mcp import mcp_tools


class RequireWorkspaceHeaderMiddleware:
    """Reject HTTP MCP requests that do not declare a workspace."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope)
            workspace = request.headers.get(settings.mcp_workspace_header)
            if workspace is None or not workspace.strip():
                response = JSONResponse(
                    {
                        "error": (
                            f"Missing required header: {settings.mcp_workspace_header}"
                        )
                    },
                    status_code=400,
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[None]:
    await init_pool()
    try:
        yield
    finally:
        await close_pool()


mcp = FastMCP(
    name="memory",
    instructions=(
        "Persistent memory graph backed by Postgres + pgvector. "
        "Stores named nodes with typed observations and directed relations. "
        "Supports semantic search, graph traversal, and consolidation workflows."
    ),
    lifespan=lifespan,
)

# --- Register all tools ---

mcp.add_tool(mcp_tools.create_entities)
mcp.add_tool(mcp_tools.delete_entities)
mcp.add_tool(mcp_tools.open_nodes)
mcp.add_tool(mcp_tools.get_nodes_by_type)
mcp.add_tool(mcp_tools.get_recently_modified)
mcp.add_tool(mcp_tools.set_summary)
mcp.add_tool(mcp_tools.set_tags)

mcp.add_tool(mcp_tools.add_observations)
mcp.add_tool(mcp_tools.replace_observation)
mcp.add_tool(mcp_tools.delete_observations)
mcp.add_tool(mcp_tools.query_observations)

mcp.add_tool(mcp_tools.create_relations)
mcp.add_tool(mcp_tools.delete_relations)
mcp.add_tool(mcp_tools.update_relation_type)
mcp.add_tool(mcp_tools.get_relations_between)

mcp.add_tool(mcp_tools.get_neighborhood)
mcp.add_tool(mcp_tools.get_path)
mcp.add_tool(mcp_tools.get_orphans)
mcp.add_tool(mcp_tools.get_relation_gaps)
mcp.add_tool(mcp_tools.find_similar_nodes)

mcp.add_tool(mcp_tools.search_nodes)

mcp.add_tool(mcp_tools.get_consolidation_report)
mcp.add_tool(mcp_tools.get_pending_consolidation)
mcp.add_tool(mcp_tools.get_stats)


class TransportMux:
    """Dispatch requests to the appropriate MCP transport app."""

    def __init__(self, app, streamable_http_app, sse_app):
        self.app = app
        self.streamable_http_app = streamable_http_app
        self.sse_app = sse_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path == "/mcp" or path.startswith("/mcp/"):
            await self.streamable_http_app(scope, receive, send)
            return
        if path == "/sse" or path.startswith("/sse/"):
            await self.sse_app(scope, receive, send)
            return

        response = JSONResponse({"error": "Not found"}, status_code=404)
        await response(scope, receive, send)


def build_app() -> Starlette:
    middleware = [Middleware(RequireWorkspaceHeaderMiddleware)]
    streamable_http_app = mcp.http_app(
        path="/mcp",
        transport="streamable-http",
        middleware=middleware,
    )
    sse_app = create_sse_app(
        server=mcp,
        message_path="/sse/messages/",
        sse_path="/sse",
        middleware=middleware,
    )

    return Starlette(
        routes=[],
        middleware=[Middleware(TransportMux, streamable_http_app, sse_app)],
        lifespan=combine_lifespans(
            streamable_http_app.lifespan,
            sse_app.lifespan,
        ),
    )


def main() -> None:
    log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    log_config["loggers"]["memory_mcp"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    uvicorn.run(
        build_app(),
        host="0.0.0.0",
        port=settings.mcp_port,
        lifespan="on",
        log_config=log_config,
    )


if __name__ == "__main__":
    main()
