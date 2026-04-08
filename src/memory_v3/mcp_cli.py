"""Small CLI for dispatching MCP tool calls to configured servers."""

from __future__ import annotations

import json
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import anyio
import click
from fastmcp import Client
from fastmcp.client.transports.http import StreamableHttpTransport


def _json_default(value: Any) -> str:
    return str(value)


def _parse_json(value: str | None, *, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value)


def _parse_headers(header_items: tuple[str, ...]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in header_items:
        if "=" not in item:
            raise ValueError(f"Invalid header '{item}'. Expected KEY=VALUE.")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid header '{item}'. Header name is required.")
        headers[key] = value
    return headers


def _load_server_config(config_path: Path, server_name: str) -> tuple[str, dict[str, str]]:
    if not config_path.is_file():
        raise ValueError(f"Codex config not found: {config_path}")
    payload = tomllib.loads(config_path.read_text(encoding="utf-8"))
    servers = payload.get("mcp_servers", {})
    if server_name not in servers:
        raise ValueError(f"MCP server '{server_name}' not found in {config_path}")
    server = servers[server_name]
    if not server.get("enabled", True):
        raise ValueError(f"MCP server '{server_name}' is disabled in {config_path}")
    url = server.get("url")
    if not url:
        raise ValueError(f"MCP server '{server_name}' is missing a url in {config_path}")
    headers = dict(server.get("http_headers", {}))
    return url, headers


def _resolve_target(
    *,
    server: str | None,
    url: str | None,
    workspace: str | None,
    header_items: tuple[str, ...],
    config_path: Path,
) -> tuple[str, dict[str, str]]:
    resolved_headers = _parse_headers(header_items)
    resolved_url = url

    if server is not None:
        config_url, config_headers = _load_server_config(config_path, server)
        resolved_url = resolved_url or config_url
        for key, value in config_headers.items():
            resolved_headers.setdefault(key, value)

    if resolved_url is None:
        raise ValueError("Provide either --server or --url")

    if workspace is not None:
        resolved_headers["X-Memory-Workspace"] = workspace

    return resolved_url, resolved_headers


async def _list_tools(url: str, headers: dict[str, str]) -> list[dict[str, str | None]]:
    transport = StreamableHttpTransport(url=url, headers=headers)
    async with Client(transport, timeout=10) as client:
        tools = await client.list_tools()
    results = []
    for tool in tools:
        results.append(
            {
                "name": getattr(tool, "name", None),
                "description": getattr(tool, "description", None),
            }
        )
    return results


async def _call_tool(
    url: str,
    headers: dict[str, str],
    tool_name: str,
    arguments: dict[str, Any],
) -> Any:
    transport = StreamableHttpTransport(url=url, headers=headers)
    async with Client(transport, timeout=30) as client:
        result = await client.call_tool(tool_name, arguments)
    if result.is_error:
        raise RuntimeError(str(result))
    return result.data


def _emit_result(result: Any, *, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(result, default=_json_default))
        return
    if isinstance(result, list) and all(isinstance(item, dict) for item in result):
        for item in result:
            name = item.get("name") or "<unnamed>"
            description = item.get("description") or ""
            if description:
                click.echo(f"{name}\t{description}")
            else:
                click.echo(str(name))
        return
    if isinstance(result, (dict, list)):
        click.echo(json.dumps(result, indent=2, default=_json_default))
        return
    click.echo(str(result))


@click.group()
@click.option("--server", default=None, help="Server name from ~/.codex/config.toml.")
@click.option("--url", default=None, help="Raw MCP URL, e.g. http://127.0.0.1:8765/v3/mcp")
@click.option("--workspace", default=None, help="Workspace header override.")
@click.option("--header", "header_items", multiple=True, help="Extra header as KEY=VALUE.")
@click.option(
    "--config",
    "config_path",
    default="~/.codex/config.toml",
    show_default=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Codex config file to read when using --server.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def cli(
    ctx: click.Context,
    server: str | None,
    url: str | None,
    workspace: str | None,
    header_items: tuple[str, ...],
    config_path: Path,
    as_json: bool,
) -> None:
    ctx.ensure_object(dict)
    resolved_url, resolved_headers = _resolve_target(
        server=server,
        url=url,
        workspace=workspace,
        header_items=header_items,
        config_path=config_path.expanduser(),
    )
    ctx.obj["url"] = resolved_url
    ctx.obj["headers"] = resolved_headers
    ctx.obj["as_json"] = as_json


@cli.command("list-tools")
@click.pass_context
def list_tools(ctx: click.Context) -> None:
    result = anyio.run(_list_tools, ctx.obj["url"], ctx.obj["headers"])
    _emit_result(result, as_json=ctx.obj["as_json"])


@cli.command("call")
@click.argument("tool_name")
@click.argument("arguments", required=False)
@click.pass_context
def call_tool(ctx: click.Context, tool_name: str, arguments: str | None) -> None:
    parsed_arguments = _parse_json(arguments, default={})
    if not isinstance(parsed_arguments, dict):
        raise ValueError("Tool arguments must be a JSON object")
    result = anyio.run(
        _call_tool,
        ctx.obj["url"],
        ctx.obj["headers"],
        tool_name,
        parsed_arguments,
    )
    _emit_result(result, as_json=ctx.obj["as_json"])


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = cli.main(
            args=list(argv) if argv is not None else None,
            prog_name="memory-mcp-cli",
            standalone_mode=False,
        )
        return int(result) if isinstance(result, int) else 0
    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
        click.echo(str(exc), err=True)
        return 1
    except click.ClickException as exc:
        exc.show(file=sys.stderr)
        return exc.exit_code
    except click.exceptions.Exit as exc:
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
