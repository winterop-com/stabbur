"""stabbur plugin: advertise the shell MCP server (advertise-only, no command).

Keeps ``stabbur-mcp-shell`` a plain MCP server — no CLI command, never imports stabbur — while letting
stabbur discover it (``--mcp`` resolution, ``stabbur mcp list``, tool pickers) via the ``stabbur.plugins``
entry point. It is *not* seeded by ``stabbur setup``: add it deliberately with ``stabbur mcp add shell``.
"""

from pluginkit import Extension

extension = Extension("stabbur")


class ShellPlugin:
    """Advertises the shell server to stabbur's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``stabbur-mcp-shell`` (run host commands; read-only by default)."""
        return [
            {
                "name": "shell",
                "command": "stabbur-mcp-shell",
                "description": "Run host shell commands: read-only diagnostics by default; opt-in full mode.",
            }
        ]


# The object stabbur loads via the ``stabbur.plugins`` entry point.
PLUGIN = ShellPlugin()
