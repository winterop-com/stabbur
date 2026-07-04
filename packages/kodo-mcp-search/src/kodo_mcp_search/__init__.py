"""Kodo MCP server: web search returning titled results (title, url, snippet).

Re-exports the FastMCP app (``mcp``) and stdio entry point (``main``) from ``app`` so
both the ``kodo-mcp-search`` console script and ``python -m kodo_mcp_search`` resolve
against the package root.
"""

from kodo_mcp_search.app import main, mcp

__all__ = ["main", "mcp"]
