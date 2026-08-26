"""stabbur plugin: advertise the search MCP server (advertise-only, no command).

This keeps ``stabbur-mcp-search`` a plain MCP server — it contributes no CLI command and never
imports stabbur — while letting stabbur discover it (for ``--mcp`` resolution, a ``stabbur mcp
list``, and tool pickers) instead of hardcoding it. Matched to the host purely by
pluginkit's project name (``stabbur``) via the ``stabbur.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("stabbur")


class SearchPlugin:
    """Advertises the search server to stabbur's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, object]]:
        """Advertise ``stabbur-mcp-search`` (web search -> titled results).

        The backend only. The two API keys are deliberately **not** declared: a declared setting is
        read back by ``GET /api/mcp/servers`` and rendered in a panel, which is the wrong home for a
        secret — keys stay in ``mcp.json`` (or the environment), where a user puts them once.
        """
        return [
            {
                "name": "search",
                "command": "stabbur-mcp-search",
                "description": "Search the web and return titled results (title, URL, snippet).",
                "settings": [
                    {
                        "env": "STABBUR_SEARCH_BACKEND",
                        "label": "Backend",
                        "description": "auto, duckduckgo, brave, or exa. auto picks a keyed backend, else duckduckgo.",
                        "type": "text",
                        "default": "auto",
                    }
                ],
            }
        ]


# The object stabbur loads via the ``stabbur.plugins`` entry point.
PLUGIN = SearchPlugin()
