"""Tests for kodo's MCP client wrapper and agent loop (in-memory, no live runtime).

The datetime server's own behavior is tested in ``packages/mcp/tests/test_datetime.py``;
here we use a tiny inline FastMCP server as a fixture so these tests stay independent of
any particular MCP package.
"""

from typing import Any

import pytest
from fastmcp import Client, FastMCP

from kodo import agent, tools

# A minimal in-memory MCP server standing in for any real one — two trivial tools.
_fixture: FastMCP = FastMCP("fixture")


@_fixture.tool
def today() -> str:
    """A deterministic date string."""
    return "2026-07-01"


@_fixture.tool
def day_of_week() -> str:
    """A deterministic weekday."""
    return "Wednesday"


async def test_toolset_namespaces_and_calls() -> None:
    # Tools are namespaced by server (<prefix>__<tool>); call routes back to the
    # underlying tool name.
    async with Client(_fixture) as client:
        toolset = tools.MCPToolset()
        await toolset.add(client, "srv")

        assert "srv__today" in toolset.names
        assert "today" not in toolset.names  # bare name is not exposed
        schema = next(s for s in toolset.schemas if s["function"]["name"] == "srv__today")
        assert schema["type"] == "function"
        assert "parameters" in schema["function"]

        assert (await toolset.call("srv__day_of_week", {})).text == "Wednesday"
        assert (await toolset.call("no_such_tool", {})).text.startswith("error:")


async def test_toolset_dedupes_within_a_prefix() -> None:
    # Same server added twice under the same prefix must not duplicate tools.
    async with Client(_fixture) as a, Client(_fixture) as b:
        toolset = tools.MCPToolset()
        await toolset.add(a, "srv")
        await toolset.add(b, "srv")
        assert toolset.names.count("srv__today") == 1


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


class _ImageToolset:
    """A toolset whose one tool returns text plus an image (like browser_take_screenshot)."""

    schemas: list[dict[str, Any]] = []

    async def call(self, name: str, args: dict[str, Any], timeout: float | None = None) -> tools.ToolResult:
        return tools.ToolResult(text="captured", images=["data:image/png;base64,QUJD"])


def _one_tool_then_done() -> Any:
    """A staged _stream_turn: round 1 calls a tool, round 2 answers with plain text."""
    rounds = iter([("", [{"id": "1", "name": "b__shot", "args": "{}"}], None), ("done", [], None)])

    async def staged(
        http: Any, base_url: str, body: Any, on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[Any], dict[str, Any] | None]:
        return next(rounds)

    return staged


