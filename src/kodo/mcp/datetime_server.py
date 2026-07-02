"""A FastMCP server exposing date, time, timezone, and calendar tools.

Everything is stdlib (``datetime``, ``zoneinfo``, ``calendar``) — no extra deps.
Timezones are IANA names (e.g. ``Europe/Oslo``, ``America/New_York``); the special
values ``local`` and ``UTC`` are also accepted. Datetimes are ISO 8601
(``2026-07-01T14:30``, optionally with an offset); dates are ``YYYY-MM-DD``.

Run standalone over stdio: ``kodo-mcp-datetime`` (or ``python -m
kodo.mcp.datetime_server``). Point kodo at it with ``kodo chat --mcp``.
"""

import asyncio
import calendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from fastmcp import FastMCP

mcp: FastMCP = FastMCP("kodo-datetime")

_BAD_TZ = "unknown timezone {name!r} — use an IANA name like 'Europe/Oslo', 'UTC', or 'local'"


# --- helpers ---------------------------------------------------------------


def _tz(name: str) -> ZoneInfo | None:
    """Resolve a timezone name to a tzinfo. ``local``/``""`` → None (system local)."""
    if not name or name.lower() == "local":
        return None
    try:
        return ZoneInfo("UTC" if name.upper() == "UTC" else name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(_BAD_TZ.format(name=name)) from exc


def _now(name: str = "local") -> datetime:
    """The current aware datetime in timezone ``name`` (DST-correct — it's now)."""
    tz = _tz(name)
    return datetime.now(tz) if tz else datetime.now().astimezone()


def _parse_dt(value: str) -> datetime:
    """Parse an ISO date or datetime string into a datetime (a date → midnight)."""
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO datetime {value!r} (expected e.g. '2026-07-01T14:30')") from exc


def _parse_date(value: str) -> date:
    """Parse a strict ISO date (``YYYY-MM-DD``); reject datetimes (no silent truncation)."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid ISO date {value!r} (expected 'YYYY-MM-DD', a date with no time)") from exc


def _is_date_only(value: str) -> bool:
    """Whether an input string is a bare date (no time component)."""
    return "T" not in value and ":" not in value


def _localize(dt: datetime, tz: ZoneInfo | None) -> datetime:
    """Attach a timezone to a naive wall-clock time.

    For a named zone, the offset is resolved for that specific date (DST-aware) and a
    **nonexistent** wall-clock (a spring-forward gap) is rejected. ``tz=None`` means
    the system-local zone (Python resolves its per-date offset).
    """
    if tz is None:
        return dt.astimezone()  # naive → system-local, offset correct for the date
    aware = dt.replace(tzinfo=tz)
    if aware != aware.astimezone(timezone.utc).astimezone(tz):
        raise ValueError(f"{dt.isoformat()} does not exist in that timezone (a spring-forward DST gap)")
    return aware


def _add_months(dt: datetime, months: int) -> datetime:
    """Add ``months`` to a datetime, clamping the day to the target month's length."""
    total = dt.month - 1 + months
    year = dt.year + total // 12
    month = total % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


# --- current time ----------------------------------------------------------


@mcp.tool
def current_datetime(timezone: str = "local") -> str:
    """Current date and time in ISO 8601 with the UTC offset, for a timezone.

    ``timezone`` is an IANA name (e.g. ``Europe/Oslo``), or ``local`` / ``UTC``.
    """
    return _now(timezone).isoformat(timespec="seconds")


@mcp.tool
def today(timezone: str = "local") -> str:
    """Today's date and weekday, e.g. ``2026-07-01 (Wednesday)``, for a timezone."""
    return _now(timezone).strftime("%Y-%m-%d (%A)")


@mcp.tool
def day_of_week(date: str = "") -> str:
    """The weekday name for ``date`` (ISO ``YYYY-MM-DD``), or today if omitted."""
    d = _parse_date(date) if date else _now().date()
    return d.strftime("%A")


@mcp.tool
def time_in(timezones: list[str]) -> dict[str, str]:
    """Current time in each of several timezones — ``{timezone: ISO datetime}``."""
    return {name: _now(name).isoformat(timespec="seconds") for name in timezones}


# --- timezones -------------------------------------------------------------


@mcp.tool
def timezone_offset(timezone: str) -> str:
    """The current UTC offset and abbreviation for a timezone, e.g. ``+02:00 (CEST)``."""
    now = _now(timezone)
    offset = now.strftime("%z")
    pretty = f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"
    return f"{pretty} ({now.tzname()})"


@mcp.tool
def convert_time(when: str, from_timezone: str, to_timezone: str) -> str:
    """Convert an ISO datetime from one timezone to another (returns ISO 8601).

    A bare wall-clock ``when`` (no offset) is interpreted in ``from_timezone`` with
    that date's correct DST offset; an explicit offset in ``when`` is respected.
    A nonexistent wall-clock time (spring-forward gap) is rejected; an ambiguous
    fall-back time uses the earlier (pre-transition) occurrence.
    """
    dt = _parse_dt(when)
    if dt.tzinfo is None:
        dt = _localize(dt, _tz(from_timezone))
    return dt.astimezone(_tz(to_timezone)).isoformat(timespec="seconds")


@mcp.tool
def list_timezones(contains: str = "") -> list[str]:
    """IANA timezone names, optionally filtered to those containing ``contains``.

    Case-insensitive substring match (e.g. ``Europe``, ``oslo``). Sorted; capped at
    100 results so the list stays usable.
    """
    q = contains.lower()
    return sorted(z for z in available_timezones() if q in z.lower())[:100]


# --- calendar & arithmetic -------------------------------------------------


@mcp.tool
def days_between(start: str, end: str) -> int:
    """Whole days from ``start`` to ``end`` (ISO dates). Negative if ``end`` is earlier."""
    return (_parse_date(end) - _parse_date(start)).days


@mcp.tool
def add_to_date(date: str, days: int = 0, weeks: int = 0, months: int = 0, years: int = 0) -> str:
    """Add a duration to a date/datetime and return it in the same shape (ISO 8601).

    ``date`` is an ISO date or datetime. Any component may be negative to subtract.
    Month/year math clamps the day (Jan 31 + 1 month → Feb 28/29). A datetime input
    keeps its time and offset; a date input returns a date.
    """
    dt = _add_months(_parse_dt(date), months + years * 12) + timedelta(days=days, weeks=weeks)
    return dt.date().isoformat() if _is_date_only(date) else dt.isoformat(timespec="seconds")


@mcp.tool
def week_number(date: str = "") -> str:
    """ISO week for ``date`` (or today), e.g. ``2026-W27`` (year-week)."""
    iso = (_parse_date(date) if date else _now().date()).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


@mcp.tool
def is_leap_year(year: int) -> bool:
    """Whether ``year`` is a leap year."""
    return calendar.isleap(year)


@mcp.tool
def month_calendar(year: int, month: int) -> str:
    """A text calendar for a month (weeks start Monday), e.g. for July 2026."""
    if not 1 <= month <= 12:
        raise ValueError(f"month must be 1-12, got {month}")
    return calendar.TextCalendar(firstweekday=0).formatmonth(year, month).rstrip()


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn).

    Swallow Ctrl-C / stream-closed shutdown noise so exiting is quiet.
    """
    try:
        mcp.run(show_banner=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass


if __name__ == "__main__":
    main()
