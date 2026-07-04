"""MCP client: connect to an MCP server and expose its tools to a model.

kodo is the MCP *client* — it spawns an MCP server (over stdio), lists its tools
as OpenAI function schemas for the model, and executes ``tool_call``s against it.
"""

import os
import re
import shutil
import sys
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

_NAME_STRIP = re.compile(r"^(kodo-mcp-|mcp-server-|mcp-)")


def _bin_dir() -> str:
    """The running interpreter's directory — where kodo's bundled ``kodo-mcp-*`` scripts live.

    Not necessarily on PATH (a ``uv tool install``ed kodo exposes only the ``kodo`` symlink),
    so we add it explicitly when spawning bundled servers.
    """
    return str(Path(sys.executable).parent)


def _mcp_env() -> dict[str, str]:
    """Environment for a spawned MCP server, with kodo's own bin/ prepended to PATH.

    So bundled ``kodo-mcp-*`` scripts (and tools they call) resolve regardless of how kodo ran.
    """
    env = dict(os.environ)
    env["PATH"] = _bin_dir() + os.pathsep + env.get("PATH", "")
    return env


def _resolve_command(cmd: str) -> str:
    """Resolve a bare command to an absolute path, searching kodo's own bin/ too.

    subprocess resolves a bare executable name against the *parent's* PATH, so putting kodo's
    bin/ only in the child env isn't enough — resolve it here. A command already found on PATH
    (or absolute) is returned as-is; an unfound one is passed through unchanged.
    """
    return shutil.which(cmd, path=os.environ.get("PATH", "") + os.pathsep + _bin_dir()) or cmd


def _slug(text: str) -> str:
    """Identifier slug: runs of non-alphanumerics → single underscores, trimmed."""
    return re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")


def _default_name(command: list[str]) -> str:
    """Short server prefix from its launch command (e.g. kodo-mcp-datetime → datetime)."""
    base = Path(command[0]).name if command else "mcp"
    return _slug(_NAME_STRIP.sub("", base)) or "mcp"


def _server_prefix(name: str | None, command: list[str]) -> str:
    """The tool namespace for a server: its manifest ``name`` if set, else derived."""
    if name:
        return _slug(name) or _default_name(command)
    return _default_name(command)


def _openai_schema(tool: Any) -> dict[str, Any]:
    """Convert one MCP tool to an OpenAI ``tools`` entry."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema or {"type": "object", "properties": {}},
        },
    }


def _result_text(result: Any) -> str:
    """Best-effort text from a FastMCP call result (structured data or content)."""
    data = getattr(result, "data", None)
    if data is not None:
        return str(data)
    parts = [c.text for c in getattr(result, "content", []) if getattr(c, "text", None)]
    return "\n".join(parts) or str(result)


class MCPToolset:
    """Aggregated tools across one or more MCP servers.

    Tool names are namespaced by server (``<server>__<tool>``) so tools from
    different servers never collide and stay unambiguous to the model and the UI;
    ``call`` strips the prefix and routes to the owning server.
    """

    def __init__(self) -> None:
        self.schemas: list[dict[str, Any]] = []
        self._owner: dict[str, tuple[Client, str]] = {}  # qualified name → (client, tool name)

    async def add(self, client: Client, prefix: str) -> None:
        """Register a server's tools under ``<prefix>__<tool>`` (skip duplicates)."""
        for tool in await client.list_tools():
            qualified = f"{prefix}__{tool.name}"
            if qualified in self._owner:
                continue
            schema = _openai_schema(tool)
            schema["function"]["name"] = qualified
            self.schemas.append(schema)
            self._owner[qualified] = (client, tool.name)

    @property
    def names(self) -> list[str]:
        """Names of the available (namespaced) tools."""
        return [s["function"]["name"] for s in self.schemas]

    def subset(self, names: set[str]) -> "MCPToolset":
        """A view exposing only ``names`` (call-routing shared with this toolset)."""
        view = MCPToolset()
        view._owner = self._owner
        view.schemas = [s for s in self.schemas if s["function"]["name"] in names]
        return view

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a namespaced tool on its owning server and return its result as text."""
        entry = self._owner.get(name)
        if entry is None:
            return f"error: unknown tool {name!r}"
        client, tool_name = entry
        return _result_text(await client.call_tool(tool_name, arguments))


@asynccontextmanager
async def connect(servers: list[tuple[str | None, list[str]]]) -> AsyncGenerator[MCPToolset, None]:
    """Spawn one or more MCP servers over stdio and yield a merged, namespaced toolset.

    Each server is ``(name, command)``: the tools are namespaced under the manifest
    ``name`` when given (``kodo.toml`` ``[[mcp]].name``), else a prefix derived from
    the executable. ``name`` may be ``None`` for a bare command (e.g. CLI ``--mcp``).
    """
    toolset = MCPToolset()
    used: dict[str, int] = {}  # disambiguate servers that derive the same prefix
    env = _mcp_env()  # kodo's bin/ on PATH so bundled kodo-mcp-* servers resolve
    async with AsyncExitStack() as stack:
        for name, command in servers:
            # Discard the spawned server's stderr (banners/logs) to keep our output clean.
            transport = StdioTransport(
                command=_resolve_command(command[0]), args=command[1:], env=env, log_file=Path(os.devnull)
            )
            client = await stack.enter_async_context(Client(transport))
            prefix = _server_prefix(name, command)
            n = used.get(prefix, 0)
            used[prefix] = n + 1
            await toolset.add(client, prefix if n == 0 else f"{prefix}{n + 1}")
        yield toolset
