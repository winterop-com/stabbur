"""A FastMCP server exposing date, time, timezone, and calendar tools.

This module holds the server itself: it builds the ``mcp`` app, registers each
``@mcp.tool``, and defines ``main()`` (the stdio entry point). ``__init__`` re-exports
``mcp`` / ``main``; ``__main__`` runs it for ``python -m``. A new MCP server copies this
package's shape and replaces the tools here — see the package README.

Everything is stdlib (``datetime``, ``zoneinfo``, ``calendar``) — no extra deps.
Timezones are IANA names (e.g. ``Europe/Oslo``, ``America/New_York``); the special
values ``local`` and ``UTC`` are also accepted. Datetimes are ISO 8601
(``2026-07-01T14:30``, optionally with an offset); dates are ``YYYY-MM-DD``.

Run standalone over stdio: ``stabbur-mcp-datetime`` (or ``python -m
stabbur.mcp_servers.datetime``). Point stabbur at it with ``stabbur chat --mcp``.
"""

import asyncio
import calendar
import os
import re
from datetime import MAXYEAR, MINYEAR, date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from fastmcp import FastMCP

mcp: FastMCP = FastMCP("stabbur-datetime")

_BAD_TZ = "unknown timezone {name!r} — use an IANA name like 'Europe/Oslo', 'UTC', or 'local'"
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}$")  # strict YYYY-MM-DD (no compact/week forms)
# YYYY-MM-DD, optionally T/space + HH:MM[:SS][.ffffff][+HH:MM | Z] — the documented spellings.
_ISO_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?([+-]\d{2}:\d{2}|Z)?)?$")

# A short, geographically spread set returned by an unfiltered ``list_timezones`` — the
# full IANA set is ~600 zones, so an unfiltered slice would omit UTC and most cities.
_COMMON_ZONES = [
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Sao_Paulo",
    "Europe/London",
    "Europe/Oslo",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Moscow",
    "Africa/Cairo",
    "Africa/Johannesburg",
    "Asia/Dubai",
    "Asia/Kolkata",
    "Asia/Shanghai",
    "Asia/Tokyo",
    "Asia/Singapore",
    "Australia/Sydney",
    "Pacific/Auckland",
]


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


