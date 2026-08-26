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
    def mcp_servers(self) -> list[dict[str, object]]:
        """Advertise ``heim-mcp-http`` (SSRF-guarded, allowlisted HTTP GET/HEAD).

        Both declared knobs are the ones that make the server usable at all: fail-closed means an
        unconfigured allowlist refuses every request, so "why does this tool always say no" is
        answered by a field the user can see and fill in. The caps and timeout stay undeclared.
        """
        return [
            {
                "name": "http",
                "command": "heim-mcp-http",
                "description": "SSRF-guarded, allowlisted HTTP GET/HEAD of a URL (empty allowlist denies by default).",
                "settings": [
                    {
                        "env": "HEIM_MCP_HTTP_ALLOWLIST",
                        "label": "Allowed hosts",
                        "description": "Comma-separated hosts (subdomains match). Empty refuses every request.",
                        "type": "text",
                        "default": "",
                    },
                    {
                        "env": "HEIM_MCP_HTTP_ALLOW_PRIVATE",
                        "label": "Allow private hosts",
                        "description": "Reach localhost / LAN addresses too (still allowlist-gated).",
                        "type": "boolean",
                        "default": "false",
                    },
                ],
            }
        ]


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = HttpPlugin()
