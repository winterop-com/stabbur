"""Kodo MCP server: read-only file access (list / read / search) under one configured root.

Re-exports the FastMCP app (``mcp``) and stdio entry point (``main``) so both the
``kodo-mcp-files`` console script and ``python -m kodo_mcp_files`` resolve against the package root.
"""

from kodo_mcp_files.app import main, mcp

__all__ = ["main", "mcp"]
