"""stabbur plugin: advertise the datetime MCP server (advertise-only, no command).

This keeps ``stabbur-mcp-datetime`` a plain MCP server — it contributes no CLI command and
never imports stabbur — while letting stabbur discover it (for ``--mcp`` resolution, a
``stabbur mcp list``, and tool pickers) instead of hardcoding it. Matched to the host purely
by pluginkit's project name (``stabbur``) via the ``stabbur.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("stabbur")


class DatetimePlugin:
    """Advertises the datetime server to stabbur's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``stabbur-mcp-datetime`` (date/time/timezone/calendar tools)."""
        return [
            {
                "name": "datetime",
                "command": "stabbur-mcp-datetime",
                "description": "Date, time, timezone, and calendar tools.",
            }
        ]


# The object stabbur loads via the ``stabbur.plugins`` entry point.
PLUGIN = DatetimePlugin()
