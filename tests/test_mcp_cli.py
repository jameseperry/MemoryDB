from pathlib import Path

from memory_v3.mcp_cli import main


def test_mcp_cli_list_tools_from_named_server(monkeypatch, capsys, tmp_path):
    config = tmp_path / "codex.toml"
    config.write_text(
        """
        [mcp_servers.memory_test]
        enabled = true
        url = "http://127.0.0.1:8765/v3/mcp"

        [mcp_servers.memory_test.http_headers]
        X-Memory-Workspace = "james/gpt"
        """,
        encoding="utf-8",
    )

    async def fake_list_tools(url: str, headers: dict[str, str]):
        assert url == "http://127.0.0.1:8765/v3/mcp"
        assert headers == {"X-Memory-Workspace": "james/gpt"}
        return [{"name": "orient", "description": "Load workspace documents"}]

    monkeypatch.setattr("memory_v3.mcp_cli._list_tools", fake_list_tools)

    exit_code = main(
        [
            "--server",
            "memory_test",
            "--config",
            str(config),
            "list-tools",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == "orient\tLoad workspace documents"


def test_mcp_cli_call_merges_workspace_override(monkeypatch, capsys, tmp_path):
    config = tmp_path / "codex.toml"
    config.write_text(
        """
        [mcp_servers.memory_test]
        enabled = true
        url = "http://127.0.0.1:8765/v3/mcp"

        [mcp_servers.memory_test.http_headers]
        X-Memory-Workspace = "wrong/workspace"
        Authorization = "Bearer token"
        """,
        encoding="utf-8",
    )

    async def fake_call_tool(url: str, headers: dict[str, str], tool_name: str, arguments: dict):
        assert url == "http://127.0.0.1:8765/v3/mcp"
        assert headers == {
            "X-Memory-Workspace": "james/gpt",
            "Authorization": "Bearer token",
        }
        assert tool_name == "get_workspace_documents"
        assert arguments == {}
        return {"soul_understanding_id": 183}

    monkeypatch.setattr("memory_v3.mcp_cli._call_tool", fake_call_tool)

    exit_code = main(
        [
            "--server",
            "memory_test",
            "--workspace",
            "james/gpt",
            "--config",
            str(config),
            "call",
            "get_workspace_documents",
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == '{\n  "soul_understanding_id": 183\n}'


def test_mcp_cli_rejects_non_object_arguments(capsys):
    exit_code = main(
        [
            "--url",
            "http://127.0.0.1:8765/v3/mcp",
            "call",
            "orient",
            '["not","an","object"]',
        ]
    )

    assert exit_code == 1
    assert capsys.readouterr().err.strip() == "Tool arguments must be a JSON object"
