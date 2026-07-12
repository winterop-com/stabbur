"""kodo plugin: advertise the http MCP server (advertise-only, no command).

Keeps ``kodo-mcp-http`` a plain MCP server — no CLI command, never imports kodo — while letting
kodo discover it (``--mcp`` resolution, ``kodo mcp list``, tool pickers) via the ``kodo.plugins``
entry point. Only imports pluginkit, never the runtime deps (httpx/fastmcp), so discovery is cheap.
"""

from pluginkit import Extension

extension = Extension("kodo")


class HttpPlugin:
    """Advertises the http fetch server to kodo's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``kodo-mcp-http`` (SSRF-guarded, allowlisted HTTP GET/HEAD)."""
        return [
            {
                "name": "http",
                "command": "kodo-mcp-http",
                "description": "SSRF-guarded, allowlisted HTTP GET/HEAD of a URL (empty allowlist denies by default).",
            }
        ]


# The object kodo loads via the ``kodo.plugins`` entry point.
PLUGIN = HttpPlugin()
