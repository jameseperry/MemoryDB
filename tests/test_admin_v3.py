from datetime import datetime, timezone

import pytest
import pytest_asyncio

from memory_v3.admin import (
    _parse_timestamp,
    create_workspace,
    delete_workspace,
    list_workspaces,
    reembed_database,
    set_workspace_document_ids,
)
from memory_v3.admin_cli import _emit_table, _workspace_import_progress_total, main


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
        self.named_understandings = {}

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

    async def execute(self, query, *args):
        if "INSERT INTO named_understandings" in query:
            _, name, understanding_id = args
            self.named_understandings[name] = understanding_id
            return None
        if "DELETE FROM named_understandings" in query:
            _, name = args
            self.named_understandings.pop(name, None)
            return None
        raise AssertionError(query)


def test_v3_parse_timestamp_from_export_snapshot():
    parsed = _parse_timestamp("2026-04-02T16:20:51.426535+00:00")

    assert parsed == datetime(2026, 4, 2, 16, 20, 51, 426535, tzinfo=timezone.utc)
    assert _parse_timestamp(parsed) is parsed
    assert _parse_timestamp(None) is None


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

    async def fake_reset_workspace(name: str):
        return {"name": name}

    monkeypatch.setattr("memory_v3.admin.get_pool", fake_get_pool_delete)
    monkeypatch.setattr("memory_v3.admin.reset_workspace", fake_reset_workspace)
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
            "consolidation_understanding_id": None,
        },
        understanding_rows={
            11: {"id": 11},
            12: {"id": 12},
            13: {"id": 13},
        },
        updated_workspace_row={
            "name": "alpha",
            "soul_understanding_id": 11,
            "protocol_understanding_id": None,
            "orientation_understanding_id": 12,
            "consolidation_understanding_id": 13,
        },
    )

    async def fake_get_pool():
        return _FakePool(conn)

    monkeypatch.setattr("memory_v3.admin.get_pool", fake_get_pool)

    result = await set_workspace_document_ids(
        "alpha",
        soul_id=11,
        orientation_id=12,
        consolidation_id=13,
    )

    assert result == {
        "name": "alpha",
        "soul_understanding_id": 11,
        "protocol_understanding_id": None,
        "orientation_understanding_id": 12,
        "consolidation_understanding_id": 13,
    }
    assert conn.named_understandings == {
        "soul": 11,
        "orientation": 12,
        "consolidation": 13,
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
            "consolidation_understanding_id": None,
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
        consolidation_id: int | None = None,
    ):
        return {
            "name": name,
            "soul_understanding_id": soul_id,
            "protocol_understanding_id": protocol_id,
            "orientation_understanding_id": orientation_id,
            "consolidation_understanding_id": consolidation_id,
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
            "--consolidation",
            "13",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        '{"name": "alpha", "soul_understanding_id": 11, '
        '"protocol_understanding_id": null, "orientation_understanding_id": 12, '
        '"consolidation_understanding_id": 13}'
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
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0] == "id  name   summary  single_subject_understanding_id"
    assert lines[1].startswith("--  -----  -------  ")
    assert lines[2] == "11  James  human"


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
        points_to: list[int] | None = None,
        session_id: str = "admin-cli",
    ):
        assert workspace == "alpha"
        assert subject_names == ["James", "MemoryDB"]
        assert content == "James is validating v3"
        assert kind == "episodic"
        assert confidence == 0.9
        assert related_to == [42]
        assert points_to == [88]
        assert session_id == "manual-check"
        return {
            "id": 77,
            "content": content,
            "kind": kind,
            "subject_names": subject_names,
            "points_to": points_to,
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
            "--points-to",
            "88",
            "--session-id",
            "manual-check",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        '{"id": 77, "content": "James is validating v3", '
        '"kind": "episodic", "subject_names": ["James", "MemoryDB"], "points_to": [88]}'
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


def test_v3_cli_workspace_reset_json(monkeypatch, capsys):
    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_reset_workspace(name: str):
        assert name == "alpha"
        return {
            "name": name,
            "subjects_deleted": 2,
            "observations_deleted": 4,
            "understandings_deleted": 3,
            "perspectives_deleted": 1,
            "utility_signals_deleted": 0,
            "events_deleted": 5,
        }

    monkeypatch.setattr("memory_v3.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_v3.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr("memory_v3.admin_cli.reset_workspace", fake_reset_workspace)

    exit_code = main(["--json", "workspace", "reset", "alpha", "--yes"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        '{"name": "alpha", "subjects_deleted": 2, "observations_deleted": 4, '
        '"understandings_deleted": 3, "perspectives_deleted": 1, '
        '"utility_signals_deleted": 0, "events_deleted": 5}'
    )


def test_v3_cli_workspace_export_text(monkeypatch, capsys):
    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_export_workspace(name: str, path: str):
        assert name == "alpha"
        assert path == "alpha.json"
        return {
            "name": name,
            "path": "/tmp/alpha.json",
            "subjects_exported": 2,
        }

    monkeypatch.setattr("memory_v3.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_v3.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr("memory_v3.admin_cli.export_workspace", fake_export_workspace)

    exit_code = main(["workspace", "export", "alpha", "alpha.json"])

    assert exit_code == 0
    assert [
        line.rstrip()
        for line in capsys.readouterr().out.strip().splitlines()
    ] == [
        "field              value",
        "-----------------  ---------------",
        "name               alpha",
        "path               /tmp/alpha.json",
        "subjects_exported  2",
    ]


def test_v3_cli_workspace_import_json(monkeypatch, capsys, tmp_path):
    snapshot = tmp_path / "workspace.json"
    snapshot.write_text("{}", encoding="utf-8")

    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_import_workspace(path: str, *, name: str | None = None):
        assert path == str(snapshot)
        assert name == "beta"
        return {
            "name": "beta",
            "source_name": "alpha",
            "subjects_imported": 2,
        }

    monkeypatch.setattr("memory_v3.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_v3.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr("memory_v3.admin_cli.import_workspace", fake_import_workspace)

    exit_code = main(
        [
            "--json",
            "workspace",
            "import",
            str(snapshot),
            "--name",
            "beta",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        '{"name": "beta", "source_name": "alpha", "subjects_imported": 2}'
    )


def test_v3_workspace_import_progress_total(tmp_path):
    snapshot = tmp_path / "workspace.json"
    snapshot.write_text(
        """
        {
          "schema_version": 3,
          "workspace": {"name": "alpha"},
          "subjects": [{"id": 1}, {"id": 2}],
          "observations": [{"id": 3}],
          "understandings": [{"id": 4}],
          "observation_subjects": [{"observation_id": 3, "subject_id": 1}],
          "understanding_subjects": [{"understanding_id": 4, "subject_id": 1}],
          "understanding_sources": [{"understanding_id": 4, "observation_id": 3}],
          "perspectives": [{"name": "general"}],
          "utility_signals": [{"target_id": 3}],
          "events": [{"id": 5}]
        }
        """,
        encoding="utf-8",
    )

    assert _workspace_import_progress_total(str(snapshot)) == 2


def test_v3_cli_workspace_import_text_progress(monkeypatch, capsys, tmp_path):
    snapshot = tmp_path / "workspace.json"
    snapshot.write_text(
        '{"schema_version": 3, "workspace": {"name": "alpha"}, '
        '"subjects": [], '
        '"observations": [{"id": 1, "content": "obs"}], '
        '"understandings": [{"id": 2, "content": "u"}]}',
        encoding="utf-8",
    )

    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_import_workspace(
        path: str,
        *,
        name: str | None = None,
        progress=None,
    ):
        assert path == str(snapshot)
        assert name is None
        assert progress is not None
        progress("embedding_observations", 1)
        progress("embedding_understandings", 1)
        return {
            "name": "alpha",
            "subjects_imported": 0,
            "observations_imported": 1,
            "understandings_imported": 1,
        }

    monkeypatch.setattr("memory_v3.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_v3.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr("memory_v3.admin_cli.import_workspace", fake_import_workspace)

    exit_code = main(["workspace", "import", str(snapshot)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Importing workspace records" in captured.out
    rows = [
        line.split()
        for line in captured.out.splitlines()
        if line
        and not line.startswith("Importing workspace records")
        and not line.startswith("Generating embeddings")
        and not line.startswith("Observations")
        and not line.startswith("Understandings")
        and not line.startswith("-")
        and not line.startswith("field")
    ]
    assert ["name", "alpha"] in rows
    assert ["subjects_imported", "0"] in rows
    assert ["observations_imported", "1"] in rows
    assert ["understandings_imported", "1"] in rows


def test_v3_cli_database_backup_json(monkeypatch, capsys):
    def fake_backup_database(path: str, method: str = "auto"):
        assert path == "backup.sql"
        assert method == "docker"
        return {
            "path": "/tmp/backup.sql",
            "method": "docker",
            "database": "memory_v3",
        }

    monkeypatch.setattr("memory_v3.admin_cli.backup_database", fake_backup_database)

    exit_code = main(["--json", "database", "backup", "backup.sql", "--method", "docker"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        '{"path": "/tmp/backup.sql", "method": "docker", "database": "memory_v3"}'
    )


def test_v3_cli_database_restore_json(monkeypatch, capsys, tmp_path):
    backup = tmp_path / "backup.sql"
    backup.write_text("SELECT 1;", encoding="utf-8")

    def fake_restore_database(path: str, method: str = "auto"):
        assert path == str(backup)
        assert method == "local"
        return {
            "path": str(backup),
            "method": "local",
            "database": "memory_v3",
        }

    monkeypatch.setattr("memory_v3.admin_cli.restore_database", fake_restore_database)

    exit_code = main(
        [
            "--json",
            "database",
            "restore",
            str(backup),
            "--method",
            "local",
            "--yes",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        f'{{"path": "{backup}", "method": "local", "database": "memory_v3"}}'
    )


@pytest.mark.asyncio
async def test_v3_reembed_database_regenerates_active_targets(monkeypatch):
    class _FakeReembedConn:
        def __init__(self):
            self.deleted_embeddings = False

        async def fetch(self, query, *args):
            if "FROM workspaces" in query:
                return [{"id": 1, "name": "alpha"}]
            if "FROM observations" in query:
                assert args == (1,)
                return [
                    {"id": 10, "content": "observation one"},
                    {"id": 11, "content": "observation two"},
                ]
            if "FROM understandings" in query:
                assert args == (1,)
                assert "superseded_by IS NULL" in query
                return [{"id": 20, "content": "understanding one"}]
            raise AssertionError(query)

        async def execute(self, query, *args):
            assert query == "DELETE FROM embeddings"
            assert args == ()
            self.deleted_embeddings = True

    conn = _FakeReembedConn()
    embedded_calls = []
    progress_calls = []

    async def fake_get_pool():
        return _FakePool(conn)

    async def fake_embed_targets(conn_arg, *, workspace_id, targets, model_version=None):
        assert conn_arg is conn
        assert workspace_id == 1
        assert model_version is None
        embedded_calls.append(list(targets))

    monkeypatch.setattr("memory_v3.admin.get_pool", fake_get_pool)
    monkeypatch.setattr("memory_v3.admin.embed_targets", fake_embed_targets)

    result = await reembed_database(
        progress=lambda label, advance: progress_calls.append((label, advance))
    )

    assert conn.deleted_embeddings
    assert embedded_calls == [
        [
            (10, "observation one"),
            (11, "observation two"),
            (20, "understanding one"),
        ]
    ]
    assert progress_calls == [("alpha", 3)]
    assert result == {
        "workspaces_reembedded": 1,
        "observations_reembedded": 2,
        "understandings_reembedded": 1,
    }


def test_v3_cli_database_reembed_json(monkeypatch, capsys):
    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_reembed_database(*, progress=None):
        assert progress is None
        return {
            "workspaces_reembedded": 2,
            "observations_reembedded": 5,
            "understandings_reembedded": 3,
        }

    monkeypatch.setattr("memory_v3.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_v3.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr("memory_v3.admin_cli.reembed_database", fake_reembed_database)

    exit_code = main(["--json", "database", "reembed"])

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == (
        '{"workspaces_reembedded": 2, "observations_reembedded": 5, '
        '"understandings_reembedded": 3}'
    )


def test_v3_cli_database_reembed_text_progress(monkeypatch, capsys):
    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_count_reembed_targets():
        return 3

    async def fake_reembed_database(*, progress=None):
        assert progress is not None
        progress("alpha", 2)
        progress("beta", 1)
        return {
            "workspaces_reembedded": 2,
            "observations_reembedded": 2,
            "understandings_reembedded": 1,
        }

    monkeypatch.setattr("memory_v3.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_v3.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr(
        "memory_v3.admin_cli.count_reembed_targets",
        fake_count_reembed_targets,
    )
    monkeypatch.setattr("memory_v3.admin_cli.reembed_database", fake_reembed_database)

    exit_code = main(["database", "reembed"])

    assert exit_code == 0
    rows = [
        line.split()
        for line in capsys.readouterr().out.splitlines()
        if line and not line.startswith("Reembedding database") and not line.startswith("-")
    ]
    assert ["workspaces_reembedded", "2"] in rows
    assert ["observations_reembedded", "2"] in rows
    assert ["understandings_reembedded", "1"] in rows


def test_v3_emit_table_wraps_long_cells(monkeypatch, capsys):
    monkeypatch.setattr("memory_v3.admin_cli._terminal_width", lambda: 40)

    _emit_table(
        ["field", "value"],
        [["summary", "this is a deliberately long value that should wrap"]],
    )

    output = capsys.readouterr().out.splitlines()
    assert output[0].startswith("field")
    assert output[2].startswith("summary")
    assert any("deliberately" in line for line in output)
    assert any("should" in line for line in output)
    assert any(line.strip() == "wrap" for line in output)


def test_v3_emit_table_no_wrap_keeps_long_cells_on_one_line(monkeypatch, capsys):
    monkeypatch.setattr("memory_v3.admin_cli._terminal_width", lambda: 40)

    _emit_table(
        ["field", "value"],
        [["summary", "this is a deliberately long value that should wrap"]],
        wrap=False,
    )

    output = capsys.readouterr().out.splitlines()
    assert output[2] == "summary  this is a deliberately long value that should wrap"


def test_v3_cli_no_wrap_disables_wrapping(monkeypatch, capsys):
    async def fake_init_pool():
        return None

    async def fake_close_pool():
        return None

    async def fake_list_subjects(workspace: str, limit: int = 100):
        assert workspace == "alpha"
        return [
            {
                "id": 11,
                "name": "James",
                "summary": "this is a deliberately long value that should wrap",
                "single_subject_understanding_id": None,
            }
        ]

    monkeypatch.setattr("memory_v3.admin_cli._terminal_width", lambda: 40)
    monkeypatch.setattr("memory_v3.admin_cli.init_pool", fake_init_pool)
    monkeypatch.setattr("memory_v3.admin_cli.close_pool", fake_close_pool)
    monkeypatch.setattr("memory_v3.admin_cli.list_subjects", fake_list_subjects)

    exit_code = main(["--no-wrap", "subject", "list", "alpha"])

    output = capsys.readouterr().out.splitlines()
    assert exit_code == 0
    assert any(
        line.startswith("11  James  this is a deliberately long value that should wrap")
        for line in output
    )
