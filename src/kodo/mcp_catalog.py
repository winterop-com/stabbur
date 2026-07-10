"""The MCP tool servers kodo knows about, shared by the CLI and the web layer.

Two lists:

* :data:`CURATED` — publicly-installable servers ``kodo mcp add <name>`` can drop into a
  project's ``kodo.toml`` (they run via ``uvx``/``bunx`` — no prior install).
* :data:`OPTIONAL_FIRST_PARTY` — kodo's own servers that live behind an extra (a heavy dep),
  so they don't advertise until installed. Kept here with an install hint so they stay
  discoverable — and so the CLI (``kodo mcp add``) and the web health menu can both tell a
  user *why* a listed server reports zero tools ("install with: make install-web").
"""

from kodo.models import CuratedMcp

# DHIS2 leads (kodo's north star); the rest are general tools. Commands with placeholders
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
    CuratedMcp(
        name="git",
        command="uvx mcp-server-git --repository .",
        description="Read and inspect a git repository.",
        setup="point --repository at the repo (default: the current directory)",
    ),
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
        command="bunx @playwright/mcp@latest --isolated",
        description="Drive a real browser: navigate, read the page (snapshot/find), click, fill "
        "forms, screenshot. Handles JavaScript pages; a vision model also sees the screenshots.",
        setup="needs bun (bunx); downloads a browser on first run. Opens a visible window — "
        "add --headless for servers/automation, drop --isolated to keep a login between runs",
    ),
]


OPTIONAL_FIRST_PARTY: list[CuratedMcp] = [
    CuratedMcp(
        name="web",
        command="kodo-mcp-web",
        description="Read a web page in a headless browser and return its main content as Markdown.",
        setup="install with: make install-web  (Playwright + Chromium)",
    ),
]


def uninstalled_optional(advertised: set[str]) -> list[CuratedMcp]:
    """Optional first-party servers not currently installed (i.e. not advertised)."""
    return [s for s in OPTIONAL_FIRST_PARTY if s.name not in advertised]


def optional_hint(server_or_command: str) -> str | None:
    """Install hint for a known optional first-party server (matched by name or command), else None.

    Lets the web health menu / ``kodo mcp add`` explain a server that reports zero tools because
    its extra isn't installed (e.g. ``web`` needs ``make install-web``).
    """
    for server in OPTIONAL_FIRST_PARTY:
        if server_or_command in (server.name, server.command):
            return server.setup
    return None
