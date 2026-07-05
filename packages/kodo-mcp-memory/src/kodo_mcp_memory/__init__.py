"""Kodo MCP server: persistent notes / key-value memory saved in the library.

Re-exports the FastMCP app (``mcp``) and stdio entry point (``main``) from ``app`` so both
the ``kodo-mcp-memory`` console script and ``python -m kodo_mcp_memory`` resolve against the
package root.
"""

from kodo_mcp_memory.app import main, mcp

__all__ = ["main", "mcp"]
