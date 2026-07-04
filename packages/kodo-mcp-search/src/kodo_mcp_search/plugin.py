"""kodo plugin: advertise the search MCP server (advertise-only, no command).

This keeps ``kodo-mcp-search`` a plain MCP server — it contributes no CLI command and never
imports kodo — while letting kodo discover it (for ``--mcp`` resolution, a ``kodo mcp
list``, and tool pickers) instead of hardcoding it. Matched to the host purely by
pluginkit's project name (``kodo``) via the ``kodo.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("kodo")


class SearchPlugin:
    """Advertises the search server to kodo's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``kodo-mcp-search`` (web search -> titled results)."""
        return [
            {
                "name": "search",
                "command": "kodo-mcp-search",
                "description": "Search the web and return titled results (title, URL, snippet).",
            }
        ]


# The object kodo loads via the ``kodo.plugins`` entry point.
PLUGIN = SearchPlugin()
