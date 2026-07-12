"""heim plugin: advertise the git MCP server (advertise-only, no command).

Keeps ``heim-mcp-git`` a plain MCP server — no CLI command, never imports heim — while letting heim
discover it (``--mcp`` resolution, ``heim mcp list``, tool pickers) via the ``heim.plugins`` entry
point. Read-only by default; not seeded by ``heim setup`` — add it deliberately with
``heim mcp add git``.
"""

from pluginkit import Extension

extension = Extension("heim")


class GitPlugin:
    """Advertises the git server to heim's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``heim-mcp-git`` (read-only git inspection sandboxed to one repo)."""
        return [
            {
                "name": "git",
                "command": "heim-mcp-git",
                "description": "Read-only git inspection (status, log, diff, show, blame) sandboxed to one repo.",
            }
        ]


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = GitPlugin()
