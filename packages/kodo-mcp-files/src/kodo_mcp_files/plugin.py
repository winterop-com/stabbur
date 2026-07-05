"""kodo plugin: advertise the files MCP server (advertise-only, no command).

Keeps ``kodo-mcp-files`` a plain MCP server — no CLI command, never imports kodo — while letting
kodo discover it (``--mcp`` resolution, ``kodo mcp list``, tool pickers). Matched to the host by
pluginkit's project name (``kodo``) via the ``kodo.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("kodo")


class FilesPlugin:
    """Advertises the files server to kodo's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``kodo-mcp-files`` (read-only file browse/read/search under a root)."""
        return [
            {
                "name": "files",
                "command": "kodo-mcp-files",
                "description": "Browse, read, and search files under a configured workspace root (read-only).",
            }
        ]


# The object kodo loads via the ``kodo.plugins`` entry point.
PLUGIN = FilesPlugin()
