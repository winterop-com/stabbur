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


def test_user_content_builds_multimodal_parts() -> None:
    # No images → plain string (backward compatible).
    assert agent.user_content("hi") == "hi"
    assert agent.user_content("hi", []) == "hi"
    # With images → OpenAI content parts: text first, then image_url parts.
    parts = agent.user_content("describe", ["data:image/png;base64,AAAA"])
    assert parts == [
        {"type": "text", "text": "describe"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    # No text → images only (no empty text part).
    only = agent.user_content("", ["data:image/jpeg;base64,BBBB"])
    assert only == [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBBB"}}]


def test_user_content_builds_audio_parts() -> None:
    # Audio data URLs become OpenAI input_audio parts: raw base64 (no data: prefix)
    # + a format derived from the mime type.
    parts = agent.user_content("transcribe", audios=["data:audio/wav;base64,QUJD"])
    assert parts == [
        {"type": "text", "text": "transcribe"},
        {"type": "input_audio", "input_audio": {"data": "QUJD", "format": "wav"}},
    ]
    # mime → format mapping (audio/mpeg → mp3).
    mp3 = agent.user_content("", audios=["data:audio/mpeg;base64,ZZZ"])
    assert mp3 == [{"type": "input_audio", "input_audio": {"data": "ZZZ", "format": "mp3"}}]


def test_default_name_derives_prefix() -> None:
    assert tools._default_name(["kodo-mcp-datetime"]) == "datetime"
    assert tools._default_name(["/usr/bin/dhis2w-mcp-bridge"]) == "dhis2w_mcp_bridge"
    assert tools._default_name([]) == "mcp"


def test_server_prefix_prefers_manifest_name() -> None:
    # A manifest name (kodo.toml [[mcp]].name) wins over the derived prefix, slugified.
    assert tools._server_prefix("dhis2", ["dhis2w-mcp-bridge"]) == "dhis2"
    assert tools._server_prefix("My Server", ["whatever"]) == "My_Server"
    # No name → fall back to the command-derived prefix.
    assert tools._server_prefix(None, ["kodo-mcp-datetime"]) == "datetime"
    # Empty / all-punctuation name → fall back rather than yield an empty prefix.
    assert tools._server_prefix("  ", ["kodo-mcp-datetime"]) == "datetime"


async def test_agent_appends_final_answer_to_history(monkeypatch: pytest.MonkeyPatch) -> None:
    # A no-tool-call turn must record the assistant reply in ``messages`` so a
    # REPL keeps prior answers in context on the next turn.
    async def fake_stream(
        http: Any, base_url: str, body: Any, on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[Any], dict[str, Any] | None]:
        return "final answer", [], None

    monkeypatch.setattr(agent, "_stream_turn", fake_stream)
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    out = await agent.run("http://runtime", messages, tools.MCPToolset())

    assert out == "final answer"
    assert messages[-1] == {"role": "assistant", "content": "final answer"}


async def test_agent_streams_stop_message_on_max_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    # A model that keeps calling tools past max_rounds must still deliver a terminal
    # message: streamed via on_token (so the web UI, which drops the return value,
    # still shows it) and recorded in history.
    async def looping_stream(
        http: Any, base_url: str, body: Any, on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[Any], dict[str, Any] | None]:
        return "", [{"id": "1", "name": "x__y", "args": "{}"}], None

    monkeypatch.setattr(agent, "_stream_turn", looping_stream)
    tokens: list[str] = []
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    out = await agent.run("http://runtime", messages, tools.MCPToolset(), None, None, tokens.append, max_rounds=2)

    stopped = "[agent stopped: too many tool rounds]"
    assert out == stopped
    assert tokens == [stopped]  # streamed to the client
    assert messages[-1] == {"role": "assistant", "content": stopped}  # recorded in history
