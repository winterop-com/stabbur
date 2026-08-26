"""Behavior tests for the datetime/timezone/calendar MCP server (in-memory client)."""

from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from stabbur.mcp_servers.datetime import mcp

_WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}


async def _call(name: str, **kw: Any) -> Any:
    async with Client(mcp) as client:
        return (await client.call_tool(name, kw)).data


async def test_server_exposes_tools() -> None:
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
        assert {"current_datetime", "today", "day_of_week"} <= names


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


async def test_datetime_inputs_reject_malformed_spellings() -> None:
    # Only YYYY-MM-DD or YYYY-MM-DDTHH:MM[:SS] are accepted; fromisoformat's looser forms aren't.
    for bad in ("2026-07-01:00", "20260701T00:00", "2026-7-1T00:00"):
        with pytest.raises(ToolError):
            await _call("add_to_date", date=bad, days=1)
    # A well-formed datetime still works and keeps its shape.
    assert await _call("add_to_date", date="2026-07-01T14:30", days=1) == "2026-07-02T14:30:00"


async def test_list_timezones_matches_human_spelling() -> None:
    # "New York" / "new-york" should find America/New_York (spaces/hyphens -> underscore).
    assert "America/New_York" in await _call("list_timezones", contains="new york")
    assert "America/New_York" in await _call("list_timezones", contains="New-York")


async def test_local_timezone_reports_iana_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # A valid TZ env var is used verbatim; the result names the zone and carries an offset.
    monkeypatch.setenv("TZ", "Europe/Oslo")
    result = await _call("local_timezone")
    assert result.startswith("Europe/Oslo (") and result.endswith(")")


async def test_local_timezone_ignores_bogus_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    # A TZ that isn't a real IANA zone is skipped (falls through to the file probes / offset).
    from stabbur.mcp_servers.datetime import app

    monkeypatch.setenv("TZ", "Not/AZone")
    monkeypatch.setattr(app.Path, "is_file", lambda self: False)  # ignore /etc/timezone
    monkeypatch.setattr(app.Path, "resolve", lambda self: app.Path("/no/zoneinfo/here"))  # no marker
    result = await _call("local_timezone")
    assert "Not/AZone" not in result  # the invalid name never leaks
