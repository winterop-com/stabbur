"""stabbur plugin: advertise the network MCP server (advertise-only, no command).

Keeps ``stabbur-mcp-network`` a plain MCP server — no CLI command, never imports stabbur — while letting
stabbur discover it (``--mcp`` resolution, ``stabbur mcp list``, tool pickers) via the ``stabbur.plugins``
entry point.
"""

from pluginkit import Extension

extension = Extension("stabbur")


class NetworkPlugin:
    """Advertises the network server to stabbur's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``stabbur-mcp-network`` (public IP, IP-based location, Tailscale status)."""
        return [
            {
                "name": "network",
                "command": "stabbur-mcp-network",
                "description": "This machine's network identity: public IP, IP-based location, and Tailscale status.",
            }
        ]


# The object stabbur loads via the ``stabbur.plugins`` entry point.
PLUGIN = NetworkPlugin()
