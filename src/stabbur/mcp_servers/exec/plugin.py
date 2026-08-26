"""stabbur plugin: advertise the exec MCP server (advertise-only, no command).

Keeps ``stabbur-mcp-exec`` a plain MCP server — no CLI command, never imports stabbur — while letting
stabbur discover it (``--mcp`` resolution, ``stabbur mcp list``, tool pickers). Matched to the host by
pluginkit's project name (``stabbur``) via the ``stabbur.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("stabbur")


class ExecPlugin:
    """Advertises the exec server to stabbur's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``stabbur-mcp-exec`` (sandboxed Python scratchpad; needs Docker)."""
        return [
            {
                "name": "exec",
                "command": "stabbur-mcp-exec",
                "description": "Run a Python snippet in a locked-down Docker sandbox (calculator / scratchpad).",
            }
        ]


# The object stabbur loads via the ``stabbur.plugins`` entry point.
PLUGIN = ExecPlugin()
