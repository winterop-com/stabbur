"""Kodo MCP server: a locked-down Python code sandbox (calculator / scratchpad).

Re-exports the FastMCP app (``mcp``) and stdio entry point (``main``) so both the
``kodo-mcp-exec`` console script and ``python -m kodo_mcp_exec`` resolve against the package root.
"""

from kodo_mcp_exec.app import main, mcp

__all__ = ["main", "mcp"]
