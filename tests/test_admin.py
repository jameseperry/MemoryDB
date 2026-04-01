from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from memory_mcp.admin import (
    backup_database,
    create_workspace,
    delete_workspace,
    list_workspaces,
    rehome_null_workspace_nodes,
    restore_database,
)
from memory_mcp.admin_cli import main


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _AsyncContext(self.conn)


class _FakeConn:
    def __init__(
        self,
        *,
        workspace_row=None,
        conflicting=None,
        legacy_node_ids=None,
        update_counts=None,
    ):
        self.workspace_row = workspace_row
        self.conflicting = conflicting or []
        self.legacy_node_ids = legacy_node_ids or []
        self.update_counts = update_counts or {
            "nodes": "UPDATE 0",
            "relations": "UPDATE 0",
            "events": "UPDATE 0",
        }

    def transaction(self):
        return _AsyncContext(None)

    async def fetchrow(self, query, *args):
        if "FROM workspaces" in query:
            return self.workspace_row
        raise AssertionError(query)

    async def fetch(self, query, *args):
        if "JOIN nodes AS target" in query:
            return self.conflicting
        if "FROM nodes" in query and "workspace_id IS NULL" in query:
            return self.legacy_node_ids
        raise AssertionError(query)

    async def execute(self, query, *args):
        if "UPDATE nodes" in query:
            return self.update_counts["nodes"]
        if "UPDATE relations" in query:
            return self.update_counts["relations"]
        if "UPDATE events" in query:
            return self.update_counts["events"]
        raise AssertionError(query)


async def test_workspace_admin_crud(ws, other_ws):
    result = await list_workspaces()
    assert [row["name"] for row in result] == sorted([ws, other_ws])

    created = await create_workspace("zeta")
    assert created["name"] == "zeta"
    assert created["created"] is True

    created_again = await create_workspace("zeta")
    assert created_again["name"] == "zeta"
    assert created_again["created"] is False

    names = [row["name"] for row in await list_workspaces()]
    assert names == sorted([ws, other_ws, "zeta"])

    deleted = await delete_workspace("zeta")
    assert deleted == {"name": "zeta", "deleted": True}

    deleted_again = await delete_workspace("zeta")
    assert deleted_again == {"name": "zeta", "deleted": False}


@pytest.mark.parametrize("name", ["", "   "])
async def test_workspace_admin_rejects_blank_names(name):
    with pytest.raises(ValueError, match="Workspace name is required"):
        await create_workspace(name)

    with pytest.raises(ValueError, match="Workspace name is required"):
        await delete_workspace(name)


