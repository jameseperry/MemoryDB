"""Shared MCP transport host for multiple memory server variants."""

import copy
from dataclasses import dataclass

from fastmcp import FastMCP
from fastmcp.server.http import create_sse_app
from fastmcp.utilities.lifespan import combine_lifespans
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send
import uvicorn


class RequireWorkspaceHeaderMiddleware:
    """Reject HTTP MCP requests that do not declare a workspace."""

    def __init__(self, app, workspace_header: str = "X-Memory-Workspace"):
        self.app = app
        self.workspace_header = workspace_header

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope)
            workspace = request.headers.get(self.workspace_header)
            if workspace is None or not workspace.strip():
                response = JSONResponse(
                    {"error": f"Missing required header: {self.workspace_header}"},
                    status_code=400,
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)


@dataclass(frozen=True)
class MountedMCPApp:
    """A FastMCP app mounted at explicit HTTP/SSE transport paths."""

    name: str
    server: FastMCP
    streamable_http_path: str
    sse_path: str
    sse_message_path: str


class TransportMux:
    """Dispatch requests to one of several mounted MCP transport apps."""

    def __init__(self, app, mounts: list[tuple[str, object, object]]):
        self.app = app
        self.mounts = mounts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        for base_path, streamable_http_app, sse_app in self.mounts:
            streamable_prefix = base_path + "/mcp"
            sse_prefix = base_path + "/sse"
            if path == streamable_prefix or path.startswith(streamable_prefix + "/"):
                await streamable_http_app(scope, receive, send)
                return
            if path == sse_prefix or path.startswith(sse_prefix + "/"):
                await sse_app(scope, receive, _drop_duplicate_response_start(send))
                return

        response = JSONResponse({"error": "Not found"}, status_code=404)
        await response(scope, receive, send)


def _drop_duplicate_response_start(send: Send) -> Send:
    """Ignore duplicate response-start frames emitted by FastMCP SSE teardown.

    FastMCP's SSE route can return a trailing Starlette Response after the SSE
    transport has already started the stream. Uvicorn then raises because it
    expects only response-body frames. We drop that duplicate empty response
    tail while preserving normal ASGI traffic.
    """
    response_started = False
    suppress_duplicate_tail = False

    async def guarded_send(message) -> None:
        nonlocal response_started, suppress_duplicate_tail

        if suppress_duplicate_tail:
            if message["type"] == "http.response.body":
                return
            suppress_duplicate_tail = False

        if message["type"] == "http.response.start":
            if response_started:
                suppress_duplicate_tail = True
                return
            response_started = True

        await send(message)

    return guarded_send


def build_host(
    mounts: list[MountedMCPApp],
    workspace_header: str,
) -> Starlette:
    """Build one Starlette host serving several FastMCP apps under different paths."""

    transport_mounts: list[tuple[str, object, object]] = []
    lifespans = []

    for mount in mounts:
        middleware = [
            Middleware(
                RequireWorkspaceHeaderMiddleware,
                workspace_header=workspace_header,
            )
        ]
        streamable_http_app = mount.server.http_app(
            path=mount.streamable_http_path,
            transport="streamable-http",
            middleware=middleware,
        )
        sse_app = create_sse_app(
            server=mount.server,
            message_path=mount.sse_message_path,
            sse_path=mount.sse_path,
            middleware=middleware,
        )
        base_path = mount.streamable_http_path.removesuffix("/mcp")
        transport_mounts.append((base_path, streamable_http_app, sse_app))
        lifespans.extend([streamable_http_app.lifespan, sse_app.lifespan])

    return Starlette(
        routes=[],
        middleware=[Middleware(TransportMux, mounts=transport_mounts)],
        lifespan=combine_lifespans(*lifespans),
    )


def run_host(
    app: Starlette,
    *,
    host: str,
    port: int,
) -> None:
    """Run a shared Memory MCP host under Uvicorn."""

    log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    log_config["loggers"]["memory_mcp"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }
    log_config["loggers"]["memory_v3"] = {
        "handlers": ["default"],
        "level": "INFO",
        "propagate": False,
    }

    uvicorn.run(
        app,
        host=host,
        port=port,
        lifespan="on",
        log_config=log_config,
    )
