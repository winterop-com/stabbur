"""kodo plugin: advertise the datetime MCP server (advertise-only, no command).

This keeps ``kodo-mcp-datetime`` a plain MCP server — it contributes no CLI command and
never imports kodo — while letting kodo discover it (for ``--mcp`` resolution, a
``kodo mcp list``, and tool pickers) instead of hardcoding it. Matched to the host purely
by pluginkit's project name (``kodo``) via the ``kodo.plugins`` entry point.
"""

from pluginkit import Extension

extension = Extension("kodo")


class DatetimePlugin:
    """Advertises the datetime server to kodo's plugin manager."""

    @extension
    def mcp_servers(self) -> list[dict[str, str]]:
        """Advertise ``kodo-mcp-datetime`` (date/time/timezone/calendar tools)."""
        return [
            {
                "name": "datetime",
                "command": "kodo-mcp-datetime",
                "description": "Date, time, timezone, and calendar tools.",
            }
        ]


# The object kodo loads via the ``kodo.plugins`` entry point.
PLUGIN = DatetimePlugin()