def test_cli_list_json(monkeypatch, capsys):
    stamp = datetime(2026, 4, 1, tzinfo=timezone.utc)

    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_list_workspaces():
        return [{"name": "alpha", "created_at": stamp}]

    monkeypatch.setattr("memory_mcp.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_mcp.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr("memory_mcp.admin_cli.list_workspaces", fake_list_workspaces)

    exit_code = main(["--json", "workspace", "list"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        '[{"name": "alpha", "created_at": "2026-04-01T00:00:00+00:00"}]'
    )


def test_cli_delete_missing_workspace(monkeypatch, capsys):
    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_delete_workspace(name: str):
        return {"name": name, "deleted": False}

    monkeypatch.setattr("memory_mcp.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_mcp.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr("memory_mcp.admin_cli.delete_workspace", fake_delete_workspace)

    exit_code = main(["workspace", "delete", "missing"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.strip() == "workspace not found: missing"


@pytest.mark.asyncio
async def test_rehome_null_workspace_nodes(monkeypatch):
    conn = _FakeConn(
        workspace_row={"id": 7, "name": "target"},
        legacy_node_ids=[{"id": 11}, {"id": 12}],
        update_counts={
            "nodes": "UPDATE 2",
            "relations": "UPDATE 1",
            "events": "UPDATE 3",
        },
    )

    async def fake_get_pool():
        return _FakePool(conn)

    monkeypatch.setattr("memory_mcp.admin.get_pool", fake_get_pool)

    result = await rehome_null_workspace_nodes("target")

    assert result == {
        "workspace": "target",
        "nodes_rehomed": 2,
        "relations_rehomed": 1,
        "events_rehomed": 3,
    }


@pytest.mark.asyncio
async def test_rehome_null_workspace_nodes_conflict(monkeypatch):
    conn = _FakeConn(
        workspace_row={"id": 7, "name": "target"},
        conflicting=[{"name": "alpha"}, {"name": "beta"}],
    )

    async def fake_get_pool():
        return _FakePool(conn)

    monkeypatch.setattr("memory_mcp.admin.get_pool", fake_get_pool)

    with pytest.raises(ValueError, match="already has nodes with the same names: alpha, beta"):
        await rehome_null_workspace_nodes("target")


def test_cli_workspace_rehome_null_json(monkeypatch, capsys):
    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_rehome_null_workspace_nodes(name: str):
        return {
            "workspace": name,
            "nodes_rehomed": 2,
            "relations_rehomed": 1,
            "events_rehomed": 0,
        }

    monkeypatch.setattr("memory_mcp.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_mcp.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr(
        "memory_mcp.admin_cli.rehome_null_workspace_nodes",
        fake_rehome_null_workspace_nodes,
    )

    exit_code = main(["--json", "workspace", "rehome-null", "target"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        '{"workspace": "target", "nodes_rehomed": 2, "relations_rehomed": 1, "events_rehomed": 0}'
    )


def test_backup_database_uses_docker(monkeypatch, tmp_path):
    backup_path = tmp_path / "backup.sql"
    calls = []

    def fake_which(name: str):
        if name == "docker":
            return "/usr/bin/docker"
        return None

    def fake_run(command, check, stderr, stdout=None, stdin=None):
        calls.append(command)
        stdout.write(b"-- dump --\n")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr("memory_mcp.admin.shutil.which", fake_which)
    monkeypatch.setattr("memory_mcp.admin.subprocess.run", fake_run)

    result = backup_database(backup_path)

    assert result["method"] == "docker"
    assert result["path"] == str(backup_path.resolve())
    assert calls == [[
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "-U",
        "memory",
        "-d",
        "memory",
        "--format=plain",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
    ]]
    assert backup_path.read_text() == "-- dump --\n"


def test_restore_database_uses_docker(monkeypatch, tmp_path):
    backup_path = tmp_path / "backup.sql"
    backup_path.write_text("-- dump --\n")
    calls = []

    def fake_which(name: str):
        if name == "docker":
            return "/usr/bin/docker"
        return None

    def fake_run(command, check, stderr, stdout=None, stdin=None):
        calls.append((command, stdin.read()))
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr("memory_mcp.admin.shutil.which", fake_which)
    monkeypatch.setattr("memory_mcp.admin.subprocess.run", fake_run)

    result = restore_database(backup_path)

    assert result["method"] == "docker"
    assert result["path"] == str(backup_path.resolve())
    assert calls == [([
        "docker",
        "compose",
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "memory",
        "-d",
        "memory",
        "-v",
        "ON_ERROR_STOP=1",
        "-1",
    ], b"-- dump --\n")]


def test_restore_database_rejects_missing_file(tmp_path):
    missing_path = tmp_path / "missing.sql"

    with pytest.raises(ValueError, match="Backup file not found"):
        restore_database(missing_path)


def test_cli_database_backup_json(monkeypatch, capsys, tmp_path):
    backup_path = tmp_path / "backup.sql"

    def fake_backup_database(path: str, method: str = "auto"):
        assert path == str(backup_path)
        assert method == "docker"
        return {
            "path": str(backup_path),
            "method": "docker",
            "database": "memory",
        }

    monkeypatch.setattr("memory_mcp.admin_cli.backup_database", fake_backup_database)

    exit_code = main(["--json", "database", "backup", str(backup_path), "--method", "docker"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        '{"path": "'
        + str(backup_path)
        + '", "method": "docker", "database": "memory"}'
    )


def test_cli_database_restore_requires_yes(monkeypatch, capsys, tmp_path):
    backup_path = tmp_path / "backup.sql"
    backup_path.write_text("-- dump --\n")

    monkeypatch.setattr("memory_mcp.admin_cli.click.confirm", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "memory_mcp.admin_cli.restore_database",
        lambda path, method="auto": {
            "path": str(Path(path)),
            "method": method,
            "database": "memory",
        },
    )

    exit_code = main(["database", "restore", str(backup_path), "--method", "docker"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        f"restored database from: {backup_path} (docker)"
    )
