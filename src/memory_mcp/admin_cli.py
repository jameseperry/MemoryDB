"""CLI for administrative tasks outside the MCP protocol."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import datetime

import click

from memory_mcp.admin import (
    backup_database,
    create_workspace,
    delete_workspace,
    list_workspaces,
    rehome_null_workspace_nodes,
    restore_database,
)
from memory_mcp.db import close_pool, init_pool


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


async def _run_with_pool(coro):
    await init_pool()
    try:
        return await coro
    finally:
        await close_pool()


def _emit_result(result, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(result, default=_json_default))
    elif isinstance(result, list):
        for workspace in result:
            click.echo(workspace["name"])


@click.group()
@click.option("--json", "as_json", is_flag=True, help="Output JSON.")
@click.pass_context
def cli(ctx: click.Context, as_json: bool) -> None:
    ctx.ensure_object(dict)
    ctx.obj["as_json"] = as_json


@cli.group()
def workspace() -> None:
    """Workspace management commands."""


@workspace.command("list")
@click.pass_context
def workspace_list(ctx: click.Context) -> None:
    as_json = ctx.find_root().obj["as_json"]
    result = asyncio.run(_run_with_pool(list_workspaces()))
    _emit_result(result, as_json)


@workspace.command("create")
@click.argument("name")
@click.pass_context
def workspace_create(ctx: click.Context, name: str) -> None:
    as_json = ctx.find_root().obj["as_json"]
    result = asyncio.run(_run_with_pool(create_workspace(name)))
    if as_json:
        _emit_result(result, as_json=True)
        return
    if result["created"]:
        click.echo(f"created workspace: {result['name']}")
    else:
        click.echo(f"workspace already exists: {result['name']}")


@workspace.command("delete")
@click.argument("name")
@click.pass_context
def workspace_delete(ctx: click.Context, name: str) -> None:
    as_json = ctx.find_root().obj["as_json"]
    result = asyncio.run(_run_with_pool(delete_workspace(name)))
    if as_json:
        _emit_result(result, as_json=True)
        if not result["deleted"]:
            raise click.exceptions.Exit(1)
        return
    if result["deleted"]:
        click.echo(f"deleted workspace: {result['name']}")
        return
    click.echo(f"workspace not found: {result['name']}", err=True)
    raise click.exceptions.Exit(1)


@workspace.command("rehome-null")
@click.argument("name")
@click.pass_context
def workspace_rehome_null(ctx: click.Context, name: str) -> None:
    as_json = ctx.find_root().obj["as_json"]
    result = asyncio.run(_run_with_pool(rehome_null_workspace_nodes(name)))
    if as_json:
        _emit_result(result, as_json=True)
        return
    click.echo(
        "re-homed NULL-workspace rows to "
        f"{result['workspace']}: "
        f"nodes={result['nodes_rehomed']}, "
        f"relations={result['relations_rehomed']}, "
        f"events={result['events_rehomed']}"
    )


@cli.group()
def database() -> None:
    """Database backup and restore commands."""


@database.command("backup")
@click.argument("path", type=click.Path(dir_okay=False, path_type=str))
@click.option(
    "--method",
    type=click.Choice(["auto", "local", "docker"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="How to run PostgreSQL tooling.",
)
@click.pass_context
def database_backup(ctx: click.Context, path: str, method: str) -> None:
    as_json = ctx.find_root().obj["as_json"]
    result = backup_database(path, method=method)
    if as_json:
        _emit_result(result, as_json=True)
        return
    click.echo(f"backed up database to: {result['path']} ({result['method']})")


@database.command("restore")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=str))
@click.option(
    "--method",
    type=click.Choice(["auto", "local", "docker"], case_sensitive=False),
    default="auto",
    show_default=True,
    help="How to run PostgreSQL tooling.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Skip the destructive restore confirmation prompt.",
)
@click.pass_context
def database_restore(ctx: click.Context, path: str, method: str, yes: bool) -> None:
    as_json = ctx.find_root().obj["as_json"]
    if not yes:
        click.confirm(
            f"Restore database from {path}? This will overwrite current database contents.",
            abort=True,
        )
    result = restore_database(path, method=method)
    if as_json:
        _emit_result(result, as_json=True)
        return
    click.echo(f"restored database from: {result['path']} ({result['method']})")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        cli.main(args=list(argv) if argv is not None else None, prog_name="memory-admin", standalone_mode=False)
        return 0
    except (ValueError, RuntimeError) as exc:
        click.echo(str(exc), err=True)
        return 1
    except click.ClickException as exc:
        exc.show(file=sys.stderr)
        return exc.exit_code
    except click.exceptions.Exit as exc:
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
