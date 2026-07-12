"""A FastMCP server for read-only file access under one configured root, over stdio.

Lets an assistant browse, read, and search a project directory (the root, ``HEIM_FILES_ROOT``,
defaults to the current directory). Every path is contained to the root by ``safe_join`` — no
absolute paths, no ``..`` escapes. Writes are off unless ``HEIM_FILES_WRITABLE`` is set.

Run standalone over stdio: ``heim-mcp-files`` (or ``python -m heim.mcp_servers.files``). Point heim at it
with ``heim chat --mcp files`` or a ``[[mcp]]`` block.
"""

import asyncio
from typing import Any

from fastmcp import FastMCP

from heim.mcp_servers.files.core import FilesSettings, list_dir, read_text, search, write_text

mcp: FastMCP = FastMCP("heim-files")


@mcp.tool
def list_files(subdir: str = "") -> dict[str, Any]:
    """List files and directories directly under `subdir` (default: the root). Paths are relative.

    Skips vendored/build/cache dirs (.git, node_modules, .venv, …). Use to explore the tree, then
    `read_file` a specific path.
    """
    entries = list_dir(FilesSettings(), subdir)
    return {"subdir": subdir or ".", "count": len(entries), "entries": [e.model_dump() for e in entries]}


@mcp.tool
def read_file(path: str) -> dict[str, Any]:
    """Read a text file under the root and return its content. Binary or oversized files are refused."""
    return {"path": path, "content": read_text(FilesSettings(), path)}


@mcp.tool
def search_files(query: str, subdir: str = "") -> dict[str, Any]:
    """Find lines containing `query` (case-insensitive) across text files under `subdir`.

    Returns matches as {path, line, text}. Use to locate where something is defined or used.
    """
    matches = search(FilesSettings(), query, subdir)
    return {"query": query, "count": len(matches), "matches": [m.model_dump() for m in matches]}


@mcp.tool
def write_file(path: str, content: str) -> dict[str, Any]:
    """Write `content` to a file under the root. Disabled unless HEIM_FILES_WRITABLE is set."""
    written = write_text(FilesSettings(), path, content)
    return {"path": path, "written": written}


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn); quiet on Ctrl-C."""
    try:
        mcp.run(show_banner=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
