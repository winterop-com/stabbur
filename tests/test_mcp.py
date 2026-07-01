"""Tests for the date/time MCP server and kodo's MCP client wrapper (in-memory)."""

from fastmcp import Client

from kodo import tools
from kodo.mcp.datetime_server import mcp

_WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}


async def test_datetime_server_exposes_tools() -> None:
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
        assert {"current_datetime", "today", "day_of_week"} <= names


async def test_toolset_schemas_and_call() -> None:
    async with Client(mcp) as client:
        toolset = tools.MCPToolset(client)
        await toolset._load()

        assert "today" in toolset.names
        schema = next(s for s in toolset.schemas if s["function"]["name"] == "today")
        assert schema["type"] == "function"
        assert "parameters" in schema["function"]

        assert (await toolset.call("day_of_week", {})) in _WEEKDAYS
