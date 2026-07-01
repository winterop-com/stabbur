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


async def test_toolset_namespaces_and_calls() -> None:
    # Tools are namespaced by server (<prefix>__<tool>); call routes back to the
    # underlying tool name.
    async with Client(mcp) as client:
        toolset = tools.MCPToolset()
        await toolset.add(client, "datetime")

        assert "datetime__today" in toolset.names
        assert "today" not in toolset.names  # bare name is not exposed
        schema = next(s for s in toolset.schemas if s["function"]["name"] == "datetime__today")
        assert schema["type"] == "function"
        assert "parameters" in schema["function"]

        assert (await toolset.call("datetime__day_of_week", {})) in _WEEKDAYS
        assert (await toolset.call("no_such_tool", {})).startswith("error:")


async def test_toolset_dedupes_within_a_prefix() -> None:
    # Same server added twice under the same prefix must not duplicate tools.
    async with Client(mcp) as a, Client(mcp) as b:
        toolset = tools.MCPToolset()
        await toolset.add(a, "datetime")
        await toolset.add(b, "datetime")
        assert toolset.names.count("datetime__today") == 1


def test_default_name_derives_prefix() -> None:
    assert tools._default_name(["kodo-mcp-datetime"]) == "datetime"
    assert tools._default_name(["/usr/bin/dhis2w-mcp-bridge"]) == "dhis2w_mcp_bridge"
    assert tools._default_name([]) == "mcp"


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
