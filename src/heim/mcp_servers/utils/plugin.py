"""heim plugin: advertise the utils MCP server (advertise-only, no command).

Keeps ``heim-mcp-utils`` a plain MCP server — no CLI command, never imports heim — while
letting heim discover it (``heim mcp list``, ``--mcp utils``, tool pickers). Matched to
the host purely by pluginkit's project name (``heim``) via the ``heim.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("heim")


class UtilsPlugin:
    """Advertises the utils server to heim's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``heim-mcp-utils`` (text, encoding, hashing, JSON, math tools)."""
        return [
            {
                "name": "utils",
                "command": "heim-mcp-utils",
                "description": "Text, encoding, hashing, JSON, and math utilities.",
            }
        ]


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = UtilsPlugin()
