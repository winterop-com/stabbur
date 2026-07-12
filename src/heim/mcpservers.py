"""MCP tool config in the ecosystem-standard ``mcpServers`` JSON shape.

heim reads and writes the same ``mcpServers`` format Claude Desktop / Claude Code / Cursor use,
so a server's README snippet pastes straight in. It lives at two levels that **merge**:

- **project** — ``./.mcp.json`` next to ``heim.toml`` (the assistant's own tools);
- **global** — ``~/.config/heim/mcp.json`` (machine-wide defaults, e.g. what free-play chat gets).

A server entry is ``{ "command": ..., "args": [...], "env": {...} }`` keyed by name; heim's bundled
first-party servers are entered by their package (``heim-mcp-*``). :func:`resolve` returns the
effective set — global first, then project (a project name overrides a global one); the CLI's
``--mcp`` is layered on top by the caller. This is deliberately separate from ``heim.toml``, which
stays the portable assistant manifest (model + prompt + libraries) and no longer carries tools.
"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from heim import fsatomic, userconfig

PROJECT_FILE = ".mcp.json"


class McpConfigError(RuntimeError):
    """An ``mcp.json`` that exists but can't be parsed/validated — surfaced cleanly, not as a traceback."""


class McpServer(BaseModel):
    """One MCP server in ``mcpServers`` form (``command`` + ``args`` + ``env``, keyed by ``name``)."""

    model_config = ConfigDict(frozen=True)

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    def to_spec(self) -> tuple[str | None, list[str], dict[str, str]]:
        """The ``(name, argv, env)`` spec :func:`heim.tools.connect` expects."""
        return self.name, [self.command, *self.args], self.env

    def to_entry(self) -> dict[str, object]:
        """This server as an ``mcpServers`` value (omitting empty ``args`` / ``env``)."""
        entry: dict[str, object] = {"command": self.command}
        if self.args:
            entry["args"] = list(self.args)
        if self.env:
            entry["env"] = dict(self.env)
        return entry


def global_path() -> Path:
    """The machine-global config (``<XDG config>/heim/mcp.json``)."""
    return userconfig.config_dir() / "mcp.json"


def project_path(project_dir: Path | None = None) -> Path:
    """The project config (``<project_dir or cwd>/.mcp.json``)."""
    return (project_dir or Path.cwd()) / PROJECT_FILE


def _read_file(path: Path) -> list[McpServer]:
    """Parse one ``mcp.json`` into servers — ``[]`` if it doesn't exist."""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise McpConfigError(f"{path} is not valid JSON: {exc}") from exc
    servers_obj = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers_obj, dict):
        if servers_obj is None:
            return []  # a valid JSON object without mcpServers is just "no servers"
        raise McpConfigError(f"{path}: 'mcpServers' must be an object")
    out: list[McpServer] = []
    for name, entry in servers_obj.items():
        if not isinstance(entry, dict) or "command" not in entry:
            raise McpConfigError(f"{path}: server {name!r} must be an object with a 'command'")
        out.append(
            McpServer(
                name=str(name),
                command=str(entry["command"]),
                args=[str(a) for a in (entry.get("args") or [])],
                env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
            )
        )
    return out


def read_global() -> list[McpServer]:
    """Servers from the machine-global ``mcp.json``."""
    return _read_file(global_path())


def read_project(project_dir: Path | None = None) -> list[McpServer]:
    """Servers from the project ``.mcp.json``."""
    return _read_file(project_path(project_dir))


def resolve(project_dir: Path | None = None) -> list[McpServer]:
    """Effective servers: global first, then project (a project name overrides a global one)."""
    by_name: dict[str, McpServer] = {s.name: s for s in read_global()}
    for server in read_project(project_dir):
        by_name[server.name] = server
    return list(by_name.values())


def _write_file(path: Path, servers: list[McpServer]) -> None:
    """Write servers to ``path`` as an ``mcpServers`` object (pretty JSON), creating parents."""
    text = json.dumps({"mcpServers": {s.name: s.to_entry() for s in servers}}, indent=2) + "\n"
    fsatomic.write_text(path, text)


def add(server: McpServer, *, glob: bool, project_dir: Path | None = None) -> Path:
    """Add (or replace, by name) ``server`` in the global or project ``mcp.json``. Returns the path."""
    path = global_path() if glob else project_path(project_dir)
    servers = [s for s in _read_file(path) if s.name != server.name]
    servers.append(server)
    _write_file(path, servers)
    return path


def remove(name: str, *, glob: bool, project_dir: Path | None = None) -> Path | None:
    """Remove the named server from the global or project ``mcp.json``. Returns the path, or None if absent."""
    path = global_path() if glob else project_path(project_dir)
    servers = _read_file(path)
    kept = [s for s in servers if s.name != name]
    if len(kept) == len(servers):
        return None
    _write_file(path, kept)
    return path
