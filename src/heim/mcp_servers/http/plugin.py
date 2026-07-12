"""heim plugin: advertise the http MCP server (advertise-only, no command).

Keeps ``heim-mcp-http`` a plain MCP server — no CLI command, never imports heim — while letting
heim discover it (``--mcp`` resolution, ``heim mcp list``, tool pickers) via the ``heim.plugins``
entry point. Only imports pluginkit, never the runtime deps (httpx/fastmcp), so discovery is cheap.
"""

from pluginkit import Extension

extension = Extension("heim")


class HttpPlugin:
    """Advertises the http fetch server to heim's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``heim-mcp-http`` (SSRF-guarded, allowlisted HTTP GET/HEAD)."""
        return [
            {
                "name": "http",
                "command": "heim-mcp-http",
                "description": "SSRF-guarded, allowlisted HTTP GET/HEAD of a URL (empty allowlist denies by default).",
            }
        ]


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = HttpPlugin()
