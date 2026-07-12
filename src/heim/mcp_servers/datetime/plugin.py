"""heim plugin: advertise the datetime MCP server (advertise-only, no command).

This keeps ``heim-mcp-datetime`` a plain MCP server — it contributes no CLI command and
never imports heim — while letting heim discover it (for ``--mcp`` resolution, a
``heim mcp list``, and tool pickers) instead of hardcoding it. Matched to the host purely
by pluginkit's project name (``heim``) via the ``heim.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("heim")


class DatetimePlugin:
    """Advertises the datetime server to heim's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``heim-mcp-datetime`` (date/time/timezone/calendar tools)."""
        return [
            {
                "name": "datetime",
                "command": "heim-mcp-datetime",
                "description": "Date, time, timezone, and calendar tools.",
            }
        ]


# The object heim loads via the ``heim.plugins`` entry point.
PLUGIN = DatetimePlugin()
