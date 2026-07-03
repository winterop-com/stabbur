"""Kodo MCP server: core utility tools (text, encoding, hashing, JSON, math, stats).

Re-exports the FastMCP app (``mcp``) and stdio entry point (``main``) so both the
``kodo-mcp-utils`` console script and ``python -m kodo_mcp_utils`` resolve here.
"""

from kodo_mcp_utils.app import main, mcp

__all__ = ["main", "mcp"]
