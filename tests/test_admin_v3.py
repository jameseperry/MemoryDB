from datetime import datetime, timezone

import pytest
import pytest_asyncio

from memory_v3.admin import (
    create_workspace,
    delete_workspace,
    list_workspaces,
    set_workspace_document_ids,
)
from memory_v3.admin_cli import main


@pytest_asyncio.fixture(scope="session")
async def db_pool():
    """Override the global DB fixture: these tests do not touch Postgres."""
    yield


@pytest_asyncio.fixture(autouse=True)
async def isolated_workspace():
    """Override the global isolation fixture: these tests do not touch Postgres."""
    yield


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
        inserted_row=None,
        selected_row=None,
        deleted=False,
        listed_rows=None,
        workspace_row=None,
        understanding_rows=None,
        updated_workspace_row=None,
    ):
        self.inserted_row = inserted_row
        self.selected_row = selected_row
        self.deleted = deleted
        self.listed_rows = listed_rows or []
        self.workspace_row = workspace_row
        self.understanding_rows = understanding_rows or {}
        self.updated_workspace_row = updated_workspace_row

    async def fetch(self, query, *args):
        if "SELECT name, created_at" in query and "ORDER BY name" in query:
            return self.listed_rows
        raise AssertionError(query)

    async def fetchrow(self, query, *args):
        if "INSERT INTO workspaces" in query:
            return self.inserted_row
        if "SELECT name, created_at" in query and "WHERE name = $1" in query:
            return self.selected_row
        if "DELETE FROM workspaces" in query:
            return {"name": args[0]} if self.deleted else None
        if "FROM workspaces" in query and "soul_understanding_id" in query:
            return self.workspace_row
        if "FROM understandings" in query:
            return self.understanding_rows.get(args[0])
        if "UPDATE workspaces" in query:
            return self.updated_workspace_row
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_v3_workspace_admin_crud_with_fake_pool(monkeypatch):
    stamp = datetime(2026, 4, 2, tzinfo=timezone.utc)

    async def fake_get_pool_list():
        return _FakePool(_FakeConn(listed_rows=[{"name": "alpha", "created_at": stamp}]))

    monkeypatch.setattr("memory_v3.admin.get_pool", fake_get_pool_list)
    listed = await list_workspaces()
    assert listed == [{"name": "alpha", "created_at": stamp}]

    async def fake_get_pool_create():
        return _FakePool(
            _FakeConn(
                inserted_row={"name": "beta", "created_at": stamp},
                selected_row={"name": "beta", "created_at": stamp},
            )
        )

    monkeypatch.setattr("memory_v3.admin.get_pool", fake_get_pool_create)
    created = await create_workspace("beta")
    assert created == {
        "name": "beta",
        "created_at": stamp,
        "created": True,
    }

    async def fake_get_pool_existing():
        return _FakePool(
            _FakeConn(
                inserted_row=None,
                selected_row={"name": "beta", "created_at": stamp},
            )
        )

    monkeypatch.setattr("memory_v3.admin.get_pool", fake_get_pool_existing)
    created_again = await create_workspace("beta")
    assert created_again == {
        "name": "beta",
        "created_at": stamp,
        "created": False,
    }

    async def fake_get_pool_delete():
        return _FakePool(_FakeConn(deleted=True))

    monkeypatch.setattr("memory_v3.admin.get_pool", fake_get_pool_delete)
    deleted = await delete_workspace("beta")
    assert deleted == {"name": "beta", "deleted": True}


@pytest.mark.parametrize("name", ["", "   "])
@pytest.mark.asyncio
async def test_v3_workspace_admin_rejects_blank_names(name):
    with pytest.raises(ValueError, match="Workspace name is required"):
        await create_workspace(name)

    with pytest.raises(ValueError, match="Workspace name is required"):
        await delete_workspace(name)


@pytest.mark.asyncio
async def test_v3_workspace_set_document_ids(monkeypatch):
    conn = _FakeConn(
        workspace_row={
            "id": 7,
            "name": "alpha",
            "soul_understanding_id": None,
            "protocol_understanding_id": None,
            "orientation_understanding_id": None,
        },
        understanding_rows={
            11: {"id": 11},
            12: {"id": 12},
        },
        updated_workspace_row={
            "name": "alpha",
            "soul_understanding_id": 11,
            "protocol_understanding_id": None,
            "orientation_understanding_id": 12,
        },
    )

    async def fake_get_pool():
        return _FakePool(conn)

    monkeypatch.setattr("memory_v3.admin.get_pool", fake_get_pool)

    result = await set_workspace_document_ids(
        "alpha",
        soul_id=11,
        orientation_id=12,
    )

    assert result == {
        "name": "alpha",
        "soul_understanding_id": 11,
        "protocol_understanding_id": None,
        "orientation_understanding_id": 12,
    }


@pytest.mark.asyncio
async def test_v3_workspace_set_document_ids_requires_ids():
    with pytest.raises(ValueError, match="At least one document ID must be provided"):
        await set_workspace_document_ids("alpha")


