"""kodo plugin: advertise the memory MCP server (advertise-only, no command).

Keeps ``kodo-mcp-memory`` a plain MCP server — it contributes no CLI command and never
imports kodo — while letting kodo discover it (``--mcp`` resolution, ``kodo mcp list``, tool
pickers) instead of hardcoding it. Matched to the host by pluginkit's project name (``kodo``)
via the ``kodo.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("kodo")


class MemoryPlugin:
    """Advertises the memory server to kodo's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``kodo-mcp-memory`` (persistent notes / key-value memory)."""
        return [
            {
                "name": "memory",
                "command": "kodo-mcp-memory",
                "description": "Persistent notes / key-value memory saved in the library (survives sessions).",
            }
        ]


# The object kodo loads via the ``kodo.plugins`` entry point.
PLUGIN = MemoryPlugin()
