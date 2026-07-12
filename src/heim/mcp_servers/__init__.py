"""Vendored first-party MCP tool servers.

Each subpackage (``datetime``, ``exec``, ``files``, …) is a standalone FastMCP stdio
server that heim spawns as an external process via its ``heim-mcp-<name>`` console script
(registered on the ``heim`` distribution). heim never imports these at runtime.
"""
