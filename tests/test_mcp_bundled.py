"""Tests for bundled-MCP discovery, toggling, and the fresh-machine seed.

stabbur ships a dozen ``stabbur-mcp-*`` servers that were invisible until someone hand-wrote an
``mcp.json``. These cover the three pieces that fix that: :func:`stabbur.mcp_catalog.bundled`
(the shipped set + its resolved on/off state), :func:`~stabbur.mcp_catalog.set_enabled` (the
toggle, persisted to the machine-global file), :func:`~stabbur.mcp_catalog.seed_global_defaults`
(the default-on seed), and the ``/api/mcp/servers`` routes that put them in front of the UI —
including the honest restart semantics for a disable that can't take effect in-process.
"""

import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from stabbur import mcp_catalog, mcpservers, tools
from stabbur.app import create_app
from stabbur.config import Settings
from stabbur.mcpservers import McpServer


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point both config layers at ``tmp_path``: a throwaway XDG global and an empty project dir."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)  # bundled() / the routes resolve the project from cwd
    return proj


# --- mcp_catalog.bundled: the shipped set + its resolved state --------------------------------------


def test_bundled_lists_shipped_servers_disabled_by_default(isolated: Path) -> None:
    entries = mcp_catalog.bundled()
    by_name = {e.name: e for e in entries}
    # The set comes from the installed plugins' own advertisements — datetime is always among them.
    assert "datetime" in by_name
    assert by_name["datetime"].command == "stabbur-mcp-datetime"
    assert by_name["datetime"].description  # the plugin's own description rides along for the UI
    # Nothing configured yet: everything is visible but off — the point of the whole feature.
    assert all(e.enabled is False and e.scope is None for e in entries)
    assert entries == sorted(entries, key=lambda e: e.name)


def test_bundled_marks_a_globally_enabled_server(isolated: Path) -> None:
    mcpservers.add(McpServer(name="datetime", command="stabbur-mcp-datetime"), glob=True)
    entry = next(e for e in mcp_catalog.bundled() if e.name == "datetime")
    assert entry.enabled is True and entry.scope == "global"


def test_bundled_marks_a_project_enabled_server(isolated: Path) -> None:
    mcpservers.add(McpServer(name="files", command="stabbur-mcp-files"), glob=False, project_dir=isolated)
    entry = next(e for e in mcp_catalog.bundled() if e.name == "files")
    assert entry.enabled is True and entry.scope == "project"


def test_bundled_honors_a_project_disable_marker(isolated: Path) -> None:
    # A global server the project disables is NOT enabled — bundled() reports the resolved truth.
    mcpservers.add(McpServer(name="datetime", command="stabbur-mcp-datetime"), glob=True)
    (isolated / ".mcp.json").write_text(json.dumps({"mcpServers": {"datetime": {"disabled": True}}}))
    entry = next(e for e in mcp_catalog.bundled() if e.name == "datetime")
    assert entry.enabled is False and entry.scope is None