def _local_timezone_name() -> str | None:
    """Best-effort IANA name for the host's local timezone (e.g. ``Europe/Oslo``), or None.

    stdlib can't name the local zone directly (``astimezone()`` yields a fixed-offset tzinfo, and
    an offset like ``+02:00`` maps to many zones), so probe the usual sources in order: the ``TZ``
    env var, ``/etc/timezone`` (Debian/Ubuntu), then the ``/etc/localtime`` symlink target (Linux
    and macOS point it into ``.../zoneinfo/<Area>/<City>``). Each candidate is validated as a real
    IANA zone before it's returned.
    """
    candidates: list[str] = []
    env_tz = os.environ.get("TZ")
    if env_tz:
        candidates.append(env_tz)
    try:
        etc = Path("/etc/timezone")
        if etc.is_file():
            candidates.append(etc.read_text(encoding="utf-8").strip())
    except OSError:
        pass
    try:
        target = str(Path("/etc/localtime").resolve())
        if "zoneinfo/" in target:
            candidates.append(target.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    for name in candidates:
        if name and name.lower() != "local":
            try:
                ZoneInfo(name)
                return name
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return None


def _parse_dt(value: str) -> datetime:
    """Parse a strict ISO date or datetime into a datetime (a bare date → midnight).

    Only the documented spellings are accepted (``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM[:SS]``
    with an optional offset); compact/partial forms ``fromisoformat`` would otherwise take
    (e.g. ``20260701``, ``2026-07-01:00``) are rejected.
    """
    bad = f"invalid ISO datetime {value!r} (expected 'YYYY-MM-DD' or e.g. '2026-07-01T14:30')"
    if not _ISO_DATETIME.fullmatch(value):
        raise ValueError(bad)
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(bad) from exc


def _parse_date(value: str) -> date:
    """Parse a strict ISO date (``YYYY-MM-DD``); reject datetimes and compact/week forms."""
    bad = f"invalid ISO date {value!r} (expected 'YYYY-MM-DD', a date with no time)"
    if not _ISO_DATE.fullmatch(value):  # date.fromisoformat also accepts 20260701 / 2026-W27-3
        raise ValueError(bad)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(bad) from exc


def _check_year(year: int) -> None:
    """Reject years outside the civil-date range dates and calendars support."""
    if not MINYEAR <= year <= MAXYEAR:
        raise ValueError(f"year must be {MINYEAR}-{MAXYEAR}, got {year}")


def _is_date_only(value: str) -> bool:
    """Whether an input string is a bare ``YYYY-MM-DD`` (no time component)."""
    return bool(_ISO_DATE.fullmatch(value))


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
    """Current time in each of several timezones — ``{timezone: ISO datetime}``.

    Keyed by timezone, so repeated names collapse to one entry.
    """
    return {name: _now(name).isoformat(timespec="seconds") for name in timezones}


# --- timezones -------------------------------------------------------------


@mcp.tool
def local_timezone() -> str:
    """The host's own IANA timezone and current offset, e.g. ``Europe/Oslo (+02:00)``.

    Use this to answer "what's my timezone / my local time" — the other tools take a timezone but
    can't name the machine's own, and an offset alone (``+02:00``) doesn't identify a unique zone.
    Falls back to the offset + abbreviation if the IANA name can't be determined.
    """
    now = _now("local")
    offset = now.strftime("%z")
    pretty = f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"
    name = _local_timezone_name()
    return f"{name} ({pretty})" if name else f"unknown IANA name — local offset {pretty} ({now.tzname()})"


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

    ``when`` must include a time (``2026-07-01T14:30``); a bare date is rejected. A
    wall-clock with no offset is interpreted in ``from_timezone`` at that date's correct
    DST offset; an explicit offset in ``when`` is respected but ``from_timezone`` is still
    validated. A nonexistent wall-clock time (spring-forward gap) is rejected; an ambiguous
    fall-back time uses the earlier (pre-transition) occurrence.
    """
    if _is_date_only(when):
        raise ValueError(
            f"convert_time needs a datetime with a time, got a bare date {when!r} (e.g. '2026-07-01T14:30')"
        )
    from_tz = _tz(from_timezone)  # validated even when ``when`` carries an explicit offset
    to_tz = _tz(to_timezone)
    dt = _parse_dt(when)
    if dt.tzinfo is None:
        dt = _localize(dt, from_tz)
    return dt.astimezone(to_tz).isoformat(timespec="seconds")


@mcp.tool
def list_timezones(contains: str = "") -> list[str]:
    """IANA timezone names filtered by ``contains``; a common set when unfiltered.

    With no filter, returns a short spread of common zones (the full set is ~600, so an
    unfiltered slice would omit UTC and most cities). With a filter, a case-insensitive
    substring match with spaces/hyphens treated as ``_`` (so ``new york`` matches
    ``America/New_York``), sorted and capped at 100 results.
    """
    if not contains:
        return _COMMON_ZONES
    q = contains.strip().lower().replace(" ", "_").replace("-", "_")
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
    try:
        dt = _add_months(_parse_dt(date), months + years * 12) + timedelta(days=days, weeks=weeks)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"result is out of the supported date range ({exc})") from exc
    return dt.date().isoformat() if _is_date_only(date) else dt.isoformat(timespec="seconds")


@mcp.tool
def week_number(date: str = "") -> str:
    """ISO week for ``date`` (or today), e.g. ``2026-W27`` (year-week)."""
    iso = (_parse_date(date) if date else _now().date()).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


@mcp.tool
def is_leap_year(year: int) -> bool:
    """Whether ``year`` is a leap year."""
    _check_year(year)
    return calendar.isleap(year)


@mcp.tool
def month_calendar(year: int, month: int) -> str:
    """A text calendar for a month (weeks start Monday), e.g. for July 2026."""
    _check_year(year)
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
