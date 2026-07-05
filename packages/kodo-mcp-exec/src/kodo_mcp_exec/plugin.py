"""kodo plugin: advertise the exec MCP server (advertise-only, no command).

Keeps ``kodo-mcp-exec`` a plain MCP server — no CLI command, never imports kodo — while letting
kodo discover it (``--mcp`` resolution, ``kodo mcp list``, tool pickers). Matched to the host by
pluginkit's project name (``kodo``) via the ``kodo.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("kodo")


class ExecPlugin:
    """Advertises the exec server to kodo's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``kodo-mcp-exec`` (sandboxed Python scratchpad; needs Docker)."""
        return [
            {
                "name": "exec",
                "command": "kodo-mcp-exec",
                "description": "Run a Python snippet in a locked-down Docker sandbox (calculator / scratchpad).",
            }
        ]


# The object kodo loads via the ``kodo.plugins`` entry point.
PLUGIN = ExecPlugin()
