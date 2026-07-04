"""Kodo MCP server: read a web page in a headless browser and return readable Markdown.

Re-exports the FastMCP app (``mcp``) and stdio entry point (``main``) from ``app`` so
both the ``kodo-mcp-web`` console script and ``python -m kodo_mcp_web`` resolve against
the package root.
"""

from kodo_mcp_web.app import main, mcp

__all__ = ["main", "mcp"]
