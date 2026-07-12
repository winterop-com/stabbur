"""heim plugin: advertise the files MCP server (advertise-only, no command).

Keeps ``heim-mcp-files`` a plain MCP server — no CLI command, never imports heim — while letting
heim discover it (``--mcp`` resolution, ``heim mcp list``, tool pickers). Matched to the host by
pluginkit's project name (``heim``) via the ``heim.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("heim")


class FilesPlugin:
    """Advertises the files server to heim's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``heim-mcp-files`` (read-only file browse/read/search under a root)."""
        return [
            {
                "name": "files",
                "command": "heim-mcp-files",
                "description": "Browse, read, and search files under a configured workspace root (read-only).",
            }
        ]


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = FilesPlugin()
