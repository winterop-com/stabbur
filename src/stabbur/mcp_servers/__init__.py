"""Vendored first-party MCP tool servers.

Each subpackage (``datetime``, ``exec``, ``files``, …) is a standalone FastMCP stdio
server that stabbur spawns as an external process via its ``stabbur-mcp-<name>`` console script
(registered on the ``stabbur`` distribution). stabbur never imports these at runtime.
"""
