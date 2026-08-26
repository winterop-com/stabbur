"""The MCP tool servers heim knows about, shared by the CLI and the web layer.

Two lists:

* :data:`CURATED` — publicly-installable servers ``heim mcp add <name>`` can drop into a
  project's ``heim.toml`` (they run via ``uvx``/``bunx`` — no prior install).
* :data:`OPTIONAL_FIRST_PARTY` — heim's own servers that live behind an extra (a heavy dep),
  so they don't advertise until installed. Kept here with an install hint so they stay
  discoverable — and so the CLI (``heim mcp add``) and the web health menu can both tell a
  user *why* a listed server reports zero tools ("install with: make install-web").

…plus the **bundled** set: :func:`bundled` pairs the ``heim-mcp-*`` servers heim ships (read from
the plugins' own advertisements, so there is no second hardcoded list) with their resolved on/off
state, and :func:`set_enabled` flips one by writing the machine-global ``mcp.json`` — the same file
``heim mcp add --global`` writes, so enabling from the web UI and from the CLI stay one source of
truth. :func:`seed_global_defaults` is the fresh-machine seed behind :data:`DEFAULT_ENABLED`.
"""

import shlex
from pathlib import Path

from heim import mcpservers
from heim.mcpservers import McpServer
from heim.models import BundledMcp, CuratedMcp

# DHIS2 leads (heim's north star); the rest are general tools. Commands with placeholders
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
    # (No `git` server — the bundled first-party `heim-mcp-git` plugin covers read-only git
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
        command="heim-mcp-web",
        description="Read a web page in a headless browser and return its main content as Markdown.",
        setup="install with: make install-web  (Playwright + Chromium)",
    ),
]


# heim's default-on tool set for a fresh machine — what a chat gets before anyone edits JSON.
# Deliberately tiny: a model with no clock confidently invents the date (the one gap that makes an
# assistant look broken on question one), while every *other* bundled server touches the disk, the
# network, or a shell and must stay an explicit opt-in.
DEFAULT_ENABLED: tuple[str, ...] = ("datetime",)


class UnknownServer(LookupError):
    """A toggle named a server heim doesn't bundle — surfaced as a 404, not a traceback."""


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

    Bundled servers are plain console scripts (``heim-mcp-datetime``), never the ``env VAR=val …``
    prefixed commands the external catalog carries — so a bare :func:`shlex.split` is the whole
    parse here (the CLI's ``_to_mcp_server`` keeps the env-prefix handling for catalog entries).
    """
    argv = shlex.split(entry.command)
    return McpServer(name=entry.name, command=argv[0], args=argv[1:])


def bundled(project_dir: Path | None = None) -> list[BundledMcp]:
    """Every first-party MCP server heim ships, each with its live enabled state, sorted by name.

    The shipped set comes from the plugins' own ``mcp_servers`` advertisements (:mod:`heim.plugins`),
    so it is exactly what ``heim mcp list`` shows and never drifts from what's installed; the
    optional first-party servers whose extra isn't installed yet are folded in with ``installed=False``
    and their install hint, so the whole shipped surface is one list rather than "invisible until
    installed". ``enabled`` is the honest resolved answer — the name is in :func:`heim.mcpservers.resolve`
    — which is what heim actually spawns, including a project override or disable marker.
    """
    from heim import plugins  # noqa: PLC0415 - drags in typer/pluginkit; kept off module load

    resolved = {s.name for s in mcpservers.resolve(project_dir)}
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

    One writer, one file: this goes through :func:`heim.mcpservers.add` / :func:`~heim.mcpservers.remove`
    against the same ``~/.config/heim/mcp.json`` that ``heim mcp add --global`` writes, so a toggle from
    the web UI is indistinguishable from the CLI's and both survive a restart. Idempotent in both
    directions. Raises :class:`UnknownServer` for a name heim doesn't bundle (the toggle is an allow-list
    over the shipped set — a client can never make heim spawn an arbitrary command), and
    :class:`ProjectScoped` when a disable would need the project's own ``.mcp.json`` edited instead.

    Note this only changes what heim *would* spawn. Whether the running server picks it up is the
    caller's job (see :meth:`heim.tools.MCPBridge.add_server`) — this function never lies about that.
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


def seed_global_defaults(*, only_if_absent: bool = True) -> list[str]:
    """Write :data:`DEFAULT_ENABLED` into the global ``mcp.json``; return the names actually seeded.

    A fresh machine otherwise has *no* tools at all: ``heim setup`` seeds this, but nothing forces a
    user through setup, so anyone who went straight to ``heim serve`` gets a Tools panel that reads
    "no MCP servers configured" while a dozen installed servers sit unused.

    ``only_if_absent`` (the default, and what the serve lifespan uses) makes this a no-op once the file
    exists — guarded on **absent**, never on empty, because an empty file is a user who removed
    everything on purpose and re-seeding would fight them on every startup. ``heim setup`` asks first,
    so it passes ``False`` to also fill an existing-but-empty file. A default whose plugin isn't
    installed is skipped rather than written as a dead entry.
    """
    from heim import plugins  # noqa: PLC0415 - drags in typer/pluginkit; kept off module load

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

    Lets the web health menu / ``heim mcp add`` explain a server that reports zero tools because
    its extra isn't installed (e.g. ``web`` needs ``make install-web``).
    """
    for server in OPTIONAL_FIRST_PARTY:
        if server_or_command in (server.name, server.command):
            return server.setup
    return None
