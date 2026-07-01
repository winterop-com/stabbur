"""MCP client: connect to an MCP server and expose its tools to a model.

kodo is the MCP *client* — it spawns an MCP server (over stdio), lists its tools
as OpenAI function schemas for the model, and executes ``tool_call``s against it.
"""

import os
import re
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

_NAME_STRIP = re.compile(r"^(kodo-mcp-|mcp-server-|mcp-)")


def _default_name(command: list[str]) -> str:
    """Short server prefix from its launch command (e.g. kodo-mcp-datetime → datetime)."""
    base = Path(command[0]).name if command else "mcp"
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", _NAME_STRIP.sub("", base)).strip("_")
    return slug or "mcp"


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

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a namespaced tool on its owning server and return its result as text."""
        entry = self._owner.get(name)
        if entry is None:
            return f"error: unknown tool {name!r}"
        client, tool_name = entry
        return _result_text(await client.call_tool(tool_name, arguments))


@asynccontextmanager
async def connect(commands: list[list[str]]) -> AsyncGenerator[MCPToolset, None]:
    """Spawn one or more MCP servers over stdio and yield a merged, namespaced toolset."""
    toolset = MCPToolset()
    used: dict[str, int] = {}  # disambiguate servers that derive the same prefix
    async with AsyncExitStack() as stack:
        for command in commands:
            # Discard the spawned server's stderr (banners/logs) to keep our output clean.
            transport = StdioTransport(command=command[0], args=command[1:], log_file=Path(os.devnull))
            client = await stack.enter_async_context(Client(transport))
            prefix = _default_name(command)
            n = used.get(prefix, 0)
            used[prefix] = n + 1
            await toolset.add(client, prefix if n == 0 else f"{prefix}{n + 1}")
        yield toolset
