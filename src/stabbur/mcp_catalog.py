"""The MCP tool servers stabbur knows about, shared by the CLI and the web layer.

Two lists:

* :data:`CURATED` — publicly-installable servers ``stabbur mcp add <name>`` can drop into a
  project's ``stabbur.toml`` (they run via ``uvx``/``bunx`` — no prior install).
* :data:`OPTIONAL_FIRST_PARTY` — stabbur's own servers that live behind an extra (a heavy dep),
  so they don't advertise until installed. Kept here with an install hint so they stay
  discoverable — and so the CLI (``stabbur mcp add``) and the web health menu can both tell a
  user *why* a listed server reports zero tools ("install with: make install-web").

…plus the **bundled** set: :func:`bundled` pairs the ``stabbur-mcp-*`` servers stabbur ships (read from
the plugins' own advertisements, so there is no second hardcoded list) with their resolved on/off
state, and :func:`set_enabled` flips one by writing the machine-global ``mcp.json`` — the same file
``stabbur mcp add --global`` writes, so enabling from the web UI and from the CLI stay one source of
truth. :func:`seed_global_defaults` is the fresh-machine seed behind :data:`DEFAULT_ENABLED`.

:func:`bundled` also resolves each server's declared **settings** (:class:`stabbur.models.McpSetting`) to
the value actually in force, and :func:`set_env` writes one back. That closes a real gap: a server's
env decides what it can reach — ``STABBUR_FILES_ROOT`` is the whole of what the assistant can browse —
yet an unset default like ``.`` is invisible from outside the process, so "a configured workspace
root" was the only thing a UI could say and hand-editing JSON the only way to change it.
"""

import os
import shlex
from pathlib import Path

from stabbur import mcpservers
from stabbur.mcpservers import McpServer
from stabbur.models import BundledMcp, CuratedMcp, McpSetting, McpSettingKind

# DHIS2 leads (stabbur's north star); the rest are general tools. Commands with placeholders
# (a path, a profile) are meant to be edited — the `setup` note says what.
CURATED: list[CuratedMcp] = [
    CuratedMcp(
        name="dhis2",
        command="env DHIS2_PROFILE=play42 DHIS2_MCP_READONLY=1 uvx dhis2w-mcp-bridge",
        description="DHIS2 CLI bridge — one `dhis2_cli` tool (best for smaller models). "
        "Swap uvx dhis2w-mcp-bridge for dhis2w-mcp-router or dhis2w-mcp for bigger models.",
        setup="set DHIS2_PROFILE to a profile in ~/.config/dhis2/profiles.toml; "
        "drop DHIS2_MCP_READONLY to allow writes",
    ),
    CuratedMcp(
        name="fetch",
        command="uvx mcp-server-fetch",
        description="Fetch a URL and return its content as markdown.",
    ),
    # (No `time` server — the installed `datetime` plugin already covers time/timezone/calendar.)
    # (No `git` server — the bundled first-party `stabbur-mcp-git` plugin covers read-only git
    # inspection, sandboxed and dependency-light, so the external `mcp-server-git` is redundant.)
    CuratedMcp(
        name="sqlite",
        command="uvx mcp-server-sqlite --db-path ./data.db",
        description="Query a SQLite database.",
        setup="point --db-path at your .db file",
    ),
    CuratedMcp(
        name="filesystem",
        command="bunx @modelcontextprotocol/server-filesystem .",
        description="Read and write files under a directory.",
        setup="pass the directory to expose (default: here); needs bun (bunx)",
    ),
    CuratedMcp(
        name="playwright",
        command="bunx @playwright/mcp@latest --headless --isolated",
        description="Drive a real browser: navigate, read the page (snapshot/find), click, fill "
        "forms, screenshot. Handles JavaScript pages; a vision model also sees the screenshots.",
        setup="needs bun (bunx); downloads a browser on first run. Runs headless — remove "
        "--headless to watch/drive the window, drop --isolated to keep a login between runs",
    ),
]


OPTIONAL_FIRST_PARTY: list[CuratedMcp] = [
    CuratedMcp(
        name="web",
        command="stabbur-mcp-web",
        description="Read a web page in a headless browser and return its main content as Markdown.",
        setup="install with: make install-web  (Playwright + Chromium)",
    ),
]


