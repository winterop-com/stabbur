"""kodo plugin: advertise the weather MCP server (advertise-only, no command).

Keeps ``kodo-mcp-weather-yr`` a plain MCP server — it contributes no CLI command and never
imports kodo — while letting kodo discover it (``--mcp`` resolution, ``kodo mcp list``, tool
pickers). Matched to the host by pluginkit's project name (``kodo``) via the ``kodo.plugins``
entry point.
"""

from pluginkit import Extension

extension = Extension("kodo")


class WeatherPlugin:
    """Advertises the weather server to kodo's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``kodo-mcp-weather-yr`` (weather via met.no / yr.no)."""
        return [
            {
                "name": "weather-yr",
                "command": "kodo-mcp-weather-yr",
                "description": "Weather forecasts by place or coordinates, via the free met.no (yr.no) API.",
            }
        ]


# The object kodo loads via the ``kodo.plugins`` entry point.
PLUGIN = WeatherPlugin()
