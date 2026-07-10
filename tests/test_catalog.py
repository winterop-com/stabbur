"""The MCP catalog and project templates stay well-formed and invocable.

`kodo mcp add <name>` resolves against the curated catalog, and scaffolding writes each
template's mcp command into a project's `.mcp.json`; both need commands that parse.
"""

from __future__ import annotations

import shlex

from kodo.mcp_catalog import CURATED
from kodo.project.templates import TEMPLATES


def test_playwright_is_addable_via_mcp_add() -> None:
    # kodo mcp add resolves names against CURATED; playwright must be there with a runnable command.
    entry = next((c for c in CURATED if c.name == "playwright"), None)
    assert entry is not None
    argv = shlex.split(entry.command)
    assert argv[0] == "bunx" and "@playwright/mcp@latest" in argv


def test_browse_template_wires_playwright() -> None:
    template = TEMPLATES["browse"]
    assert template.model  # binds a model
    assert "playwright" in [name for name, _cmd in template.mcp]
    # The prompt should steer tool choice (snapshot to read, screenshot for visual).
    assert "browser_snapshot" in template.system_prompt


def test_all_curated_and_template_commands_parse() -> None:
    # Invariant over every entry (not just the new ones): commands shlex-split to a non-empty argv,
    # so `kodo mcp add` and scaffolding never write a broken .mcp.json.
    for curated in CURATED:
        assert shlex.split(curated.command), curated.name
    for name, template in TEMPLATES.items():
        for _server, command in template.mcp:
            assert shlex.split(command), name
