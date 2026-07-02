"""Kodo MCP server: date, time, timezone, and calendar tools.

Re-exports the FastMCP app (``mcp``) and stdio entry point (``main``) from ``app`` so
both the ``kodo-mcp-datetime`` console script and ``python -m kodo_mcp_datetime``
resolve against the package root.
"""

from kodo_mcp_datetime.app import main, mcp

__all__ = ["main", "mcp"]
