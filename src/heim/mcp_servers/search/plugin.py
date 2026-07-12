"""heim plugin: advertise the search MCP server (advertise-only, no command).

This keeps ``heim-mcp-search`` a plain MCP server — it contributes no CLI command and never
imports heim — while letting heim discover it (for ``--mcp`` resolution, a ``heim mcp
list``, and tool pickers) instead of hardcoding it. Matched to the host purely by
pluginkit's project name (``heim``) via the ``heim.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("heim")


class SearchPlugin:
    """Advertises the search server to heim's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``heim-mcp-search`` (web search -> titled results)."""
        return [
            {
                "name": "search",
                "command": "heim-mcp-search",
                "description": "Search the web and return titled results (title, URL, snippet).",
            }
        ]


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = SearchPlugin()
