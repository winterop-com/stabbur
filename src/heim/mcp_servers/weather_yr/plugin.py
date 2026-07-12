"""heim plugin: advertise the weather MCP server (advertise-only, no command).

Keeps ``heim-mcp-weather-yr`` a plain MCP server — it contributes no CLI command and never
imports heim — while letting heim discover it (``--mcp`` resolution, ``heim mcp list``, tool
pickers). Matched to the host by pluginkit's project name (``heim``) via the ``heim.plugins``
entry point.
"""

from pluginkit import Extension

extension = Extension("heim")


class WeatherPlugin:
    """Advertises the weather server to heim's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``heim-mcp-weather-yr`` (weather via met.no / yr.no)."""
        return [
            {
                "name": "weather-yr",
                "command": "heim-mcp-weather-yr",
                "description": "Weather forecasts by place or coordinates, via the free met.no (yr.no) API.",
            }
        ]


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = WeatherPlugin()
