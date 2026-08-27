"""MCP tool config in the ecosystem-standard ``mcpServers`` JSON shape.

stabbur reads and writes the same ``mcpServers`` format Claude Desktop / Claude Code / Cursor use,
so a server's README snippet pastes straight in. It lives at two levels that **merge**:

- **project** — ``.mcp.json`` next to the discovered ``stabbur.toml`` (the assistant's own tools),
  or ``./.mcp.json`` outside a project;
- **global** — ``~/.config/stabbur/mcp.json`` (machine-wide defaults, e.g. what free-play chat gets).

A server entry is ``{ "command": ..., "args": [...], "env": {...} }`` keyed by name; stabbur's bundled
first-party servers are entered by their package (``stabbur-mcp-*``). A project entry may instead be a
**disable marker** — ``null`` or ``{"disabled": true}`` — which drops a same-named global server from
the effective set (a project excluding an unwanted machine-global tool). :func:`resolve` returns the
effective set — global first, then project (a project name overrides *or disables* a global one); the
CLI's ``--mcp`` is layered on top by the caller. This is deliberately separate from ``stabbur.toml``, which
stays the portable assistant manifest (model + prompt + libraries) and no longer carries tools.

Two properties matter because this file is **hand-edited and shared with other tools**:

- **The writer preserves what it doesn't model.** :func:`add` / :func:`remove` are a read-modify-write
  over the raw JSON: they touch exactly one ``mcpServers`` key and hand everything else back verbatim —
  ``$schema``, ``inputs``, disable markers on *other* names, and per-server fields stabbur has no opinion
  about (``autoApprove``, ``timeout``, …). It never rebuilds the file from stabbur's own model.
- **One bad entry never takes down the file.** An entry stabbur can't run — a remote/HTTP server
  (``{"type": "http", "url": ...}``), or something malformed — is skipped with a warning, so the
  servers around it keep working. Skipped entries are still preserved on write.
"""

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from stabbur import fsatomic, userconfig

PROJECT_FILE = ".mcp.json"

_log = logging.getLogger(__name__)


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
        """The ``(name, argv, env)`` spec :func:`stabbur.tools.connect` expects."""
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
    """The machine-global config (``<XDG config>/stabbur/mcp.json``)."""
    return userconfig.config_dir() / "mcp.json"


def project_path(project_dir: Path | None = None) -> Path:
    """The project config — ``.mcp.json`` next to the discovered ``stabbur.toml``, else ``./.mcp.json``.

    With no explicit ``project_dir`` the base is the project found by walking up from the working
    directory (:func:`stabbur.project.project_root`), so a subdirectory gets the project's tools
    rather than looking for a ``.mcp.json`` that only ever exists at the top. Outside a project
    there is nothing to walk up to and the cwd is the base, unchanged.
    """
    from stabbur import project  # noqa: PLC0415 - lazy: project imports targets, which imports project

    return (project_dir or project.project_root() or Path.cwd()) / PROJECT_FILE


def _is_disabled(entry: object) -> bool:
    """Whether an ``mcpServers`` value is a **disable marker** rather than a real server.

    Two shapes disable a server *name*: a bare ``null`` (``"foo": null``) and an object with
    ``"disabled": true`` (``"foo": {"disabled": true}``). Any other fields on a disabled object
    (e.g. a leftover ``command``) are ignored — ``disabled`` wins. See :func:`resolve` for how a
    disabled name drops a same-named global server; a disabled *global* is simply not read.
    """
    return entry is None or (isinstance(entry, dict) and entry.get("disabled") is True)


def _raw_document(path: Path) -> dict[str, object]:
    """The file's raw top-level JSON object — ``{}`` if it doesn't exist.

    The single read used by *both* the parser and the writers, so what :func:`add` writes back is
    exactly what was on disk plus one changed key. Everything outside ``mcpServers`` (``$schema``,
    ``inputs``, anything a sibling tool put there) rides along in this dict untouched.
    """
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise McpConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise McpConfigError(f"{path}: the top level must be a JSON object")
    return data


def _raw_servers(path: Path, data: dict[str, object]) -> dict[str, object]:
    """``data``'s ``mcpServers`` object — ``{}`` when absent, an error when it's the wrong type.

    Returns the *live* sub-object when there is one, so a writer mutating it mutates ``data`` and the
    surrounding keys (and the order of the servers already there) are preserved on the way back out.
    """
    servers_obj = data.get("mcpServers")
    if servers_obj is None:
        return {}  # a valid JSON object without mcpServers is just "no servers"
    if not isinstance(servers_obj, dict):
        raise McpConfigError(f"{path}: 'mcpServers' must be an object")
    return servers_obj


def _warn_unrunnable(path: Path, name: str, entry: object) -> None:
    """Warn that one entry is being skipped, saying *why* in one line — never raise.

    A whole ``mcp.json`` used to fail on the first entry without a ``command``, which killed every
    other server in it (and, for the global file, in every project). The two shapes that land here are
    a **remote/HTTP server** (``{"type": "http", "url": ...}`` — an ecosystem-standard entry stabbur
    can't spawn yet) and a genuinely malformed one; both are named so the user can find them.
    """
    if isinstance(entry, dict) and entry.get("url"):
        _log.warning("%s: remote MCP servers are not supported yet: %r (skipped)", path, name)
    else:
        _log.warning("%s: MCP server %r has no 'command' — skipped", path, name)


