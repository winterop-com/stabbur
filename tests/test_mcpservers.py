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


def test_entry_without_command_is_skipped_not_fatal(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # A malformed entry must not take down the servers around it (it used to fail the whole file).
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"x": {"args": ["y"]}, "datetime": {"command": "stabbur-mcp-datetime"}}})
    )
    with caplog.at_level("WARNING", logger="stabbur.mcpservers"):
        assert [s.name for s in mcpservers.read_project(tmp_path)] == ["datetime"]
    assert "'x'" in caplog.text and "command" in caplog.text


def test_remote_entry_is_skipped_with_a_named_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # The ecosystem-standard remote shape: not runnable yet, but it must not kill the local servers.
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "remote": {"type": "http", "url": "https://example.com/mcp"},
                    "datetime": {"command": "stabbur-mcp-datetime"},
                }
            }
        )
    )
    with caplog.at_level("WARNING", logger="stabbur.mcpservers"):
        assert [s.name for s in mcpservers.read_project(tmp_path)] == ["datetime"]
    assert "remote MCP servers are not supported yet" in caplog.text and "'remote'" in caplog.text


def test_bad_mcpservers_type_still_raises(tmp_path: Path) -> None:
    # A structurally wrong file is still an error — only individual entries are tolerated.
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": ["datetime"]}))
    with pytest.raises(mcpservers.McpConfigError, match="must be an object"):
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


def test_add_does_not_re_enable_a_disabled_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The silent re-enable: adding an UNRELATED server used to rewrite the whole file from stabbur's
    # own model, dropping the project's disable marker and switching a global server back on.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    mcpservers.add(McpServer(name="playwright", command="bunx", args=["@playwright/mcp"]), glob=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"playwright": {"disabled": True}}}))

    mcpservers.add(McpServer(name="datetime", command="stabbur-mcp-datetime"), glob=False, project_dir=proj)

    data = json.loads((proj / ".mcp.json").read_text())
    assert data["mcpServers"]["playwright"] == {"disabled": True}  # marker survived the write
    assert {s.name for s in mcpservers.resolve(proj)} == {"datetime"}  # still disabled


def test_write_preserves_unmodelled_keys(tmp_path: Path) -> None:
    # $schema, inputs, and per-server fields stabbur has no opinion about must all survive a write.
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "$schema": "https://example.com/mcp.schema.json",
                "inputs": [{"id": "token", "type": "promptString"}],
                "mcpServers": {
                    "git": {"command": "uvx", "args": ["mcp-server-git"], "autoApprove": ["status"], "timeout": 30},
                    "remote": {"type": "http", "url": "https://example.com/mcp"},
                    "off": None,
                },
            }
        )
    )
    mcpservers.add(McpServer(name="datetime", command="stabbur-mcp-datetime"), glob=False, project_dir=tmp_path)
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["$schema"] == "https://example.com/mcp.schema.json"
    assert data["inputs"] == [{"id": "token", "type": "promptString"}]
    assert data["mcpServers"]["remote"] == {"type": "http", "url": "https://example.com/mcp"}
    assert data["mcpServers"]["off"] is None
    assert data["mcpServers"]["git"]["autoApprove"] == ["status"] and data["mcpServers"]["git"]["timeout"] == 30
    assert data["mcpServers"]["datetime"] == {"command": "stabbur-mcp-datetime"}


def test_replacing_a_server_keeps_its_extra_fields(tmp_path: Path) -> None:
    # Re-adding by name updates command/args/env and leaves the rest of the entry alone.
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"git": {"command": "uvx", "args": ["mcp-server-git"], "timeout": 30}}})
    )
    mcpservers.add(
        McpServer(name="git", command="uvx", args=["mcp-server-git", "--repo", "."], env={"GIT_ROOT": "."}),
        glob=False,
        project_dir=tmp_path,
    )
    entry = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["git"]
    assert entry["args"] == ["mcp-server-git", "--repo", "."] and entry["env"] == {"GIT_ROOT": "."}
    assert entry["timeout"] == 30  # not modelled, not lost


def test_remove_only_deletes_its_own_key(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "$schema": "https://example.com/mcp.schema.json",
                "mcpServers": {
                    "git": {"command": "uvx", "args": ["mcp-server-git"]},
                    "remote": {"type": "http", "url": "https://example.com/mcp"},
                    "off": {"disabled": True},
                },
            }
        )
    )
    assert mcpservers.remove("git", glob=False, project_dir=tmp_path) == tmp_path / ".mcp.json"
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert data["$schema"] == "https://example.com/mcp.schema.json"
    assert set(data["mcpServers"]) == {"remote", "off"}
    # A disable marker is not a server to delete — removing it reports "absent" and changes nothing.
    assert mcpservers.remove("off", glob=False, project_dir=tmp_path) is None
    assert json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["off"] == {"disabled": True}


def test_add_re_enables_the_name_it_is_asked_for(tmp_path: Path) -> None:
    # Preservation is per-key: an explicit add of a disabled NAME is a deliberate re-enable.
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"datetime": {"disabled": True}}}))
    mcpservers.add(McpServer(name="datetime", command="stabbur-mcp-datetime"), glob=False, project_dir=tmp_path)
    entry = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["datetime"]
    assert entry == {"command": "stabbur-mcp-datetime"}  # marker replaced, not merged
    assert [s.name for s in mcpservers.read_project(tmp_path)] == ["datetime"]


def test_normal_entries_unaffected_by_disable_support(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # With no disable markers present, resolve() behaves exactly as before (global then project merge).
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    mcpservers.add(McpServer(name="datetime", command="stabbur-mcp-datetime"), glob=True)
    proj = tmp_path / "proj"
    proj.mkdir()
    mcpservers.add(McpServer(name="files", command="stabbur-mcp-files"), glob=False, project_dir=proj)
    resolved = {s.name: s.command for s in mcpservers.resolve(proj)}
    assert resolved == {"datetime": "stabbur-mcp-datetime", "files": "stabbur-mcp-files"}
