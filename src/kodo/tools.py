"""MCP client: connect to an MCP server and expose its tools to a model.

kodo is the MCP *client* — it spawns an MCP server (over stdio), lists its tools
as OpenAI function schemas for the model, and executes ``tool_call``s against it.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
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
    """A connected MCP server's tools: OpenAI schemas + a call() dispatcher."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self.schemas: list[dict[str, Any]] = []

    async def _load(self) -> None:
        self.schemas = [_openai_schema(t) for t in await self._client.list_tools()]

    @property
    def names(self) -> list[str]:
        """Names of the available tools."""
        return [s["function"]["name"] for s in self.schemas]

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool and return its result as text."""
        return _result_text(await self._client.call_tool(name, arguments))


@asynccontextmanager
async def connect(command: list[str]) -> AsyncGenerator[MCPToolset, None]:
    """Spawn an MCP server (``command``) over stdio and yield its toolset."""
    transport = StdioTransport(command=command[0], args=command[1:])
    async with Client(transport) as client:
        toolset = MCPToolset(client)
        await toolset._load()
        yield toolset
