"""Tests for the date/time MCP server and kodo's MCP client wrapper (in-memory)."""

from typing import Any

import pytest
from fastmcp import Client

from kodo import agent, tools
from kodo.mcp.datetime_server import mcp

_WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}


async def test_datetime_server_exposes_tools() -> None:
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
        assert {"current_datetime", "today", "day_of_week"} <= names


async def test_toolset_schemas_and_call() -> None:
    async with Client(mcp) as client:
        toolset = tools.MCPToolset()
        await toolset.add(client)

        assert "today" in toolset.names
        schema = next(s for s in toolset.schemas if s["function"]["name"] == "today")
        assert schema["type"] == "function"
        assert "parameters" in schema["function"]

        assert (await toolset.call("day_of_week", {})) in _WEEKDAYS
        assert (await toolset.call("no_such_tool", {})).startswith("error:")


async def test_toolset_merges_servers_and_dedupes() -> None:
    # Adding the same server twice must not duplicate tools (first server wins).
    async with Client(mcp) as a, Client(mcp) as b:
        toolset = tools.MCPToolset()
        await toolset.add(a)
        await toolset.add(b)
        assert toolset.names.count("today") == 1


async def test_agent_appends_final_answer_to_history(monkeypatch: pytest.MonkeyPatch) -> None:
    # A no-tool-call turn must record the assistant reply in ``messages`` so a
    # REPL keeps prior answers in context on the next turn.
    async def fake_stream(
        http: Any, base_url: str, body: Any, on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[Any]]:
        return "final answer", []

    monkeypatch.setattr(agent, "_stream_turn", fake_stream)
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    out = await agent.run("http://runtime", messages, tools.MCPToolset())

    assert out == "final answer"
    assert messages[-1] == {"role": "assistant", "content": "final answer"}
