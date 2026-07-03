"""kodo plugin: advertise the utils MCP server (advertise-only, no command).

Keeps ``kodo-mcp-utils`` a plain MCP server — no CLI command, never imports kodo — while
letting kodo discover it (``kodo mcp list``, ``--mcp utils``, tool pickers). Matched to
the host purely by pluginkit's project name (``kodo``) via the ``kodo.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("kodo")


class UtilsPlugin:
    """Advertises the utils server to kodo's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``kodo-mcp-utils`` (text, encoding, hashing, JSON, math tools)."""
        return [
            {
                "name": "utils",
                "command": "kodo-mcp-utils",
                "description": "Text, encoding, hashing, JSON, and math utilities.",
            }
        ]


# The object kodo loads via the ``kodo.plugins`` entry point.
PLUGIN = UtilsPlugin()
