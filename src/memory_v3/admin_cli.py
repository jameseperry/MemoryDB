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
    backup_database,
    count_reembed_targets,
    create_observation,
    create_subject,
    create_understanding,
    create_workspace,
    delete_observation,
    delete_subject,
    delete_understanding,
    delete_workspace,
    export_workspace,
    import_workspace,
    list_events,
    list_observations,
    list_perspectives,
    list_subjects,
    list_understandings,
    list_utility_signals,
    list_workspaces,
    reset_workspace,
    reembed_database,
    restore_database,
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
    if isinstance(result, dict):
        _emit_table(["field", "value"], [[key, value] for key, value in result.items()])
        return
    if isinstance(result, list):
        if not result:
            return
        if all(isinstance(item, dict) for item in result):
            headers = _collect_headers(result)
            rows = [[item.get(header) for header in headers] for item in result]
            _emit_table(headers, rows)
            return
        _emit_table(["value"], [[item] for item in result])
        return
    click.echo(str(result))


def _collect_headers(items: list[dict]) -> list[str]:
    headers: list[str] = []
    seen: set[str] = set()
    for item in items:
        for key in item:
            if key not in seen:
                seen.add(key)
                headers.append(key)
    return headers


def _format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return ", ".join(_format_cell(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, default=_json_default)
    return str(value)


def _emit_table(headers: list[str], rows: list[list[Any]]) -> None:
    formatted_rows = [[_format_cell(value) for value in row] for row in rows]
    widths = [
        max(
            len(header),
            *(len(row[index]) for row in formatted_rows),
        )
        for index, header in enumerate(headers)
    ]
    click.echo("  ".join(header.ljust(widths[index]) for index, header in enumerate(headers)))
    click.echo("  ".join("-" * width for width in widths))
    for row in formatted_rows:
        click.echo("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def _run_admin_call(coro):
    return asyncio.run(_run_with_pool(coro))


def _workspace_import_progress_total(path: str) -> int:
    with click.open_file(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    observations = payload.get("observations", [])
    understandings = payload.get("understandings", [])
    return len(observations) + len(understandings)


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
    result = _run_admin_call(create_workspace(name))
    _emit_result(result, ctx.find_root().obj["as_json"])


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
    result = _run_admin_call(
        set_workspace_document_ids(
            name,
            soul_id=soul_id,
            protocol_id=protocol_id,
            orientation_id=orientation_id,
        )
    )
    _emit_result(result, ctx.find_root().obj["as_json"])


@workspace.command("reset")
@click.argument("name")
@click.option(
    "--yes",
    is_flag=True,
    help="Skip the destructive reset confirmation prompt.",
)
@click.pass_context
def workspace_reset(ctx: click.Context, name: str, yes: bool) -> None:
    if not yes:
        click.confirm(
            f"Reset workspace {name}? This will delete all workspace contents.",
            abort=True,
        )
    result = _run_admin_call(reset_workspace(name))
    _emit_result(result, ctx.find_root().obj["as_json"])


@workspace.command("export")
@click.argument("name")
@click.argument("path", type=click.Path(dir_okay=False, path_type=str))
@click.pass_context
def workspace_export(ctx: click.Context, name: str, path: str) -> None:
    result = _run_admin_call(export_workspace(name, path))
    _emit_result(result, ctx.find_root().obj["as_json"])


@workspace.command("import")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=str))
@click.option(
    "--name",
    "workspace_name",
    default=None,
    help="Import into this workspace name instead of the source name.",
)
@click.pass_context
def workspace_import(
    ctx: click.Context,
    path: str,
    workspace_name: str | None,
) -> None:
    as_json = ctx.find_root().obj["as_json"]
    if as_json:
        result = _run_admin_call(import_workspace(path, name=workspace_name))
        _emit_result(result, as_json)
        return

    click.echo("Importing workspace records")
    total = _workspace_import_progress_total(path)

    with click.progressbar(length=total, label="Generating embeddings") as bar:
        def update_progress(label: str, advance: int) -> None:
            if not label.startswith("embedding_"):
                return
            bar.label = label.removeprefix("embedding_").replace("_", " ").title()
            bar.update(advance)

        result = _run_admin_call(
            import_workspace(
                path,
                name=workspace_name,
                progress=update_progress,
            )
        )
    _emit_result(result, as_json)


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
    result = backup_database(path, method=method)
    _emit_result(result, ctx.find_root().obj["as_json"])


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
    if not yes:
        click.confirm(
            f"Restore database from {path}? This will overwrite current database contents.",
            abort=True,
        )
    result = restore_database(path, method=method)
    _emit_result(result, ctx.find_root().obj["as_json"])


@database.command("reembed")
@click.pass_context
def database_reembed(ctx: click.Context) -> None:
    as_json = ctx.find_root().obj["as_json"]
    if as_json:
        result = _run_admin_call(reembed_database())
        _emit_result(result, as_json)
        return

    total = _run_admin_call(count_reembed_targets())
    with click.progressbar(length=total, label="Reembedding database") as bar:
        result = _run_admin_call(
            reembed_database(
                progress=lambda _label, advance: bar.update(advance),
            )
        )
    _emit_result(result, as_json)


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
