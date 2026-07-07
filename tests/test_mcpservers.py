"""Tests for the standard mcpServers JSON config (kodo.mcpservers)."""

import json
from pathlib import Path

import pytest

from kodo import mcpservers
from kodo.mcpservers import McpServer


def test_read_project_parses_mcpservers(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "datetime": {"command": "kodo-mcp-datetime"},
                    "git": {"command": "uvx", "args": ["mcp-server-git"], "env": {"GIT_ROOT": "."}},
                }
            }
        )
    )
    servers = mcpservers.read_project(tmp_path)
    assert [s.name for s in servers] == ["datetime", "git"]
    git = servers[1]
    assert git.command == "uvx" and git.args == ["mcp-server-git"] and git.env == {"GIT_ROOT": "."}
    # to_spec joins command+args into the argv connect() expects.
    assert git.to_spec() == ("git", ["uvx", "mcp-server-git"], {"GIT_ROOT": "."})


def test_missing_file_is_no_servers(tmp_path: Path) -> None:
    assert mcpservers.read_project(tmp_path) == []


def test_bad_json_raises(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text("{ not json")
    with pytest.raises(mcpservers.McpConfigError, match="not valid JSON"):
        mcpservers.read_project(tmp_path)


def test_entry_without_command_raises(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"x": {"args": ["y"]}}}))
    with pytest.raises(mcpservers.McpConfigError, match="command"):
        mcpservers.read_project(tmp_path)


def test_add_and_remove_roundtrip(tmp_path: Path) -> None:
    p = mcpservers.add(McpServer(name="datetime", command="kodo-mcp-datetime"), glob=False, project_dir=tmp_path)
    assert p == tmp_path / ".mcp.json"
    assert [s.name for s in mcpservers.read_project(tmp_path)] == ["datetime"]
    # Re-adding the same name replaces (idempotent), not duplicates.
    mcpservers.add(
        McpServer(name="datetime", command="kodo-mcp-datetime", args=["--tz", "UTC"]), glob=False, project_dir=tmp_path
    )
    servers = mcpservers.read_project(tmp_path)
    assert len(servers) == 1 and servers[0].args == ["--tz", "UTC"]
    assert mcpservers.remove("datetime", glob=False, project_dir=tmp_path) == p
    assert mcpservers.read_project(tmp_path) == []
    assert mcpservers.remove("datetime", glob=False, project_dir=tmp_path) is None  # absent


def test_written_file_is_standard_mcpservers_shape(tmp_path: Path) -> None:
    mcpservers.add(McpServer(name="git", command="uvx", args=["mcp-server-git"]), glob=False, project_dir=tmp_path)
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data == {"mcpServers": {"git": {"command": "uvx", "args": ["mcp-server-git"]}}}


def test_resolve_merges_global_then_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    mcpservers.add(McpServer(name="datetime", command="kodo-mcp-datetime"), glob=True)
    mcpservers.add(McpServer(name="search", command="global-search"), glob=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    # Project overrides "search" and adds "files".
    mcpservers.add(McpServer(name="search", command="proj-search"), glob=False, project_dir=proj)
    mcpservers.add(McpServer(name="files", command="kodo-mcp-files"), glob=False, project_dir=proj)
    resolved = {s.name: s.command for s in mcpservers.resolve(proj)}
    assert resolved == {"datetime": "kodo-mcp-datetime", "search": "proj-search", "files": "kodo-mcp-files"}
