"""CLI for v3 administrative tasks outside the MCP protocol."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import click

from memory_v3.admin import (
    create_observation,
    create_subject,
    create_understanding,
    create_workspace,
    delete_observation,
    delete_subject,
    delete_understanding,
    delete_workspace,
    list_events,
    list_observations,
    list_perspectives,
    list_subjects,
    list_understandings,
    list_utility_signals,
    list_workspaces,
    set_workspace_document_ids,
    show_observation,
    show_subject,
    show_understanding,
)
from memory_v3.db import close_pool, init_pool


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
        return
    _emit_text_result(result)


def _emit_text_result(result: Any) -> None:
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict):
                click.echo(_format_item(item))
            else:
                click.echo(str(item))
        return
    if isinstance(result, dict):
        click.echo(_format_item(result))
        return
    click.echo(str(result))


def _format_item(item: dict) -> str:
    if "subject_names" in item and "content" in item and "summary" in item and "kind" in item:
        subjects = ", ".join(item.get("subject_names", [])) or "-"
        return f"{item['id']} [{item.get('kind') or '-'}] {subjects} :: {item['summary']}"
    if "subject_names" in item and "content" in item:
        subjects = ", ".join(item.get("subject_names", [])) or "-"
        return f"{item['id']} [{item.get('kind') or '-'}] {subjects} :: {item['content']}"
    if "name" in item and "summary" in item and "single_subject_understanding_id" in item:
        return f"{item['id']} {item['name']} :: {item.get('summary') or ''}".rstrip()
    if "operation" in item and "timestamp" in item:
        return f"{item['timestamp']} [{item.get('session_id') or '-'}] {item['operation']} {item.get('detail')}"
    if "signal_type" in item and "target_id" in item:
        return (
            f"{item['id']} {item['signal_type']} {item.get('target_kind') or '-'} "
            f"{item['target_id']} :: {item.get('reason') or ''}"
        ).rstrip()
    if "instruction" in item and "is_default" in item and "name" in item:
        scope = "global" if item.get("workspace_id") is None else f"workspace:{item['workspace_id']}"
        return f"{item['id']} {item['name']} [{scope}] default={item['is_default']}"
    if "deleted" in item and "name" in item:
        return f"deleted {item['name']}" if item["deleted"] else f"not found {item['name']}"
    if "deleted" in item and "id" in item:
        return f"deleted {item['id']}" if item["deleted"] else f"not found {item['id']}"
    if "name" in item and set(item) == {"name", "created_at"}:
        return item["name"]
    return json.dumps(item, default=_json_default)


def _run_admin_call(coro):
    return asyncio.run(_run_with_pool(coro))


def _print_and_exit_on_missing(result: dict, as_json: bool) -> None:
    _emit_result(result, as_json)
    if not result["deleted"]:
        raise click.exceptions.Exit(1)


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
    result = _run_admin_call(list_workspaces())
    _emit_result(result, as_json)


@workspace.command("create")
@click.argument("name")
@click.pass_context
def workspace_create(ctx: click.Context, name: str) -> None:
    as_json = ctx.find_root().obj["as_json"]
    result = _run_admin_call(create_workspace(name))
    if as_json:
        _emit_result(result, as_json=True)
        return
    if result["created"]:
        click.echo(f"created workspace: {result['name']}")
    else:
        click.echo(f"workspace already exists: {result['name']}")


def _delete_workspace_command(ctx: click.Context, name: str) -> None:
    as_json = ctx.find_root().obj["as_json"]
    result = _run_admin_call(delete_workspace(name))
    if result["deleted"]:
        _emit_result(result, as_json)
        return
    if as_json:
        _print_and_exit_on_missing(result, as_json=True)
    click.echo(f"workspace not found: {result['name']}", err=True)
    raise click.exceptions.Exit(1)

@workspace.command("delete")
@click.argument("name")
@click.pass_context
def workspace_delete(ctx: click.Context, name: str) -> None:
    _delete_workspace_command(ctx, name)


@workspace.command("remove")
@click.argument("name")
@click.pass_context
def workspace_remove(ctx: click.Context, name: str) -> None:
    _delete_workspace_command(ctx, name)


@workspace.command("set-documents")
@click.argument("name")
@click.option("--soul", "soul_id", type=int, help="Active understanding ID for the soul document.")
@click.option(
    "--protocol",
    "protocol_id",
    type=int,
    help="Active understanding ID for the protocol document.",
)
@click.option(
    "--orientation",
    "orientation_id",
    type=int,
    help="Active understanding ID for the orientation document.",
)
@click.pass_context
def workspace_set_documents(
    ctx: click.Context,
    name: str,
    soul_id: int | None,
    protocol_id: int | None,
    orientation_id: int | None,
) -> None:
    as_json = ctx.find_root().obj["as_json"]
    result = _run_admin_call(
        set_workspace_document_ids(
            name,
            soul_id=soul_id,
            protocol_id=protocol_id,
            orientation_id=orientation_id,
        )
    )
    if as_json:
        _emit_result(result, as_json=True)
        return
    click.echo(
        "updated workspace documents for "
        f"{result['name']}: "
        f"soul={result['soul_understanding_id']}, "
        f"protocol={result['protocol_understanding_id']}, "
        f"orientation={result['orientation_understanding_id']}"
    )


@cli.group()
def subject() -> None:
    """Subject inspection and mutation commands."""


@subject.command("list")
@click.argument("workspace")
@click.option("--limit", type=int, default=100, show_default=True)
@click.pass_context
def subject_list(ctx: click.Context, workspace: str, limit: int) -> None:
    result = _run_admin_call(list_subjects(workspace, limit=limit))
    _emit_result(result, ctx.find_root().obj["as_json"])


@subject.command("create")
@click.argument("workspace")
@click.argument("name")
@click.option("--summary", default=None)
@click.option("--tag", "tags", multiple=True)
@click.pass_context
def subject_create(
    ctx: click.Context,
    workspace: str,
    name: str,
    summary: str | None,
    tags: tuple[str, ...],
) -> None:
    result = _run_admin_call(
        create_subject(workspace, name, summary=summary, tags=list(tags))
    )
    _emit_result(result, ctx.find_root().obj["as_json"])


@subject.command("show")
@click.argument("workspace")
@click.argument("name")
@click.pass_context
def subject_show(ctx: click.Context, workspace: str, name: str) -> None:
    result = _run_admin_call(show_subject(workspace, name))
    _emit_result(result, ctx.find_root().obj["as_json"])


@subject.command("delete")
@click.argument("workspace")
@click.argument("name")
@click.pass_context
def subject_delete(ctx: click.Context, workspace: str, name: str) -> None:
    as_json = ctx.find_root().obj["as_json"]
    result = _run_admin_call(delete_subject(workspace, name))
    if result["deleted"]:
        _emit_result(result, as_json)
        return
    if as_json:
        _print_and_exit_on_missing(result, as_json=True)
    click.echo(f"subject not found: {name}", err=True)
    raise click.exceptions.Exit(1)


@cli.group()
def observation() -> None:
    """Observation inspection and mutation commands."""


@observation.command("list")
@click.argument("workspace")
@click.option("--subject", "subject_name", default=None)
@click.option("--limit", type=int, default=100, show_default=True)
@click.pass_context
def observation_list(
    ctx: click.Context,
    workspace: str,
    subject_name: str | None,
    limit: int,
) -> None:
    result = _run_admin_call(
        list_observations(workspace, subject_name=subject_name, limit=limit)
    )
    _emit_result(result, ctx.find_root().obj["as_json"])


@observation.command("create")
@click.argument("workspace")
@click.option("--subject", "subject_names", multiple=True, required=True)
@click.option("--content", required=True)
@click.option("--kind", default=None)
@click.option("--confidence", type=float, default=None)
@click.option("--related-to", "related_to", multiple=True, type=int)
@click.option("--session-id", default="admin-cli", show_default=True)
@click.pass_context
def observation_create(
    ctx: click.Context,
    workspace: str,
    subject_names: tuple[str, ...],
    content: str,
    kind: str | None,
    confidence: float | None,
    related_to: tuple[int, ...],
    session_id: str,
) -> None:
    result = _run_admin_call(
        create_observation(
            workspace,
            list(subject_names),
            content,
            kind=kind,
            confidence=confidence,
            related_to=list(related_to) or None,
            session_id=session_id,
        )
    )
    _emit_result(result, ctx.find_root().obj["as_json"])


@observation.command("show")
@click.argument("workspace")
@click.argument("observation_id", type=int)
@click.pass_context
def observation_show(
    ctx: click.Context,
    workspace: str,
    observation_id: int,
) -> None:
    result = _run_admin_call(show_observation(workspace, observation_id))
    _emit_result(result, ctx.find_root().obj["as_json"])


@observation.command("delete")
@click.argument("workspace")
@click.argument("observation_id", type=int)
@click.pass_context
def observation_delete(
    ctx: click.Context,
    workspace: str,
    observation_id: int,
) -> None:
    as_json = ctx.find_root().obj["as_json"]
    result = _run_admin_call(delete_observation(workspace, observation_id))
    if result["deleted"]:
        _emit_result(result, as_json)
        return
    if as_json:
        _print_and_exit_on_missing(result, as_json=True)
    click.echo(f"observation not found: {observation_id}", err=True)
    raise click.exceptions.Exit(1)


@cli.group()
def understanding() -> None:
    """Understanding inspection and mutation commands."""


@understanding.command("list")
@click.argument("workspace")
@click.option("--subject", "subject_name", default=None)
@click.option("--kind", default=None)
@click.option("--include-superseded", is_flag=True)
@click.option("--limit", type=int, default=100, show_default=True)
@click.pass_context
def understanding_list(
    ctx: click.Context,
    workspace: str,
    subject_name: str | None,
    kind: str | None,
    include_superseded: bool,
    limit: int,
) -> None:
    result = _run_admin_call(
        list_understandings(
            workspace,
            subject_name=subject_name,
            kind=kind,
            include_superseded=include_superseded,
            limit=limit,
        )
    )
    _emit_result(result, ctx.find_root().obj["as_json"])


@understanding.command("create")
@click.argument("workspace")
@click.option("--subject", "subject_names", multiple=True, required=True)
@click.option("--content", required=True)
@click.option("--summary", required=True)
@click.option("--kind", default=None)
@click.option("--source-observation", "source_observation_ids", multiple=True, type=int)
@click.option("--reason", default=None)
@click.option("--session-id", default="admin-cli", show_default=True)
@click.pass_context
def understanding_create(
    ctx: click.Context,
    workspace: str,
    subject_names: tuple[str, ...],
    content: str,
    summary: str,
    kind: str | None,
    source_observation_ids: tuple[int, ...],
    reason: str | None,
    session_id: str,
) -> None:
    result = _run_admin_call(
        create_understanding(
            workspace,
            list(subject_names),
            content,
            summary,
            kind=kind,
            source_observation_ids=list(source_observation_ids) or None,
            reason=reason,
            session_id=session_id,
        )
    )
    _emit_result(result, ctx.find_root().obj["as_json"])


@understanding.command("show")
@click.argument("workspace")
@click.argument("understanding_id", type=int)
@click.pass_context
def understanding_show(
    ctx: click.Context,
    workspace: str,
    understanding_id: int,
) -> None:
    result = _run_admin_call(show_understanding(workspace, understanding_id))
    _emit_result(result, ctx.find_root().obj["as_json"])


@understanding.command("delete")
@click.argument("workspace")
@click.argument("understanding_id", type=int)
@click.pass_context
def understanding_delete(
    ctx: click.Context,
    workspace: str,
    understanding_id: int,
) -> None:
    as_json = ctx.find_root().obj["as_json"]
    result = _run_admin_call(delete_understanding(workspace, understanding_id))
    if result["deleted"]:
        _emit_result(result, as_json)
        return
    if as_json:
        _print_and_exit_on_missing(result, as_json=True)
    click.echo(f"understanding not found: {understanding_id}", err=True)
    raise click.exceptions.Exit(1)


@cli.group()
def event() -> None:
    """Event log inspection commands."""


@event.command("list")
@click.argument("workspace")
@click.option("--limit", type=int, default=100, show_default=True)
@click.pass_context
def event_list(ctx: click.Context, workspace: str, limit: int) -> None:
    result = _run_admin_call(list_events(workspace, limit=limit))
    _emit_result(result, ctx.find_root().obj["as_json"])


@cli.group("utility-signal")
def utility_signal() -> None:
    """Utility signal inspection commands."""


@utility_signal.command("list")
@click.argument("workspace")
@click.option("--limit", type=int, default=100, show_default=True)
@click.pass_context
def utility_signal_list(ctx: click.Context, workspace: str, limit: int) -> None:
    result = _run_admin_call(list_utility_signals(workspace, limit=limit))
    _emit_result(result, ctx.find_root().obj["as_json"])


@cli.group()
def perspective() -> None:
    """Perspective inspection commands."""


@perspective.command("list")
@click.argument("workspace")
@click.option(
    "--exclude-global",
    is_flag=True,
    help="Hide global default perspectives.",
)
@click.pass_context
def perspective_list(
    ctx: click.Context,
    workspace: str,
    exclude_global: bool,
) -> None:
    result = _run_admin_call(
        list_perspectives(workspace, include_global=not exclude_global)
    )
    _emit_result(result, ctx.find_root().obj["as_json"])


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = cli.main(
            args=list(argv) if argv is not None else None,
            prog_name="memory-admin-v3",
            standalone_mode=False,
        )
        return int(result) if isinstance(result, int) else 0
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
