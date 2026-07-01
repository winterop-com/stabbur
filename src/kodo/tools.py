"""MCP client: connect to an MCP server and expose its tools to a model.

kodo is the MCP *client* — it spawns an MCP server (over stdio), lists its tools
as OpenAI function schemas for the model, and executes ``tool_call``s against it.
"""

import os
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from fastmcp import Client
from fastmcp.client.transports import StdioTransport


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

    Exposes OpenAI schemas for every tool and routes ``call(name, ...)`` to the
    server that owns that tool. On a name collision the first server wins.
    """

    def __init__(self) -> None:
        self.schemas: list[dict[str, Any]] = []
        self._owner: dict[str, Client] = {}

    async def add(self, client: Client) -> None:
        """Register a connected client's tools (skipping name collisions)."""
        for tool in await client.list_tools():
            if tool.name in self._owner:
                continue  # first server wins
            self.schemas.append(_openai_schema(tool))
            self._owner[tool.name] = client

    @property
    def names(self) -> list[str]:
        """Names of the available tools."""
        return [s["function"]["name"] for s in self.schemas]

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool on its owning server and return its result as text."""
        client = self._owner.get(name)
        if client is None:
            return f"error: unknown tool {name!r}"
        return _result_text(await client.call_tool(name, arguments))


@asynccontextmanager
async def connect(commands: list[list[str]]) -> AsyncGenerator[MCPToolset, None]:
    """Spawn one or more MCP servers over stdio and yield a merged toolset."""
    toolset = MCPToolset()
    async with AsyncExitStack() as stack:
        for command in commands:
            # Discard the spawned server's stderr (banners/logs) to keep our output clean.
            transport = StdioTransport(command=command[0], args=command[1:], log_file=Path(os.devnull))
            client = await stack.enter_async_context(Client(transport))
            await toolset.add(client)
        yield toolset
