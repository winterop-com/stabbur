"""heim plugin: advertise the shell MCP server (advertise-only, no command).

Keeps ``heim-mcp-shell`` a plain MCP server — no CLI command, never imports heim — while letting
heim discover it (``--mcp`` resolution, ``heim mcp list``, tool pickers) via the ``heim.plugins``
entry point. It is *not* seeded by ``heim setup``: add it deliberately with ``heim mcp add shell``.
"""

from pluginkit import Extension

extension = Extension("heim")


class ShellPlugin:
    """Advertises the shell server to heim's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``heim-mcp-shell`` (run host commands; read-only by default)."""
        return [
            {
                "name": "shell",
                "command": "heim-mcp-shell",
                "description": "Run host shell commands: read-only diagnostics by default; opt-in full mode.",
            }
        ]


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = ShellPlugin()
