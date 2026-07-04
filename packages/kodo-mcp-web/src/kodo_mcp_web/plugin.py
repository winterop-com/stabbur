"""kodo plugin: advertise the web MCP server (advertise-only, no command).

This keeps ``kodo-mcp-web`` a plain MCP server — it contributes no CLI command and never
imports kodo — while letting kodo discover it (for ``--mcp`` resolution, a ``kodo mcp
list``, and tool pickers) instead of hardcoding it. Matched to the host purely by
pluginkit's project name (``kodo``) via the ``kodo.plugins`` entry point.

Note: only imports pluginkit — never the heavy runtime deps (playwright/trafilatura) — so
kodo can advertise this server even before the browser/deps are set up (``kodo mcp list``
works; actually spawning ``kodo-mcp-web`` is what needs them).
"""

from pluginkit import Extension

extension = Extension("kodo")


class WebPlugin:
    """Advertises the web reader server to kodo's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``kodo-mcp-web`` (read a web page -> readable Markdown)."""
        return [
            {
                "name": "web",
                "command": "kodo-mcp-web",
                "description": "Read a web page in a headless browser and return its main content as Markdown.",
            }
        ]


# The object kodo loads via the ``kodo.plugins`` entry point.
PLUGIN = WebPlugin()
