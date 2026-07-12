"""A FastMCP server exposing persistent notes / key-value memory over stdio.

Gives an assistant durable scratch memory across sessions: remember facts, preferences, or
running state under short keys and recall them later. The store is a single JSON file that
lives in the library by default (``<HEIM_LIBRARY_ROOT>/.heim/memory/notes.json``), so it
travels with the drive — see :mod:`heim.mcp_servers.memory.core` for the location rules.

Run standalone over stdio: ``heim-mcp-memory`` (or ``python -m heim.mcp_servers.memory``). Point heim
at it with ``heim chat --mcp memory`` or a ``[[mcp]]`` block.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastmcp import FastMCP

from heim.mcp_servers.memory.core import MemorySettings, MemoryStore

mcp: FastMCP = FastMCP("heim-memory")


def _store() -> MemoryStore:
    """Build the store from the current environment (read per call so HEIM_* is respected)."""
    return MemoryStore(MemorySettings().notes_path())


def _now() -> str:
    """Current UTC time as an ISO 8601 string (second precision), for a note's `updated`."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@mcp.tool
def memory_set(key: str, value: str) -> dict[str, Any]:
    """Remember something under a short key (creates it, or overwrites the existing note).

    Use for durable facts across sessions — a preference, a decision, running state. Keep keys
    short and stable (e.g. "user_name", "project_goal") so you can recall them later. Returns
    the stored key and its `updated` timestamp.
    """
    now = _now()
    _store().set(key, value, now=now)
    return {"ok": True, "key": key, "updated": now}


@mcp.tool
def memory_get(key: str) -> dict[str, Any]:
    """Recall the note stored under `key`. `found` is false (and `value` null) if there is none."""
    note = _store().get(key)
    return {"key": key, "found": note is not None, "value": note.value if note else None}


@mcp.tool
def memory_list() -> dict[str, Any]:
    """List every remembered note (key, value, and when it was last updated). Use to review memory."""
    notes = _store().notes()
    return {"count": len(notes), "notes": [n.model_dump() for n in notes]}


@mcp.tool
def memory_search(query: str) -> dict[str, Any]:
    """Find notes whose key or value contains `query` (case-insensitive)."""
    matches = _store().search(query)
    return {"count": len(matches), "matches": [n.model_dump() for n in matches]}


@mcp.tool
def memory_delete(key: str) -> dict[str, Any]:
    """Forget the note under `key`. `deleted` is false if there was nothing stored there."""
    return {"key": key, "deleted": _store().delete(key)}


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn); quiet on Ctrl-C."""
    try:
        mcp.run(show_banner=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
