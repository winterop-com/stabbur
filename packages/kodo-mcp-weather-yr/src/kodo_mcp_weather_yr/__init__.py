"""Kodo MCP server: weather forecasts from the free met.no (yr.no) APIs.

Re-exports the FastMCP app (``mcp``) and stdio entry point (``main``) from ``app`` so both the
``kodo-mcp-weather-yr`` console script and ``python -m kodo_mcp_weather_yr`` resolve against the
package root.
"""

from kodo_mcp_weather_yr.app import main, mcp

__all__ = ["main", "mcp"]
