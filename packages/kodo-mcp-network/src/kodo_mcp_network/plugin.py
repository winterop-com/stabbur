"""kodo plugin: advertise the network MCP server (advertise-only, no command).

Keeps ``kodo-mcp-network`` a plain MCP server — no CLI command, never imports kodo — while letting
kodo discover it (``--mcp`` resolution, ``kodo mcp list``, tool pickers) via the ``kodo.plugins``
entry point.
"""

from pluginkit import Extension

extension = Extension("kodo")


class NetworkPlugin:
    """Advertises the network server to kodo's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``kodo-mcp-network`` (public IP, IP-based location, Tailscale status)."""
        return [
            {
                "name": "network",
                "command": "kodo-mcp-network",
                "description": "This machine's network identity: public IP, IP-based location, and Tailscale status.",
            }
        ]


# The object kodo loads via the ``kodo.plugins`` entry point.
PLUGIN = NetworkPlugin()