async def test_agent_feeds_tool_image_to_vision_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # A vision model must receive a tool-returned image as a follow-up user image message, so it
    # actually sees (e.g.) a screenshot instead of hallucinating what it "saw" after the pixels
    # were dropped.
    monkeypatch.setattr(agent, "_stream_turn", _one_tool_then_done())
    messages: list[dict[str, Any]] = [{"role": "user", "content": "screenshot it"}]
    out = await agent.run("http://runtime", messages, _ImageToolset(), vision=True)  # type: ignore[arg-type]

    assert out == "done"
    tool_msg = next(m for m in messages if m["role"] == "tool")
    assert tool_msg["content"] == "captured"  # text stays in the tool message
    img_users = [m for m in messages if m["role"] == "user" and isinstance(m["content"], list)]
    assert img_users, "a vision model should get the image as a user message"
    parts = img_users[-1]["content"]
    assert any(p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:image/png") for p in parts)


async def test_agent_notes_tool_image_for_text_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # A text-only model can't see the image: it must be TOLD one was returned rather than have it
    # silently dropped (which is what makes a vision-less model hallucinate a description).
    monkeypatch.setattr(agent, "_stream_turn", _one_tool_then_done())
    messages: list[dict[str, Any]] = [{"role": "user", "content": "screenshot it"}]
    await agent.run("http://runtime", messages, _ImageToolset(), vision=False)  # type: ignore[arg-type]

    tool_msg = next(m for m in messages if m["role"] == "tool")
    assert "cannot view images" in tool_msg["content"]
    assert not any(m["role"] == "user" and isinstance(m["content"], list) for m in messages)  # no image message


def test_result_content_extracts_image_blocks() -> None:
    # A result whose content carries an image block becomes a data: URL alongside the text.
    text_block = type("T", (), {"type": "text", "text": "shot"})()
    img_block = type("I", (), {"type": "image", "data": "QUJD", "mimeType": "image/png"})()
    result = type("R", (), {"data": None, "content": [text_block, img_block]})()
    tr = tools._result_content(result)
    assert tr.text == "shot"
    assert tr.images == ["data:image/png;base64,QUJD"]


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


async def test_agent_stop_message_reaches_an_async_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    # The /api/chat sink is async (queue.put): the max-rounds terminal message must go
    # through _emit and be awaited — a bare on_token(...) call would silently drop it
    # for exactly the streaming clients it exists for.
    async def looping_stream(
        http: Any, base_url: str, body: Any, on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[Any], dict[str, Any] | None]:
        return "", [{"id": "1", "name": "x__y", "args": "{}"}], None

    monkeypatch.setattr(agent, "_stream_turn", looping_stream)
    tokens: list[str] = []

    async def sink(text: str) -> None:
        tokens.append(text)

    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    out = await agent.run("http://runtime", messages, tools.MCPToolset(), None, None, sink, max_rounds=1)

    assert out == "[agent stopped: too many tool rounds]"
    assert tokens == [out]  # delivered through the async sink, not dropped


async def test_agent_recovers_from_a_hung_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    # A tool call that hangs (surfaced as a timeout) must NOT stall the loop: the error is
    # fed back to the model and the loop continues to a final answer. Regression for a
    # 40-min benchmark hang where a wedged MCP tool blocked the loop indefinitely.
    rounds = iter(
        [
            ("", [{"id": "1", "name": "x__y", "args": "{}"}], None),  # round 1: a tool call
            ("recovered", [], None),  # round 2: a plain answer
        ]
    )

    async def staged_stream(
        http: Any, base_url: str, body: Any, on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[Any], dict[str, Any] | None]:
        return next(rounds)

    class _HangingToolset:
        schemas: list[dict[str, Any]] = []

        def __init__(self) -> None:
            self.timeouts: list[float | None] = []

        async def call(self, name: str, args: dict[str, Any], timeout: float | None = None) -> str:
            self.timeouts.append(timeout)
            raise TimeoutError("Timed out")

    monkeypatch.setattr(agent, "_stream_turn", staged_stream)
    toolset = _HangingToolset()
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    out = await agent.run("http://runtime", messages, toolset)  # type: ignore[arg-type]

    assert out == "recovered"  # loop continued past the hung tool
    assert toolset.timeouts == [120.0]  # default per-call timeout was applied
    tool_msg = next(m for m in messages if m.get("role") == "tool")
    assert "error:" in tool_msg["content"] and "Timed out" in tool_msg["content"]


async def test_toolset_call_forwards_timeout_to_client() -> None:
    # MCPToolset.call must pass the per-call timeout down to fastmcp so a wedged server is bounded.
    class _RecordingClient:
        def __init__(self) -> None:
            self.timeouts: list[float | None] = []

        async def call_tool(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> Any:
            self.timeouts.append(timeout)
            return type("R", (), {"data": "ok"})()

    toolset = tools.MCPToolset()
    client = _RecordingClient()
    toolset._owner["srv__t"] = (client, "t")  # type: ignore[assignment]
    result = await toolset.call("srv__t", {}, timeout=7.5)

    assert result.text == "ok"
    assert client.timeouts == [7.5]


async def test_connect_skips_a_failing_server_and_records_it() -> None:
    # An uninstalled/bad server must not abort the others: it's skipped and recorded in errors.
    async with tools.connect([("bogus", ["kodo-nonexistent-server-xyz"])]) as toolset:
        assert toolset.schemas == []  # nothing from the failed server
        assert toolset.errors and toolset.errors[0][0] == "bogus"  # failure recorded with its label


async def test_subset_restricts_execution_not_just_display() -> None:
    # A tool outside the enabled subset must not execute even if the model calls it (V-1).
    executed: list[str] = []

    class _Client:
        async def call_tool(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> Any:
            executed.append(name)
            return type("R", (), {"data": "ran"})()

    ts = tools.MCPToolset()
    client = _Client()
    ts._owner = {"srv__safe": (client, "safe"), "srv__danger": (client, "danger")}  # type: ignore[dict-item]
    ts.schemas = [{"type": "function", "function": {"name": n}} for n in ("srv__safe", "srv__danger")]

    view = ts.subset({"srv__safe"})
    assert view.names == ["srv__safe"]  # display restricted
    assert "unknown tool" in (await view.call("srv__danger", {})).text  # execution refused
    assert executed == []  # the disabled tool never ran
    assert (await view.call("srv__safe", {})).text == "ran"  # the enabled one still works
    assert executed == ["safe"]


async def test_agent_rejects_unparseable_tool_args(monkeypatch: pytest.MonkeyPatch) -> None:
    # Malformed tool-call JSON must NOT run the tool with empty args (V-7): feed the parse
    # error back and continue, so the model can resend valid arguments.
    rounds = iter([("", [{"id": "1", "name": "x__y", "args": "{not valid json"}], None), ("done", [], None)])

    async def staged_stream(
        http: Any, base_url: str, body: Any, on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[Any], dict[str, Any] | None]:
        return next(rounds)

    class _RecordingToolset:
        schemas: list[dict[str, Any]] = []

        def __init__(self) -> None:
            self.calls: list[str] = []

        async def call(self, name: str, args: dict[str, Any], timeout: float | None = None) -> tools.ToolResult:
            self.calls.append(name)
            return tools.ToolResult(text="ran")

    monkeypatch.setattr(agent, "_stream_turn", staged_stream)
    toolset = _RecordingToolset()
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    out = await agent.run("http://runtime", messages, toolset)  # type: ignore[arg-type]

    assert out == "done"
    assert toolset.calls == []  # the tool was never executed on unparseable args
    tool_msg = next(m for m in messages if m.get("role") == "tool")
    assert "could not parse" in tool_msg["content"]
