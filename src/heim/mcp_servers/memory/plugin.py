"""heim plugin: advertise the memory MCP server (advertise-only, no command).

Keeps ``heim-mcp-memory`` a plain MCP server — it contributes no CLI command and never
imports heim — while letting heim discover it (``--mcp`` resolution, ``heim mcp list``, tool
pickers) instead of hardcoding it. Matched to the host by pluginkit's project name (``heim``)
via the ``heim.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("heim")


class MemoryPlugin:
    """Advertises the memory server to heim's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``heim-mcp-memory`` (persistent notes / key-value memory)."""
        return [
            {
                "name": "memory",
                "command": "heim-mcp-memory",
                "description": "Persistent notes / key-value memory saved in the library (survives sessions).",
            }
        ]


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = MemoryPlugin()
