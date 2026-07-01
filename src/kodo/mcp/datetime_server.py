"""A tiny FastMCP server exposing date/time tools — for testing kodo's MCP client.

Run standalone over stdio: ``kodo-mcp-datetime`` (or ``python -m
kodo.mcp.datetime_server``). Point kodo at it with ``kodo chat --mcp``.
"""

import asyncio
from datetime import datetime

from fastmcp import FastMCP

mcp: FastMCP = FastMCP("kodo-datetime")


@mcp.tool
def current_datetime() -> str:
    """Return the current local date and time in ISO 8601 (seconds precision)."""
    return datetime.now().isoformat(timespec="seconds")


@mcp.tool
def today() -> str:
    """Return today's date and weekday, e.g. ``2026-07-01 (Wednesday)``."""
    return datetime.now().strftime("%Y-%m-%d (%A)")


@mcp.tool
def day_of_week() -> str:
    """Return the current day of the week, e.g. ``Wednesday``."""
    return datetime.now().strftime("%A")


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn).

    Swallow Ctrl-C / stream-closed shutdown noise so exiting is quiet.
    """
    try:
        mcp.run()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    main()
