"""stabbur plugin: advertise the utils MCP server (advertise-only, no command).

Keeps ``stabbur-mcp-utils`` a plain MCP server — no CLI command, never imports stabbur — while
letting stabbur discover it (``stabbur mcp list``, ``--mcp utils``, tool pickers). Matched to
the host purely by pluginkit's project name (``stabbur``) via the ``stabbur.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("stabbur")


class UtilsPlugin:
    """Advertises the utils server to stabbur's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``stabbur-mcp-utils`` (text, encoding, hashing, JSON, math tools)."""
        return [
            {
                "name": "utils",
                "command": "stabbur-mcp-utils",
                "description": "Text, encoding, hashing, JSON, and math utilities.",
            }
        ]


# The object stabbur loads via the ``stabbur.plugins`` entry point.
PLUGIN = UtilsPlugin()
