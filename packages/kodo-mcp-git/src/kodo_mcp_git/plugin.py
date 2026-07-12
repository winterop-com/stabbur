"""kodo plugin: advertise the git MCP server (advertise-only, no command).

Keeps ``kodo-mcp-git`` a plain MCP server — no CLI command, never imports kodo — while letting kodo
discover it (``--mcp`` resolution, ``kodo mcp list``, tool pickers) via the ``kodo.plugins`` entry
point. Read-only by default; not seeded by ``kodo setup`` — add it deliberately with
``kodo mcp add git``.
"""

from pluginkit import Extension

extension = Extension("kodo")


class GitPlugin:
    """Advertises the git server to kodo's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``kodo-mcp-git`` (read-only git inspection sandboxed to one repo)."""
        return [
            {
                "name": "git",
                "command": "kodo-mcp-git",
                "description": "Read-only git inspection (status, log, diff, show, blame) sandboxed to one repo.",
            }
        ]


# The object kodo loads via the ``kodo.plugins`` entry point.
PLUGIN = GitPlugin()
