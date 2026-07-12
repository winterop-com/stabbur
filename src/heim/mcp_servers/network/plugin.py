"""heim plugin: advertise the network MCP server (advertise-only, no command).

Keeps ``heim-mcp-network`` a plain MCP server — no CLI command, never imports heim — while letting
heim discover it (``--mcp`` resolution, ``heim mcp list``, tool pickers) via the ``heim.plugins``
entry point.
"""

from pluginkit import Extension

extension = Extension("heim")


class NetworkPlugin:
    """Advertises the network server to heim's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``heim-mcp-network`` (public IP, IP-based location, Tailscale status)."""
        return [
            {
                "name": "network",
                "command": "heim-mcp-network",
                "description": "This machine's network identity: public IP, IP-based location, and Tailscale status.",
            }
        ]


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = NetworkPlugin()