# stabbur's default-on tool set for a fresh machine — what a chat gets before anyone edits JSON.
# Deliberately tiny: a model with no clock confidently invents the date (the one gap that makes an
# assistant look broken on question one), while every *other* bundled server touches the disk, the
# network, or a shell and must stay an explicit opt-in.
DEFAULT_ENABLED: tuple[str, ...] = ("datetime",)


class UnknownServer(LookupError):
    """A toggle named a server stabbur doesn't bundle — surfaced as a 404, not a traceback."""


class UnknownSetting(LookupError):
    """An env write named a variable the server never declared — surfaced as a 400, not a write.

    The settings API is an allow-list over what a server *says* it reads, for the same reason the
    toggle is one over the shipped set: an arbitrary ``{"env": {...}}`` would otherwise let a request
    inject any environment variable into a spawned subprocess (``PATH``, ``LD_PRELOAD``, a proxy).
    """


class NotConfigured(RuntimeError):
    """An env write for a server with no ``mcp.json`` entry to hold it — i.e. one that is switched off.

    Settings live *inside* the server's entry, so writing them for a disabled server would create that
    entry — which is exactly what "enabled" means, silently switching the server on as a side effect of
    typing in a text field. Refused instead: switch it on, then configure it.
    """


class ProjectScoped(RuntimeError):
    """A disable that can't be honored globally: the project's own ``.mcp.json`` switches it on.

    The global file is the only one :func:`set_enabled` writes — a project ``.mcp.json`` is committed,
    portable, and belongs to the assistant, so silently editing it from the web UI would rewrite the
    user's repo. Raised instead, with the file to edit, so the caller can say so plainly.
    """


def uninstalled_optional(advertised: set[str]) -> list[CuratedMcp]:
    """Optional first-party servers not currently installed (i.e. not advertised)."""
    return [s for s in OPTIONAL_FIRST_PARTY if s.name not in advertised]


def to_server(entry: BundledMcp) -> McpServer:
    """The ``mcpServers`` entry that runs ``entry`` (its console script split into command + args).

    Bundled servers are plain console scripts (``stabbur-mcp-datetime``), never the ``env VAR=val …``
    prefixed commands the external catalog carries — so a bare :func:`shlex.split` is the whole
    parse here (the CLI's ``_to_mcp_server`` keeps the env-prefix handling for catalog entries).

    Carries the entry's ``env`` through, so re-writing a server (a re-enable, a settings change)
    never silently drops the configuration already in its ``mcp.json`` entry.
    """
    argv = shlex.split(entry.command)
    return McpServer(name=entry.name, command=argv[0], args=argv[1:], env=dict(entry.env))


# What pydantic-settings accepts as a true boolean env value; anything else it treats as false (or
# rejects). Writes are canonicalized to "true"/"false", so this only has to forgive a hand-edited file.
_TRUTHY = frozenset({"1", "true", "t", "yes", "y", "on"})


def _canonical(setting: McpSetting, value: str) -> str:
    """One env value as it should be *written*: a shape the server will actually parse.

    Booleans become ``"true"`` / ``"false"`` (an empty string is a startup error for a bool field,
    never "unset"), and a path gets its ``~`` expanded — nothing between ``mcp.json`` and the spawned
    process does that, so a literal ``~/dev`` would become a directory named ``~``. A relative path is
    left relative: ``.`` deliberately means "wherever stabbur runs", and resolving it at write time would
    freeze that in a file that outlives the shell it was typed in.
    """
    if setting.type is McpSettingKind.boolean:
        return "true" if value.strip().lower() in _TRUTHY else "false"
    if setting.type is McpSettingKind.path:
        return str(Path(value.strip()).expanduser()) if value.strip() else ""
    return value.strip()


