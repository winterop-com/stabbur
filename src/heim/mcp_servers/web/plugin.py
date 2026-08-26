"""heim plugin: advertise the web MCP server (advertise-only, no command).

This keeps ``heim-mcp-web`` a plain MCP server — it contributes no CLI command and never
imports heim — while letting heim discover it (for ``--mcp`` resolution, a ``heim mcp
list``, and tool pickers) instead of hardcoding it. Matched to the host purely by
pluginkit's project name (``heim``) via the ``heim.plugins`` entry point.

Note: only imports pluginkit — never the heavy runtime deps (playwright/trafilatura) — so
heim can advertise this server even before the browser/deps are set up (``heim mcp list``
works; actually spawning ``heim-mcp-web`` is what needs them).
"""

from pluginkit import Extension

extension = Extension("heim")


class WebPlugin:
    """Advertises the web reader server to heim's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, object]]:
        """Advertise ``heim-mcp-web`` (read a web page -> readable Markdown).

        Only the private-host gate is declared: it is the one setting that changes *what the server
        may reach*, and the one a user hits deliberately (reading an internal wiki). The rendering
        knobs (timeouts, thresholds, char cap) tune one fetch and are noise in a settings panel.
        """
        return [
            {
                "name": "web",
                "command": "heim-mcp-web",
                "description": "Read a web page in a headless browser and return its main content as Markdown.",
                "settings": [
                    {
                        "env": "HEIM_WEB_ALLOW_PRIVATE",
                        "label": "Allow private hosts",
                        "description": "Read internal / localhost pages, which the SSRF guard otherwise refuses.",
                        "type": "boolean",
                        "default": "false",
                    }
                ],
            }
        ]


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = WebPlugin()
