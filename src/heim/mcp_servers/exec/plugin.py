"""heim plugin: advertise the exec MCP server (advertise-only, no command).

Keeps ``heim-mcp-exec`` a plain MCP server — no CLI command, never imports heim — while letting
heim discover it (``--mcp`` resolution, ``heim mcp list``, tool pickers). Matched to the host by
pluginkit's project name (``heim``) via the ``heim.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("heim")


class ExecPlugin:
    """Advertises the exec server to heim's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``heim-mcp-exec`` (sandboxed Python scratchpad; needs Docker)."""
        return [
            {
                "name": "exec",
                "command": "heim-mcp-exec",
                "description": "Run a Python snippet in a locked-down Docker sandbox (calculator / scratchpad).",
            }
        ]


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = ExecPlugin()
