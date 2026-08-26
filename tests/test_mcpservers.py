"""Tests for the standard mcpServers JSON config (stabbur.mcpservers)."""

import json
from pathlib import Path

import pytest

from stabbur import mcpservers
from stabbur.mcpservers import McpServer


def test_read_project_parses_mcpservers(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "datetime": {"command": "stabbur-mcp-datetime"},
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
    p = mcpservers.add(McpServer(name="datetime", command="stabbur-mcp-datetime"), glob=False, project_dir=tmp_path)
    assert p == tmp_path / ".mcp.json"
    assert [s.name for s in mcpservers.read_project(tmp_path)] == ["datetime"]
    # Re-adding the same name replaces (idempotent), not duplicates.
    mcpservers.add(
        McpServer(name="datetime", command="stabbur-mcp-datetime", args=["--tz", "UTC"]),
        glob=False,
        project_dir=tmp_path,
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
    mcpservers.add(McpServer(name="datetime", command="stabbur-mcp-datetime"), glob=True)
    mcpservers.add(McpServer(name="search", command="global-search"), glob=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    # Project overrides "search" and adds "files".
    mcpservers.add(McpServer(name="search", command="proj-search"), glob=False, project_dir=proj)
    mcpservers.add(McpServer(name="files", command="stabbur-mcp-files"), glob=False, project_dir=proj)
    resolved = {s.name: s.command for s in mcpservers.resolve(proj)}
    assert resolved == {"datetime": "stabbur-mcp-datetime", "search": "proj-search", "files": "stabbur-mcp-files"}


# --- disable marker ("<name>": null / {"disabled": true}) -----------------------------------


def test_null_marker_tolerated_and_not_a_server(tmp_path: Path) -> None:
    # A ``null`` value disables the name: it is tolerated (no parse error) and yields no server.
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"datetime": {"command": "stabbur-mcp-datetime"}, "playwright": None}})
    )
    servers = mcpservers.read_project(tmp_path)
    assert [s.name for s in servers] == ["datetime"]  # the null entry is not a server


def test_disabled_true_marker_tolerated_and_not_a_server(tmp_path: Path) -> None:
    # A ``{"disabled": true}`` value disables the name; extra fields (a stray command) are ignored.
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "datetime": {"command": "stabbur-mcp-datetime"},
                    "playwright": {"disabled": True, "command": "bunx @playwright/mcp"},
                }
            }
        )
    )
    servers = mcpservers.read_project(tmp_path)
    assert [s.name for s in servers] == ["datetime"]  # disabled wins over the leftover command


def test_project_disable_drops_a_global_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A machine-global server the project marks disabled is removed from the resolved set.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    mcpservers.add(McpServer(name="datetime", command="stabbur-mcp-datetime"), glob=True)
    mcpservers.add(McpServer(name="playwright", command="bunx", args=["@playwright/mcp"]), glob=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"playwright": {"disabled": True}}}))
    resolved = {s.name for s in mcpservers.resolve(proj)}
    assert resolved == {"datetime"}  # the disabled global is gone, the rest stays


def test_disabled_global_is_dropped_outright(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A disable marker in the GLOBAL file drops that name outright — it never enters the merged set.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (mcpservers.global_path()).parent.mkdir(parents=True, exist_ok=True)
    mcpservers.global_path().write_text(
        json.dumps({"mcpServers": {"datetime": {"command": "stabbur-mcp-datetime"}, "playwright": None}})
    )
    assert [s.name for s in mcpservers.read_global()] == ["datetime"]
    resolved = {s.name for s in mcpservers.resolve(tmp_path / "proj")}
    assert resolved == {"datetime"}


def test_normal_entries_unaffected_by_disable_support(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # With no disable markers present, resolve() behaves exactly as before (global then project merge).
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    mcpservers.add(McpServer(name="datetime", command="stabbur-mcp-datetime"), glob=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    mcpservers.add(McpServer(name="files", command="stabbur-mcp-files"), glob=False, project_dir=proj)
    resolved = {s.name: s.command for s in mcpservers.resolve(proj)}
    assert resolved == {"datetime": "stabbur-mcp-datetime", "files": "stabbur-mcp-files"}


def test_legacy_kodo_command_is_migrated_in_memory(tmp_path: Path) -> None:
    """A pre-rename config naming `kodo-mcp-*` resolves to the stabbur binary; the file is untouched."""
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"datetime": {"command": "kodo-mcp-datetime"}}}))
    servers = mcpservers._read_file(path)
    assert servers[0].command == "stabbur-mcp-datetime"
    assert "kodo-mcp-datetime" in path.read_text()  # in-memory only; the user's file is not rewritten
