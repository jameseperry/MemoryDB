"""Memory MCP v3 host entry point."""

from starlette.applications import Starlette

from memory_common.server_host import (
    MountedMCPApp,
    RequireWorkspaceHeaderMiddleware,
    build_host,
    run_host,
)
from memory_v3.app import create_mcp_server
from memory_v3.config import settings


def build_app() -> Starlette:
    """Build the v3-only Memory MCP host."""
    mounts = [
        MountedMCPApp(
            name="memory-v3",
            server=create_mcp_server(),
            streamable_http_path="/v3/mcp",
            sse_path="/v3/sse",
            sse_message_path="/v3/sse/messages/",
        ),
    ]
    return build_host(mounts, workspace_header=settings.mcp_workspace_header)


def main() -> None:
    """Run the v3-only Memory MCP host."""
    run_host(
        build_app(),
        host=settings.mcp_host,
        port=settings.mcp_port,
    )


__all__ = [
    "RequireWorkspaceHeaderMiddleware",
    "build_app",
    "main",
]


if __name__ == "__main__":
    main()