def test_bundled_includes_uninstalled_optional_with_hint(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An optional first-party server whose extra isn't installed stays listed (not hidden) so the UI
    # can explain why it would report zero tools.
    from stabbur import plugins

    monkeypatch.setattr(mcp_catalog, "uninstalled_optional", lambda _advertised: mcp_catalog.OPTIONAL_FIRST_PARTY)
    monkeypatch.setattr(plugins, "advertised_servers", lambda _pm: [])
    entry = next(e for e in mcp_catalog.bundled() if e.name == "web")
    assert entry.installed is False and "install with" in entry.setup


# --- mcp_catalog.set_enabled: the toggle, persisted to the global file -------------------------------


def test_set_enabled_writes_and_removes_the_global_entry(isolated: Path) -> None:
    entry = mcp_catalog.set_enabled("datetime", True)
    assert entry.enabled is True and entry.scope == "global"
    # Persisted in the same file `stabbur mcp add --global` writes, in the standard mcpServers shape.
    data = json.loads(mcpservers.global_path().read_text())
    assert data == {"mcpServers": {"datetime": {"command": "stabbur-mcp-datetime"}}}
    assert mcp_catalog.set_enabled("datetime", True).enabled is True  # idempotent
    off = mcp_catalog.set_enabled("datetime", False)
    assert off.enabled is False and off.scope is None
    assert mcp_catalog.set_enabled("datetime", False).enabled is False  # idempotent the other way


def test_set_enabled_rejects_an_unbundled_name(isolated: Path) -> None:
    # The toggle is an allow-list over the shipped set — it can never spawn an arbitrary command.
    with pytest.raises(mcp_catalog.UnknownServer):
        mcp_catalog.set_enabled("rm-rf", True)


def test_disable_of_a_project_scoped_server_refuses(isolated: Path) -> None:
    # stabbur never rewrites the committed, portable project .mcp.json from a toggle; it says so instead.
    mcpservers.add(McpServer(name="files", command="stabbur-mcp-files"), glob=False, project_dir=isolated)
    with pytest.raises(mcp_catalog.ProjectScoped, match=".mcp.json"):
        mcp_catalog.set_enabled("files", False)


# --- declared settings: the value in force, and writing one back ------------------------------------


def test_bundled_reports_the_effective_root_of_an_unconfigured_server(isolated: Path) -> None:
    # The bug this exists for: nothing configured, so `files` is rooted at wherever stabbur serve runs —
    # true, invisible, and the reason "what are my directories in ~/dev" answered about the checkout.
    root = next(s for s in _settings(isolated, "files") if s.env == "STABBUR_FILES_ROOT")
    assert root.type == "path" and root.default == "."
    assert root.effective == str(isolated.resolve())  # absolute: the answer a user can act on
    assert Path(root.effective).is_absolute()


def test_bundled_reports_a_configured_value_over_the_default(isolated: Path, tmp_path: Path) -> None:
    mcpservers.add(
        McpServer(name="files", command="stabbur-mcp-files", env={"STABBUR_FILES_ROOT": str(tmp_path)}), glob=True
    )
    entry = next(e for e in mcp_catalog.bundled() if e.name == "files")
    assert entry.env == {"STABBUR_FILES_ROOT": str(tmp_path)}  # what is written down
    root = next(s for s in entry.settings if s.env == "STABBUR_FILES_ROOT")
    assert root.effective == str(tmp_path)  # ...and what is in force


def test_booleans_are_always_canonical(isolated: Path) -> None:
    # "" is a startup error for a bool field, so a boolean is never blank in either direction.
    mcpservers.add(McpServer(name="files", command="stabbur-mcp-files", env={"STABBUR_FILES_WRITABLE": "1"}), glob=True)
    assert next(s for s in _settings(isolated, "files") if s.env == "STABBUR_FILES_WRITABLE").effective == "true"
    entry = mcp_catalog.set_env("files", {"STABBUR_FILES_WRITABLE": ""})
    assert entry.env["STABBUR_FILES_WRITABLE"] == "false"


def test_set_env_persists_expanding_a_tilde(isolated: Path) -> None:
    mcp_catalog.set_enabled("files", True)
    entry = mcp_catalog.set_env("files", {"STABBUR_FILES_ROOT": "~/dev"})
    # Nothing between mcp.json and the spawned process expands ~, so a literal "~/dev" would sandbox
    # the assistant to a directory named "~" — the one thing that must not survive the write.
    assert entry.env["STABBUR_FILES_ROOT"] == str(Path.home() / "dev")
    data = json.loads(mcpservers.global_path().read_text())
    assert data["mcpServers"]["files"] == {
        "command": "stabbur-mcp-files",
        "env": {"STABBUR_FILES_ROOT": str(Path.home() / "dev")},
    }


def test_set_env_clears_a_value_back_to_the_default(isolated: Path, tmp_path: Path) -> None:
    mcp_catalog.set_enabled("files", True)
    mcp_catalog.set_env("files", {"STABBUR_FILES_ROOT": str(tmp_path)})
    entry = mcp_catalog.set_env("files", {"STABBUR_FILES_ROOT": ""})
    assert entry.env == {}  # cleared, not written as an empty string
    assert next(s for s in entry.settings if s.env == "STABBUR_FILES_ROOT").effective == str(isolated.resolve())


def test_set_env_rejects_an_undeclared_variable(isolated: Path) -> None:
    # The allow-list: a request can never inject arbitrary env (PATH, LD_PRELOAD) into a spawned server.
    mcp_catalog.set_enabled("files", True)
    with pytest.raises(mcp_catalog.UnknownSetting, match="LD_PRELOAD"):
        mcp_catalog.set_env("files", {"LD_PRELOAD": "/tmp/evil.so"})
    assert mcpservers.read_global()[0].env == {}  # and nothing was written


def test_set_env_refuses_a_switched_off_server(isolated: Path) -> None:
    # Its settings live in the mcp.json entry, so writing them would create it — i.e. switch it on.
    with pytest.raises(mcp_catalog.NotConfigured):
        mcp_catalog.set_env("files", {"STABBUR_FILES_ROOT": "/tmp"})
    assert mcpservers.read_global() == []


def test_set_env_refuses_a_project_scoped_server(isolated: Path) -> None:
    mcpservers.add(McpServer(name="files", command="stabbur-mcp-files"), glob=False, project_dir=isolated)
    with pytest.raises(mcp_catalog.ProjectScoped, match=".mcp.json"):
        mcp_catalog.set_env("files", {"STABBUR_FILES_ROOT": "/tmp"})


def test_re_enabling_keeps_configured_settings(isolated: Path, tmp_path: Path) -> None:
    # set_enabled re-writes the entry; it must carry the env through or a stray toggle silently
    # resets the root the user configured.
    mcp_catalog.set_enabled("files", True)
    mcp_catalog.set_env("files", {"STABBUR_FILES_ROOT": str(tmp_path)})
    assert mcp_catalog.set_enabled("files", True).env == {"STABBUR_FILES_ROOT": str(tmp_path)}


def test_a_server_with_no_env_declares_nothing(isolated: Path) -> None:
    assert _settings(isolated, "datetime") == []  # no knobs invented for a server that reads none


def _settings(project_dir: Path, name: str) -> list[Any]:
    """The declared settings of one bundled server, with their effective values."""
    return list(next(e for e in mcp_catalog.bundled(project_dir) if e.name == name).settings)


# --- mcp_catalog.seed_global_defaults: the fresh-machine default ------------------------------------


def test_seed_creates_the_global_file_with_datetime(isolated: Path) -> None:
    assert mcp_catalog.seed_global_defaults() == ["datetime"]
    assert [s.name for s in mcpservers.read_global()] == ["datetime"]


def test_seed_is_a_noop_once_the_file_exists(isolated: Path) -> None:
    # An emptied file is a deliberate "no tools" — re-seeding it every startup would fight the user.
    mcpservers.global_path().parent.mkdir(parents=True, exist_ok=True)
    mcpservers.global_path().write_text(json.dumps({"mcpServers": {}}))
    assert mcp_catalog.seed_global_defaults() == []
    assert mcpservers.read_global() == []
    # `stabbur setup` asks first, so it fills an existing-but-empty file.
    assert mcp_catalog.seed_global_defaults(only_if_absent=False) == ["datetime"]


# --- MCPBridge.add_server: enabling without a restart -----------------------------------------------


class _FakeTool:
    """A stand-in for an MCP tool as returned by ``client.list_tools()``."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = ""
        self.inputSchema: dict[str, Any] = {"type": "object", "properties": {}}
        self.annotations = type("A", (), {"readOnlyHint": True})()


class _FakeClient:
    """A stub MCP client exposing one fixed tool."""

    def __init__(self, name: str) -> None:
        self._tools = [_FakeTool(name)]

    async def list_tools(self) -> list[_FakeTool]:
        return self._tools


def _bridge(*, fail: bool = False) -> tuple[tools.MCPBridge, list[str]]:
    """A bridge whose ``_spawn`` adds a fake tool (or records a failure) instead of launching anything."""
    toolset = tools.MCPToolset()
    bridge = tools.MCPBridge(toolset, AsyncExitStack())
    spawned: list[str] = []

    async def fake_spawn(prefix: str, server: McpServer) -> bool:
        spawned.append(prefix)
        if fail:
            toolset.errors.append((server.name, "[Errno 2] no such file"))
            return False
        await toolset.add(_FakeClient("today"), prefix)  # type: ignore[arg-type]
        return True

    bridge._spawn = fake_spawn  # type: ignore[method-assign]
    return bridge, spawned


async def test_add_server_attaches_live() -> None:
    bridge, spawned = _bridge()
    server = McpServer(name="datetime", command="stabbur-mcp-datetime")
    assert bridge.is_live(server) is False
    attached, reason = await bridge.add_server(server)
    assert attached is True and reason == ""
    assert bridge.toolset.names == ["datetime__today"]
    assert bridge.is_live(server) is True
    # A second enable is a no-op, not a second namespace (`datetime2`).
    assert await bridge.add_server(server) == (True, "already attached")
    assert spawned == ["datetime"]


async def test_add_server_reports_a_spawn_failure() -> None:
    bridge, _ = _bridge(fail=True)
    attached, reason = await bridge.add_server(McpServer(name="web", command="stabbur-mcp-web"))
    assert attached is False and "Errno 2" in reason  # a real failure, never a fake success
    assert bridge.pending_prefixes == {"web"}  # stays pending -> the existing retry contract holds


# --- /api/mcp/servers routes ------------------------------------------------------------------------


@pytest.fixture
def app(isolated: Path) -> FastAPI:
    return create_app(Settings(serve_model=None))


@pytest.fixture
async def client(app: FastAPI):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_get_lists_bundled_servers_with_state(client: AsyncClient) -> None:
    body = (await client.get("/api/mcp/servers")).json()
    names = {e["name"] for e in body}
    assert {"datetime", "files", "git"} <= names  # the shipped set is visible before anything is configured
    assert all(e["enabled"] is False for e in body)


async def test_get_reports_the_scope_that_switched_each_server_on(isolated: Path, client: AsyncClient) -> None:
    # `scope` is not decoration: a new chat's tool allow-list starts from the baseline of the
    # servers a *project* switched on (its assistant exists to use them) plus datetime, so a
    # project-scoped server has to be distinguishable over the wire from a machine-global one.
    mcpservers.add(McpServer(name="files", command="stabbur-mcp-files"), glob=False, project_dir=isolated)
    mcpservers.add(McpServer(name="git", command="stabbur-mcp-git"), glob=True)
    rows = {e["name"]: e for e in (await client.get("/api/mcp/servers")).json()}
    assert rows["files"]["scope"] == "project"
    assert rows["git"]["scope"] == "global"
    assert rows["datetime"]["scope"] is None  # off: nothing switched it on


async def test_enable_persists_and_attaches_live(app: FastAPI, client: AsyncClient) -> None:
    bridge, _ = _bridge()
    app.state.mcp_bridge = bridge  # lifespan doesn't run under ASGITransport
    r = await client.post("/api/mcp/servers/datetime", json={"enabled": True})
    assert r.status_code == 200
    body = r.json()
    assert body["server"]["enabled"] is True and body["server"]["scope"] == "global"
    assert body["applied"] is True and body["restart_required"] is False
    assert bridge.toolset.names == ["datetime__today"]  # callable on the next chat turn, no restart
    assert [s.name for s in mcpservers.read_global()] == ["datetime"]  # and it survives one


async def test_enable_without_a_bridge_says_restart(client: AsyncClient) -> None:
    # No bridge to attach to: the response must say "restart", never claim a silent success.
    body = (await client.post("/api/mcp/servers/datetime", json={"enabled": True})).json()
    assert body["applied"] is False and body["restart_required"] is True
    assert "restart" in body["detail"]


async def test_enable_reports_a_failed_spawn(app: FastAPI, client: AsyncClient) -> None:
    bridge, _ = _bridge(fail=True)
    app.state.mcp_bridge = bridge
    body = (await client.post("/api/mcp/servers/datetime", json={"enabled": True})).json()
    assert body["server"]["enabled"] is True  # the config change stands
    assert body["applied"] is False and body["restart_required"] is False  # a restart wouldn't help
    assert "could not start" in body["detail"]


async def test_disable_of_a_running_server_requires_a_restart(app: FastAPI, client: AsyncClient) -> None:
    bridge, _ = _bridge()
    app.state.mcp_bridge = bridge
    await client.post("/api/mcp/servers/datetime", json={"enabled": True})
    body = (await client.post("/api/mcp/servers/datetime", json={"enabled": False})).json()
    assert body["server"]["enabled"] is False  # persisted immediately
    assert body["applied"] is False and body["restart_required"] is True
    assert bridge.toolset.names == ["datetime__today"]  # honest: the subprocess is still attached


async def test_disable_of_a_never_spawned_server_applies_at_once(app: FastAPI, client: AsyncClient) -> None:
    bridge, _ = _bridge()
    app.state.mcp_bridge = bridge
    mcpservers.add(McpServer(name="datetime", command="stabbur-mcp-datetime"), glob=True)  # enabled, not spawned
    body = (await client.post("/api/mcp/servers/datetime", json={"enabled": False})).json()
    assert body["applied"] is True and body["restart_required"] is False


async def test_unknown_server_is_404(client: AsyncClient) -> None:
    assert (await client.post("/api/mcp/servers/rm-rf", json={"enabled": True})).status_code == 404


async def test_disable_of_a_project_scoped_server_is_409(isolated: Path, client: AsyncClient) -> None:
    mcpservers.add(McpServer(name="files", command="stabbur-mcp-files"), glob=False, project_dir=isolated)
    r = await client.post("/api/mcp/servers/files", json={"enabled": False})
    assert r.status_code == 409
    assert ".mcp.json" in r.json()["detail"]


async def test_enable_blocked_by_a_project_disable_marker(isolated: Path, client: AsyncClient) -> None:
    # The global write lands, but the project's disable marker wins — and no restart will change that.
    (isolated / ".mcp.json").write_text(json.dumps({"mcpServers": {"datetime": {"disabled": True}}}))
    body = (await client.post("/api/mcp/servers/datetime", json={"enabled": True})).json()
    assert body["server"]["enabled"] is False
    assert body["applied"] is False and body["restart_required"] is False
    assert "disables" in body["detail"]


# --- /api/mcp/servers: the settings half ------------------------------------------------------------


async def test_get_carries_declared_settings_with_effective_values(isolated: Path, client: AsyncClient) -> None:
    body = (await client.get("/api/mcp/servers")).json()
    files = next(e for e in body if e["name"] == "files")
    assert files["env"] == {}  # nothing persisted...
    root = next(s for s in files["settings"] if s["env"] == "STABBUR_FILES_ROOT")
    # ...yet the card can still say exactly which directory the assistant can browse.
    assert root["label"] and root["type"] == "path" and root["effective"] == str(isolated.resolve())


async def test_env_change_on_a_pending_server_applies_without_a_restart(app: FastAPI, client: AsyncClient) -> None:
    bridge, _ = _bridge()
    app.state.mcp_bridge = bridge
    server = McpServer(name="files", command="stabbur-mcp-files")
    mcpservers.add(server, glob=True)
    bridge._pending["files"] = server  # configured at startup, queued for a lazy first-use spawn
    r = await client.post("/api/mcp/servers/files", json={"env": {"STABBUR_FILES_ROOT": "/tmp"}})
    body = r.json()
    assert body["applied"] is True and body["restart_required"] is False
    assert bridge._pending["files"].env == {"STABBUR_FILES_ROOT": "/tmp"}  # the queued spawn got the new env
    assert body["server"]["settings"][0]["effective"] == "/tmp"


async def test_env_change_on_a_running_server_requires_a_restart(app: FastAPI, client: AsyncClient) -> None:
    bridge, _ = _bridge()
    app.state.mcp_bridge = bridge
    await client.post("/api/mcp/servers/files", json={"enabled": True})  # attaches live
    body = (await client.post("/api/mcp/servers/files", json={"env": {"STABBUR_FILES_ROOT": "/tmp"}})).json()
    # A running process cannot be handed a new environment; say so rather than report a success.
    assert body["applied"] is False and body["restart_required"] is True
    assert "restart" in body["detail"]
    assert body["server"]["env"] == {"STABBUR_FILES_ROOT": "/tmp"}  # the write itself did land


async def test_env_change_without_a_bridge_applies(client: AsyncClient) -> None:
    # No bridge = no MCP subprocess in this process at all, so the persisted value is the whole truth.
    await client.post("/api/mcp/servers/files", json={"enabled": True})
    body = (await client.post("/api/mcp/servers/files", json={"env": {"STABBUR_FILES_WRITABLE": "true"}})).json()
    assert body["applied"] is True and body["restart_required"] is False


async def test_undeclared_env_is_400(client: AsyncClient) -> None:
    await client.post("/api/mcp/servers/files", json={"enabled": True})
    r = await client.post("/api/mcp/servers/files", json={"env": {"PATH": "/tmp"}})
    assert r.status_code == 400 and "PATH" in r.json()["detail"]


async def test_env_on_a_switched_off_server_is_409(client: AsyncClient) -> None:
    r = await client.post("/api/mcp/servers/files", json={"env": {"STABBUR_FILES_ROOT": "/tmp"}})
    assert r.status_code == 409 and "switch" in r.json()["detail"]


async def test_env_on_an_unknown_server_is_404(client: AsyncClient) -> None:
    assert (await client.post("/api/mcp/servers/rm-rf", json={"env": {"X": "1"}})).status_code == 404


async def test_an_empty_change_is_400(client: AsyncClient) -> None:
    assert (await client.post("/api/mcp/servers/files", json={})).status_code == 400


async def test_toggle_is_covered_by_the_cross_site_guard(client: AsyncClient) -> None:
    # A drive-by page must not be able to switch a shell/exec server on.
    r = await client.post("/api/mcp/servers/shell", json={"enabled": True}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