def _effective(setting: McpSetting, env: dict[str, str]) -> McpSetting:
    """``setting`` with :attr:`~stabbur.models.McpSetting.effective` filled in from the configured ``env``.

    The configured value when there is one, else the default — then *resolved*, which is the point:
    a ``files`` root of ``.`` is a true answer that tells a user nothing, while
    ``/Users/me/dev/some-repo`` is the one that explains why the assistant listed those directories.
    Resolution is against ``stabbur serve``'s own working directory because that is what the spawned
    child inherits.
    """
    raw = env.get(setting.env) or setting.default
    if setting.type is McpSettingKind.boolean:
        return setting.model_copy(update={"effective": "true" if raw.strip().lower() in _TRUTHY else "false"})
    if setting.type is McpSettingKind.path and raw:
        # abspath, not resolve(): normalizes and absolutizes without following symlinks, so the value
        # shown is the one the user set (/Volumes/Library/…), not its physical target.
        return setting.model_copy(update={"effective": os.path.abspath(Path(raw).expanduser())})
    return setting.model_copy(update={"effective": raw})


def bundled(project_dir: Path | None = None) -> list[BundledMcp]:
    """Every first-party MCP server stabbur ships, each with its live enabled state, sorted by name.

    The shipped set comes from the plugins' own ``mcp_servers`` advertisements (:mod:`stabbur.plugins`),
    so it is exactly what ``stabbur mcp list`` shows and never drifts from what's installed; the
    optional first-party servers whose extra isn't installed yet are folded in with ``installed=False``
    and their install hint, so the whole shipped surface is one list rather than "invisible until
    installed". ``enabled`` is the honest resolved answer — the name is in :func:`stabbur.mcpservers.resolve`
    — which is what stabbur actually spawns, including a project override or disable marker.
    """
    from stabbur import plugins  # noqa: PLC0415 - drags in typer/pluginkit; kept off module load

    resolved = {s.name: s for s in mcpservers.resolve(project_dir)}
    in_project = {s.name for s in mcpservers.read_project(project_dir)}
    advertised = plugins.advertised_servers(plugins.manager())
    out = [
        BundledMcp(
            name=s.name,
            command=s.command,
            description=s.description,
            enabled=s.name in resolved,
            # A name in both files resolves to the project's entry, so that's the file that owns it.
            scope=("project" if s.name in in_project else "global") if s.name in resolved else None,
            # The env of the entry that actually resolves it — the project's when a project overrides.
            env=dict(resolved[s.name].env) if s.name in resolved else {},
            # Filled in even when the server is off: "where is files rooted" is a question a user asks
            # *before* switching it on, and answering it is half of why the declaration exists.
            settings=[_effective(spec, resolved[s.name].env if s.name in resolved else {}) for spec in s.settings],
        )
        for s in advertised
    ]
    out += [
        BundledMcp(
            name=c.name,
            command=c.command,
            description=c.description,
            enabled=c.name in resolved,
            scope=("project" if c.name in in_project else "global") if c.name in resolved else None,
            installed=False,
            setup=c.setup,
        )
        for c in uninstalled_optional({s.name for s in advertised})
    ]
    return sorted(out, key=lambda s: s.name)


def set_enabled(name: str, enabled: bool, project_dir: Path | None = None) -> BundledMcp:
    """Switch a bundled server on/off in the machine-global ``mcp.json``; return its refreshed state.

    One writer, one file: this goes through :func:`stabbur.mcpservers.add` / :func:`~stabbur.mcpservers.remove`
    against the same ``~/.config/stabbur/mcp.json`` that ``stabbur mcp add --global`` writes, so a toggle from
    the web UI is indistinguishable from the CLI's and both survive a restart. Idempotent in both
    directions. Raises :class:`UnknownServer` for a name stabbur doesn't bundle (the toggle is an allow-list
    over the shipped set — a client can never make stabbur spawn an arbitrary command), and
    :class:`ProjectScoped` when a disable would need the project's own ``.mcp.json`` edited instead.

    Note this only changes what stabbur *would* spawn. Whether the running server picks it up is the
    caller's job (see :meth:`stabbur.tools.MCPBridge.add_server`) — this function never lies about that.
    """
    entry = next((s for s in bundled(project_dir) if s.name == name), None)
    if entry is None:
        raise UnknownServer(name)
    if enabled:
        if not entry.enabled or entry.scope == "global":
            mcpservers.add(to_server(entry), glob=True)
    else:
        if entry.scope == "project":
            raise ProjectScoped(str(mcpservers.project_path(project_dir)))
        mcpservers.remove(name, glob=True)
    refreshed = next((s for s in bundled(project_dir) if s.name == name), None)
    # bundled() is stable across the write (the shipped set doesn't change), so this can't be None —
    # but fall back to the pre-write entry rather than asserting, so a toggle never 500s on a race.
    return refreshed if refreshed is not None else entry


