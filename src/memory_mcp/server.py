"""Combined Memory MCP host entry point.

Serves the current v1 API under `/mcp` and `/sse`, and the in-progress v3 API
under `/v3/mcp` and `/v3/sse`.
"""

from starlette.applications import Starlette

from memory_common.server_host import (
    MountedMCPApp,
    RequireWorkspaceHeaderMiddleware,
    build_host,
    run_host,
)
from memory_mcp.app import create_mcp_server
from memory_mcp.config import settings
from memory_v3.app import create_mcp_server as create_v3_mcp_server


def build_app() -> Starlette:
    """Build the combined v1/v3 Memory MCP host."""
    mounts = [
        MountedMCPApp(
            name="memory-v1",
            server=create_mcp_server(),
            streamable_http_path="/mcp",
            sse_path="/sse",
            sse_message_path="/sse/messages/",
        ),
        MountedMCPApp(
            name="memory-v3",
            server=create_v3_mcp_server(),
            streamable_http_path="/v3/mcp",
            sse_path="/v3/sse",
            sse_message_path="/v3/sse/messages/",
        ),
    ]
    return build_host(mounts, workspace_header=settings.mcp_workspace_header)


def main() -> None:
    run_host(
        build_app(),
        host="0.0.0.0",
        port=settings.mcp_port,
    )


__all__ = [
    "RequireWorkspaceHeaderMiddleware",
    "build_app",
    "main",
]


if __name__ == "__main__":
    main()
