"""stabbur plugin: advertise the http MCP server (advertise-only, no command).

Keeps ``stabbur-mcp-http`` a plain MCP server — no CLI command, never imports stabbur — while letting
stabbur discover it (``--mcp`` resolution, ``stabbur mcp list``, tool pickers) via the ``stabbur.plugins``
entry point. Only imports pluginkit, never the runtime deps (httpx/fastmcp), so discovery is cheap.
"""

from pluginkit import Extension

extension = Extension("stabbur")


class HttpPlugin:
    """Advertises the http fetch server to stabbur's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, object]]:
        """Advertise ``stabbur-mcp-http`` (SSRF-guarded, allowlisted HTTP GET/HEAD).

        Both declared knobs are the ones that make the server usable at all: fail-closed means an
        unconfigured allowlist refuses every request, so "why does this tool always say no" is
        answered by a field the user can see and fill in. The caps and timeout stay undeclared.
        """
        return [
            {
                "name": "http",
                "command": "stabbur-mcp-http",
                "description": "SSRF-guarded, allowlisted HTTP GET/HEAD of a URL (empty allowlist denies by default).",
                "settings": [
                    {
                        "env": "STABBUR_MCP_HTTP_ALLOWLIST",
                        "label": "Allowed hosts",
                        "description": "Comma-separated hosts (subdomains match). Empty refuses every request.",
                        "type": "text",
                        "default": "",
                    },
                    {
                        "env": "STABBUR_MCP_HTTP_ALLOW_PRIVATE",
                        "label": "Allow private hosts",
                        "description": "Reach localhost / LAN addresses too (still allowlist-gated).",
                        "type": "boolean",
                        "default": "false",
                    },
                ],
            }
        ]


# The object stabbur loads via the ``stabbur.plugins`` entry point.
PLUGIN = HttpPlugin()