def set_env(name: str, values: dict[str, str], project_dir: Path | None = None) -> BundledMcp:
    """Set (or clear) declared env values on a bundled server's global ``mcp.json`` entry.

    The edit-without-JSON half of the settings feature, and the same one-writer discipline as
    :func:`set_enabled`: it merges into the existing entry and re-writes it through
    :func:`stabbur.mcpservers.add`, so a value set from the web UI is the same file, shape, and
    precedence a hand-edit would produce. An **empty value clears the variable** rather than writing
    an empty string — that is the "back to the default" affordance. A boolean is the exception: it is
    always written explicitly (``"true"``/``"false"``), because an empty string is a startup error for
    a bool field, not an absent one.

    Refuses rather than doing something surprising: :class:`UnknownSetting` for a variable the server
    never declared (the allow-list — no arbitrary env into a spawned process), :class:`NotConfigured`
    for a server that is switched off (its settings would have nowhere to live but a new entry, which
    would switch it on), and :class:`ProjectScoped` when the project's own ``.mcp.json`` owns the
    server, since a global write would be silently overridden by it.

    Like :func:`set_enabled`, this only changes what stabbur *would* spawn — a running subprocess keeps
    the environment it started with (see :meth:`stabbur.tools.MCPBridge.update_server`).
    """
    entry = next((s for s in bundled(project_dir) if s.name == name), None)
    if entry is None:
        raise UnknownServer(name)
    declared = {s.env: s for s in entry.settings}
    unknown = sorted(set(values) - set(declared))
    if unknown:
        raise UnknownSetting(f"{name!r} has no setting {unknown[0]!r}")
    if entry.scope == "project":
        raise ProjectScoped(str(mcpservers.project_path(project_dir)))
    if not entry.enabled:
        raise NotConfigured(name)
    env = dict(entry.env)
    for var, value in values.items():
        canonical = _canonical(declared[var], value)
        if canonical:
            env[var] = canonical
        else:
            env.pop(var, None)
    mcpservers.add(to_server(entry.model_copy(update={"env": env})), glob=True)
    refreshed = next((s for s in bundled(project_dir) if s.name == name), None)
    return refreshed if refreshed is not None else entry


def seed_global_defaults(*, only_if_absent: bool = True) -> list[str]:
    """Write :data:`DEFAULT_ENABLED` into the global ``mcp.json``; return the names actually seeded.

    A fresh machine otherwise has *no* tools at all: ``stabbur setup`` seeds this, but nothing forces a
    user through setup, so anyone who went straight to ``stabbur serve`` gets a Tools panel that reads
    "no MCP servers configured" while a dozen installed servers sit unused.

    ``only_if_absent`` (the default, and what the serve lifespan uses) makes this a no-op once the file
    exists — guarded on **absent**, never on empty, because an empty file is a user who removed
    everything on purpose and re-seeding would fight them on every startup. ``stabbur setup`` asks first,
    so it passes ``False`` to also fill an existing-but-empty file. A default whose plugin isn't
    installed is skipped rather than written as a dead entry.
    """
    from stabbur import plugins  # noqa: PLC0415 - drags in typer/pluginkit; kept off module load

    if only_if_absent and mcpservers.global_path().exists():
        return []
    advertised = {s.name: s for s in plugins.advertised_servers(plugins.manager())}
    seeded: list[str] = []
    for name in DEFAULT_ENABLED:
        server = advertised.get(name)
        if server is None:
            continue
        mcpservers.add(McpServer(name=server.name, command=server.command), glob=True)
        seeded.append(server.name)
    return seeded


def optional_hint(server_or_command: str) -> str | None:
    """Install hint for a known optional first-party server (matched by name or command), else None.

    Lets the web health menu / ``stabbur mcp add`` explain a server that reports zero tools because
    its extra isn't installed (e.g. ``web`` needs ``make install-web``).
    """
    for server in OPTIONAL_FIRST_PARTY:
        if server_or_command in (server.name, server.command):
            return server.setup
    return None