@pytest.mark.asyncio
async def test_v3_workspace_set_document_ids_rejects_missing_understanding(monkeypatch):
    conn = _FakeConn(
        workspace_row={
            "id": 7,
            "name": "alpha",
            "soul_understanding_id": None,
            "protocol_understanding_id": None,
            "orientation_understanding_id": None,
        },
        understanding_rows={},
    )

    async def fake_get_pool():
        return _FakePool(conn)

    monkeypatch.setattr("memory_v3.admin.get_pool", fake_get_pool)

    with pytest.raises(ValueError, match="not found in workspace 'alpha' or not active"):
        await set_workspace_document_ids("alpha", soul_id=99)


def test_v3_cli_list_json(monkeypatch, capsys):
    stamp = datetime(2026, 4, 2, tzinfo=timezone.utc)

    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_list_workspaces():
        return [{"name": "alpha", "created_at": stamp}]

    monkeypatch.setattr("memory_v3.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_v3.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr("memory_v3.admin_cli.list_workspaces", fake_list_workspaces)

    exit_code = main(["--json", "workspace", "list"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        '[{"name": "alpha", "created_at": "2026-04-02T00:00:00+00:00"}]'
    )


def test_v3_cli_remove_missing_workspace(monkeypatch, capsys):
    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_delete_workspace(name: str):
        return {"name": name, "deleted": False}

    monkeypatch.setattr("memory_v3.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_v3.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr("memory_v3.admin_cli.delete_workspace", fake_delete_workspace)

    exit_code = main(["workspace", "remove", "missing"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.strip() == "workspace not found: missing"


def test_v3_cli_set_documents_json(monkeypatch, capsys):
    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_set_workspace_document_ids(
        name: str,
        *,
        soul_id: int | None = None,
        protocol_id: int | None = None,
        orientation_id: int | None = None,
    ):
        return {
            "name": name,
            "soul_understanding_id": soul_id,
            "protocol_understanding_id": protocol_id,
            "orientation_understanding_id": orientation_id,
        }

    monkeypatch.setattr("memory_v3.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_v3.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr(
        "memory_v3.admin_cli.set_workspace_document_ids",
        fake_set_workspace_document_ids,
    )

    exit_code = main(
        [
            "--json",
            "workspace",
            "set-documents",
            "alpha",
            "--soul",
            "11",
            "--orientation",
            "12",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        '{"name": "alpha", "soul_understanding_id": 11, '
        '"protocol_understanding_id": null, "orientation_understanding_id": 12}'
    )


def test_v3_cli_subject_list_text(monkeypatch, capsys):
    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_list_subjects(workspace: str, limit: int = 100):
        assert workspace == "alpha"
        assert limit == 2
        return [
            {
                "id": 11,
                "name": "James",
                "summary": "human",
                "single_subject_understanding_id": None,
            }
        ]

    monkeypatch.setattr("memory_v3.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_v3.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr("memory_v3.admin_cli.list_subjects", fake_list_subjects)

    exit_code = main(["subject", "list", "alpha", "--limit", "2"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "11 James :: human"


def test_v3_cli_observation_create_json(monkeypatch, capsys):
    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_create_observation(
        workspace: str,
        subject_names: list[str],
        content: str,
        *,
        kind: str | None = None,
        confidence: float | None = None,
        related_to: list[int] | None = None,
        session_id: str = "admin-cli",
    ):
        assert workspace == "alpha"
        assert subject_names == ["James", "MemoryDB"]
        assert content == "James is validating v3"
        assert kind == "episodic"
        assert confidence == 0.9
        assert related_to == [42]
        assert session_id == "manual-check"
        return {
            "id": 77,
            "content": content,
            "kind": kind,
            "subject_names": subject_names,
        }

    monkeypatch.setattr("memory_v3.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_v3.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr(
        "memory_v3.admin_cli.create_observation",
        fake_create_observation,
    )

    exit_code = main(
        [
            "--json",
            "observation",
            "create",
            "alpha",
            "--subject",
            "James",
            "--subject",
            "MemoryDB",
            "--content",
            "James is validating v3",
            "--kind",
            "episodic",
            "--confidence",
            "0.9",
            "--related-to",
            "42",
            "--session-id",
            "manual-check",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        '{"id": 77, "content": "James is validating v3", '
        '"kind": "episodic", "subject_names": ["James", "MemoryDB"]}'
    )


def test_v3_cli_understanding_delete_missing(monkeypatch, capsys):
    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_delete_understanding(workspace: str, understanding_id: int):
        assert workspace == "alpha"
        assert understanding_id == 404
        return {"id": understanding_id, "deleted": False}

    monkeypatch.setattr("memory_v3.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_v3.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr(
        "memory_v3.admin_cli.delete_understanding",
        fake_delete_understanding,
    )

    exit_code = main(["understanding", "delete", "alpha", "404"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err.strip() == "understanding not found: 404"
