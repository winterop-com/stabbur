"""stabbur plugin: advertise the weather MCP server (advertise-only, no command).

Keeps ``stabbur-mcp-weather-yr`` a plain MCP server — it contributes no CLI command and never
imports stabbur — while letting stabbur discover it (``--mcp`` resolution, ``stabbur mcp list``, tool
pickers). Matched to the host by pluginkit's project name (``stabbur``) via the ``stabbur.plugins``
entry point.
"""

from pluginkit import Extension

extension = Extension("stabbur")


class WeatherPlugin:
    """Advertises the weather server to stabbur's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``stabbur-mcp-weather-yr`` (weather via met.no / yr.no)."""
        return [
            {
                "name": "weather-yr",
                "command": "stabbur-mcp-weather-yr",
                "description": "Weather forecasts by place or coordinates, via the free met.no (yr.no) API.",
            }
        ]


# The object stabbur loads via the ``stabbur.plugins`` entry point.
PLUGIN = WeatherPlugin()
