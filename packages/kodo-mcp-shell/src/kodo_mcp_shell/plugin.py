"""kodo plugin: advertise the shell MCP server (advertise-only, no command).

Keeps ``kodo-mcp-shell`` a plain MCP server — no CLI command, never imports kodo — while letting
kodo discover it (``--mcp`` resolution, ``kodo mcp list``, tool pickers) via the ``kodo.plugins``
entry point. It is *not* seeded by ``kodo setup``: add it deliberately with ``kodo mcp add shell``.
"""

from pluginkit import Extension

extension = Extension("kodo")


class ShellPlugin:
    """Advertises the shell server to kodo's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``kodo-mcp-shell`` (run host commands; read-only by default)."""
        return [
            {
                "name": "shell",
                "command": "kodo-mcp-shell",
                "description": "Run host shell commands: read-only diagnostics by default; opt-in full mode.",
            }
        ]


# The object kodo loads via the ``kodo.plugins`` entry point.
PLUGIN = ShellPlugin()