def _parse_file(path: Path) -> tuple[list[McpServer], set[str]]:
    """Parse one ``mcp.json`` into ``(servers, disabled_names)`` — ``([], set())`` if it doesn't exist.

    Enabled servers are the usual ``command`` + ``args`` + ``env`` entries; ``disabled_names`` are the
    names carrying a disable marker (``null`` / ``{"disabled": true}``), tolerated here (never an error)
    so :func:`resolve` can drop the matching server. An entry stabbur can't run (remote/HTTP, or
    malformed) is **skipped with a warning**, not raised — see :func:`_warn_unrunnable`; only a file
    that isn't parseable at all is a :class:`McpConfigError`. The writers (:func:`add` / :func:`remove`)
    work on the raw JSON, so both disable markers and skipped entries survive a write untouched.
    """
    servers_obj = _raw_servers(path, _raw_document(path))
    out: list[McpServer] = []
    disabled: set[str] = set()
    for name, entry in servers_obj.items():
        if _is_disabled(entry):  # a disable marker; never a parse error, other fields ignored
            disabled.add(str(name))
            continue
        if not isinstance(entry, dict) or "command" not in entry:
            _warn_unrunnable(path, str(name), entry)
            continue
        command = str(entry["command"])
        out.append(
            McpServer(
                name=str(name),
                command=command,
                args=[str(a) for a in (entry.get("args") or [])],
                env={str(k): str(v) for k, v in (entry.get("env") or {}).items()},
            )
        )
    return out, disabled


def _read_file(path: Path) -> list[McpServer]:
    """Parse one ``mcp.json`` into enabled servers — ``[]`` if it doesn't exist (disable markers ignored)."""
    return _parse_file(path)[0]


def read_global() -> list[McpServer]:
    """Servers from the machine-global ``mcp.json``."""
    return _read_file(global_path())


def read_project(project_dir: Path | None = None) -> list[McpServer]:
    """Servers from the project ``.mcp.json``."""
    return _read_file(project_path(project_dir))


def resolve(project_dir: Path | None = None) -> list[McpServer]:
    """Effective servers: global first, then project (a project name overrides a global one).

    A project ``.mcp.json`` can also **disable** a server by name (``"foo": null`` or
    ``"foo": {"disabled": true}``): that drops a same-named global server from the result, so a project
    can exclude an unwanted machine-global tool (e.g. a stray ``playwright``). A disabled *global* entry
    is dropped outright — it never enters the merged set in the first place.
    """
    global_servers, _global_disabled = _parse_file(global_path())  # disabled globals already excluded
    project_servers, project_disabled = _parse_file(project_path(project_dir))
    by_name: dict[str, McpServer] = {s.name: s for s in global_servers}
    for name in project_disabled:  # a project disable removes a same-named global
        by_name.pop(name, None)
    for server in project_servers:
        by_name[server.name] = server
    return list(by_name.values())


def _write_file(path: Path, data: dict[str, object]) -> None:
    """Write the whole raw document back to ``path`` as pretty JSON, creating parents.

    Not key-sorted: this file is hand-edited, so the author's own ordering is part of it and a write
    that reshuffles it turns a one-key change into a whole-file diff.
    """
    fsatomic.write_text(path, json.dumps(data, indent=2) + "\n")


def _merged_entry(existing: object, server: McpServer) -> dict[str, object]:
    """``server`` as an ``mcpServers`` value, keeping fields stabbur doesn't model from ``existing``.

    ``command`` / ``args`` / ``env`` are exactly what the caller asked for (an empty ``args`` or ``env``
    *removes* the key rather than keeping the old value), while anything else the entry carried —
    ``autoApprove``, ``timeout``, a sibling tool's field — is written back untouched. A disable marker
    is replaced outright: an explicit ``add`` of that name is a deliberate re-enable.
    """
    entry = server.to_entry()
    if isinstance(existing, dict) and not _is_disabled(existing):
        entry |= {k: v for k, v in existing.items() if k not in {"command", "args", "env"}}
    return entry


def add(server: McpServer, *, glob: bool, project_dir: Path | None = None) -> Path:
    """Add (or replace, by name) ``server`` in the global or project ``mcp.json``. Returns the path.

    A read-modify-write of one key: every other entry — including a disable marker on another name,
    and a remote/HTTP entry stabbur can't run — plus every top-level key outside ``mcpServers`` is
    written back exactly as it was found.
    """
    path = global_path() if glob else project_path(project_dir)
    data = _raw_document(path)
    servers = _raw_servers(path, data)
    servers[server.name] = _merged_entry(servers.get(server.name), server)
    data["mcpServers"] = servers
    _write_file(path, data)
    return path


def remove(name: str, *, glob: bool, project_dir: Path | None = None) -> Path | None:
    """Remove the named server from the global or project ``mcp.json``. Returns the path, or None if absent.

    Preserving in the same way :func:`add` is: only ``name``'s key is deleted. A name carrying a
    disable marker counts as absent — the marker is a deliberate "off", not a server to delete.
    """
    path = global_path() if glob else project_path(project_dir)
    data = _raw_document(path)
    servers = _raw_servers(path, data)
    if _is_disabled(servers.get(name)):  # covers both "missing" (None) and an explicit disable marker
        return None
    del servers[name]
    data["mcpServers"] = servers
    _write_file(path, data)
    return path
