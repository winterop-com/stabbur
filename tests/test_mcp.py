"""Tests for the date/time MCP server and kodo's MCP client wrapper (in-memory)."""

from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

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


# --- datetime/timezone/calendar tool behavior ------------------------------


async def _call(name: str, **kw: Any) -> Any:
    async with Client(mcp) as client:
        return (await client.call_tool(name, kw)).data


async def test_convert_time_is_dst_correct_for_named_zones() -> None:
    # Oslo is CET (+01) in January, CEST (+02) in July — the offset must follow the
    # *date*, not the current system offset.
    assert await _call("convert_time", when="2026-01-01T12:00", from_timezone="Europe/Oslo", to_timezone="UTC") == (
        "2026-01-01T11:00:00+00:00"
    )
    assert await _call("convert_time", when="2026-07-01T12:00", from_timezone="Europe/Oslo", to_timezone="UTC") == (
        "2026-07-01T10:00:00+00:00"
    )


async def test_convert_time_rejects_nonexistent_dst_gap() -> None:
    # 2026-03-29 02:30 does not exist in Europe/Oslo (clocks jump 02:00 -> 03:00).
    with pytest.raises(ToolError):
        await _call("convert_time", when="2026-03-29T02:30", from_timezone="Europe/Oslo", to_timezone="UTC")


async def test_invalid_timezone_errors() -> None:
    with pytest.raises(ToolError):
        await _call("current_datetime", timezone="Mars/Olympus_Mons")


async def test_add_to_date_clamps_and_keeps_shape() -> None:
    assert await _call("add_to_date", date="2026-01-31", months=1) == "2026-02-28"  # clamp to Feb
    assert await _call("add_to_date", date="2026-07-01", days=1) == "2026-07-02"  # date stays a date
    # A datetime input keeps its time AND offset (no silent truncation to a date).
    assert await _call("add_to_date", date="2026-07-01T00:00:00+02:00", days=1) == "2026-07-02T00:00:00+02:00"


async def test_days_between_is_strict_dates() -> None:
    assert await _call("days_between", start="2026-01-01", end="2026-12-31") == 364
    with pytest.raises(ToolError):  # a datetime is rejected, not silently truncated
        await _call("days_between", start="2026-07-01T23:59:59", end="2026-07-02T00:00:00")


async def test_list_timezones_and_leap_year() -> None:
    assert await _call("list_timezones", contains="oslo") == ["Europe/Oslo"]
    assert await _call("is_leap_year", year=2028) is True
    assert await _call("is_leap_year", year=2027) is False


async def test_what_time_is_it_in_new_york() -> None:
    # "what time is it in new york" — current time carries NY's offset (EST -05 / EDT -04).
    ny = await _call("current_datetime", timezone="America/New_York")
    assert ny.endswith("-04:00") or ny.endswith("-05:00")


async def test_what_day_is_it() -> None:
    # "what day is it" — a valid weekday, and today() gives date + weekday.
    assert (await _call("day_of_week")) in _WEEKDAYS
    weekday = (await _call("today", timezone="America/New_York")).split()[1].strip("()")
    assert weekday in _WEEKDAYS


async def test_time_in_multiple_zones() -> None:
    out = await _call("time_in", timezones=["UTC", "Asia/Tokyo"])
    assert set(out) == {"UTC", "Asia/Tokyo"}
    assert out["UTC"].endswith("+00:00")
    assert out["Asia/Tokyo"].endswith("+09:00")  # Tokyo has no DST, always +09:00


async def test_list_timezones_unfiltered_includes_common_zones() -> None:
    # An unfiltered listing must include UTC and New York (a naive top-100 slice omits both).
    zones = await _call("list_timezones")
    assert "UTC" in zones and "America/New_York" in zones


async def test_year_zero_is_rejected() -> None:
    for name, kw in (("is_leap_year", {"year": 0}), ("month_calendar", {"year": 0, "month": 1})):
        with pytest.raises(ToolError):
            await _call(name, **kw)


async def test_parse_date_rejects_compact_and_week_forms() -> None:
    # date.fromisoformat accepts these, but the docs promise YYYY-MM-DD only.
    for bad in ("20260701", "2026-W27-3"):
        with pytest.raises(ToolError):
            await _call("day_of_week", date=bad)


async def test_convert_time_rejects_bare_date() -> None:
    with pytest.raises(ToolError):
        await _call("convert_time", when="2026-01-01", from_timezone="Europe/Oslo", to_timezone="UTC")


async def test_convert_time_validates_from_timezone_even_with_explicit_offset() -> None:
    # A required param must not be silently ignored when ``when`` already carries an offset.
    with pytest.raises(ToolError):
        await _call("convert_time", when="2026-01-01T12:00+02:00", from_timezone="Bad/Zone", to_timezone="UTC")


async def test_add_to_date_out_of_range_is_a_clean_error() -> None:
    with pytest.raises(ToolError):  # underflow past year 1, surfaced as a ToolError not a raw traceback
        await _call("add_to_date", date="2026-07-01", years=-3000)
