"""Shared MCP transport host for multiple memory server variants."""

import copy
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode
from uuid import uuid4

import anyio
from fastmcp import FastMCP
from fastmcp.server.http import create_base_app
from fastmcp.utilities.lifespan import combine_lifespans
from mcp.server.sse import SseServerTransport
from sse_starlette import EventSourceResponse
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Mount, Route
from starlette.types import Receive, Scope, Send
import uvicorn


class RequireWorkspaceHeaderMiddleware:
    """Reject HTTP MCP requests that do not declare a workspace."""

    def __init__(
        self,
        app,
        workspace_header: str = "X-Memory-Workspace",
        workspace_query_param: str = "workspace",
    ):
        self.app = app
        self.workspace_header = workspace_header
        self.workspace_query_param = workspace_query_param

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope)
            workspace = request.headers.get(self.workspace_header)
            query_workspace = request.query_params.get(self.workspace_query_param)

            if workspace is not None:
                workspace = workspace.strip()
                if not workspace:
                    response = JSONResponse(
                        {"error": f"{self.workspace_header} header cannot be empty"},
                        status_code=400,
                    )
                    await response(scope, receive, send)
                    return

            if query_workspace is not None:
                query_workspace = query_workspace.strip()
                if not query_workspace:
                    response = JSONResponse(
                        {
                            "error": (
                                f"{self.workspace_query_param} query parameter cannot be empty"
                            )
                        },
                        status_code=400,
                    )
                    await response(scope, receive, send)
                    return

            if workspace is not None and query_workspace is not None:
                if workspace != query_workspace:
                    response = JSONResponse(
                        {
                            "error": (
                                f"{self.workspace_header} header does not match "
                                f"{self.workspace_query_param} query parameter"
                            )
                        },
                        status_code=400,
                    )
                    await response(scope, receive, send)
                    return

            if workspace is None:
                workspace = query_workspace

            if workspace is None or not workspace.strip():
                response = JSONResponse(
                    {
                        "error": (
                            f"Missing required header/query parameter: "
                            f"{self.workspace_header} or {self.workspace_query_param}"
                        )
                    },
                    status_code=400,
                )
                await response(scope, receive, send)
                return

            if request.headers.get(self.workspace_header) is None:
                scope["headers"] = list(scope["headers"]) + [
                    (
                        self.workspace_header.lower().encode("latin-1"),
                        workspace.encode("latin-1"),
                    )
                ]

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


class WorkspaceQuerySseServerTransport(SseServerTransport):
    """SSE transport that carries workspace query params into POST endpoint URLs."""

    def __init__(
        self,
        endpoint: str,
        workspace_query_param: str = "workspace",
    ) -> None:
        super().__init__(endpoint)
        self._workspace_query_param = workspace_query_param

    @asynccontextmanager
    async def connect_sse(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            logging.error("connect_sse received non-HTTP request")
            raise ValueError("connect_sse can only handle HTTP requests")

        request = Request(scope, receive)
        error_response = await self._security.validate_request(request, is_post=False)
        if error_response:
            await error_response(scope, receive, send)
            raise ValueError("Request validation failed")

        read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

        session_id = uuid4()
        self._read_stream_writers[session_id] = read_stream_writer

        root_path = scope.get("root_path", "")
        full_message_path_for_client = root_path.rstrip("/") + self._endpoint
        query_params = {"session_id": session_id.hex}
        workspace = request.query_params.get(self._workspace_query_param)
        if workspace is not None and workspace.strip():
            query_params[self._workspace_query_param] = workspace.strip()
        client_post_uri_data = (
            f"{quote(full_message_path_for_client)}?{urlencode(query_params)}"
        )

        sse_stream_writer, sse_stream_reader = anyio.create_memory_object_stream[
            dict[str, Any]
        ](0)

        async def sse_writer():
            async with sse_stream_writer, write_stream_reader:
                await sse_stream_writer.send(
                    {"event": "endpoint", "data": client_post_uri_data}
                )

                async for session_message in write_stream_reader:
                    await sse_stream_writer.send(
                        {
                            "event": "message",
                            "data": session_message.message.model_dump_json(
                                by_alias=True,
                                exclude_none=True,
                            ),
                        }
                    )

        async with anyio.create_task_group() as tg:

            async def response_wrapper(
                scope: Scope,
                receive: Receive,
                send: Send,
            ):
                await EventSourceResponse(
                    content=sse_stream_reader,
                    data_sender_callable=sse_writer,
                )(scope, receive, send)
                await read_stream_writer.aclose()
                await write_stream_reader.aclose()

            tg.start_soon(response_wrapper, scope, receive, send)
            yield (read_stream, write_stream)


def create_workspace_sse_app(
    server: FastMCP,
    message_path: str,
    sse_path: str,
    middleware: list[Middleware],
    workspace_query_param: str = "workspace",
):
    """Create an SSE transport app that preserves workspace query parameters."""
    sse = WorkspaceQuerySseServerTransport(
        message_path,
        workspace_query_param=workspace_query_param,
    )

    async def handle_sse(scope: Scope, receive: Receive, send: Send) -> Response:
        async with sse.connect_sse(scope, receive, send) as streams:
            await server._mcp_server.run(
                streams[0],
                streams[1],
                server._mcp_server.create_initialization_options(),
            )
        return Response()

    async def sse_endpoint(request: Request) -> Response:
        return await handle_sse(request.scope, request.receive, request._send)

    routes: list[BaseRoute] = [
        Route(sse_path, endpoint=sse_endpoint, methods=["GET"]),
        Mount(message_path, app=sse.handle_post_message),
    ]
    routes.extend(server._get_additional_http_routes())

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        async with server._lifespan_manager():
            yield

    app = create_base_app(routes=routes, middleware=middleware, lifespan=lifespan)
    app.state.fastmcp_server = server
    app.state.path = sse_path
    app.state.transport_type = "sse"
    return app


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
                workspace_query_param="workspace",
            )
        ]
        streamable_http_app = mount.server.http_app(
            path=mount.streamable_http_path,
            transport="streamable-http",
            middleware=middleware,
        )
        sse_app = create_workspace_sse_app(
            server=mount.server,
            message_path=mount.sse_message_path,
            sse_path=mount.sse_path,
            middleware=middleware,
            workspace_query_param="workspace",
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
