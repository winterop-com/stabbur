"""Weather lookups against the free met.no (yr.no) APIs — no MCP, no heim imports.

Two upstreams, both free and key-less, both requiring an identifying ``User-Agent``:

* **Nominatim** (OpenStreetMap) — geocode a place name to latitude/longitude.
* **met.no Locationforecast** — the forecast for those coordinates.

The endpoints are fixed (the model supplies a place name or coordinates, never a URL), so there
is no arbitrary-fetch/SSRF surface. Per met.no policy the ``User-Agent`` identifies this app
(override with ``HEIM_WEATHER_USER_AGENT``) and coordinates are truncated to 4 decimals to aid
their caching.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_METNO = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
_TIMEOUT = 15.0
_HOURLY_MAX = 12
_DAILY_MAX = 7


class WeatherSettings(BaseSettings):
    """Config via ``HEIM_WEATHER_*`` env. The User-Agent identifies this app to met.no/Nominatim."""

    model_config = SettingsConfigDict(env_prefix="HEIM_WEATHER_", extra="ignore")

    user_agent: str = "heim-mcp-weather-yr/0.1 github.com/winterop-com/heim"


class Location(BaseModel):
    """A resolved place: display name + coordinates."""

    model_config = ConfigDict(frozen=True)

    name: str
    latitude: float
    longitude: float


class CurrentWeather(BaseModel):
    """The conditions right now (met.no's first timeseries entry)."""

    model_config = ConfigDict(frozen=True)

    time: str  # UTC ISO 8601
    temperature_c: float | None = None
    condition: str | None = None  # met.no symbol_code, e.g. "partlycloudy_day"
    wind_speed_mps: float | None = None
    wind_from_deg: float | None = None
    humidity_pct: float | None = None
    precipitation_mm_next_1h: float | None = None


class HourlyEntry(BaseModel):
    """One hour of the near-term forecast."""

    model_config = ConfigDict(frozen=True)

    time: str  # UTC ISO 8601
    temperature_c: float | None = None
    condition: str | None = None
    precipitation_mm: float | None = None


class DailyEntry(BaseModel):
    """A day's min/max temperature and a midday condition."""

    model_config = ConfigDict(frozen=True)

    date: str  # YYYY-MM-DD (UTC)
    min_c: float | None = None
    max_c: float | None = None
    condition: str | None = None


class Forecast(BaseModel):
    """A place's current conditions plus near-term hourly and multi-day outlook."""

    model_config = ConfigDict(frozen=True)

    location: Location
    updated: str  # UTC ISO 8601 of the first entry
    current: CurrentWeather
    hourly: list[HourlyEntry]
    daily: list[DailyEntry]


def _client(user_agent: str) -> httpx.Client:
    # met.no/Nominatim block requests without an identifying User-Agent.
    return httpx.Client(headers={"User-Agent": user_agent}, timeout=_TIMEOUT, follow_redirects=True)


def geocode(place: str, settings: WeatherSettings | None = None) -> Location | None:
    """Resolve a place name to a :class:`Location` via Nominatim, or ``None`` if not found."""
    settings = settings or WeatherSettings()
    with _client(settings.user_agent) as client:
        resp = client.get(_NOMINATIM, params={"q": place, "format": "jsonv2", "limit": 1})
        resp.raise_for_status()
        rows = resp.json()
    if not rows:
        return None
    row = rows[0]
    return Location(name=row["display_name"], latitude=float(row["lat"]), longitude=float(row["lon"]))


def _symbol(entry: dict[str, Any]) -> str | None:
    data = entry.get("data", {})
    for window in ("next_1_hours", "next_6_hours", "next_12_hours"):
        code = data.get(window, {}).get("summary", {}).get("symbol_code")
        if code:
            return str(code)
    return None


def _precip(entry: dict[str, Any], window: str = "next_1_hours") -> float | None:
    value = entry.get("data", {}).get(window, {}).get("details", {}).get("precipitation_amount")
    return float(value) if value is not None else None


def _daily(timeseries: list[dict[str, Any]]) -> list[DailyEntry]:
    """Roll the timeseries up into per-date min/max temperature + a midday condition."""
    by_date: dict[str, list[dict]] = {}
    for entry in timeseries:
        by_date.setdefault(entry["time"][:10], []).append(entry)
    out: list[DailyEntry] = []
    for date in sorted(by_date)[:_DAILY_MAX]:
        temps = [
            t
            for e in by_date[date]
            if (t := e.get("data", {}).get("instant", {}).get("details", {}).get("air_temperature")) is not None
        ]
        # Condition from the entry nearest 12:00 UTC (a representative "daytime" symbol).
        midday = min(by_date[date], key=lambda e: abs(int(e["time"][11:13]) - 12))
        out.append(
            DailyEntry(
                date=date,
                min_c=min(temps) if temps else None,
                max_c=max(temps) if temps else None,
                condition=_symbol(midday),
            )
        )
    return out


def fetch_forecast(location: Location, settings: WeatherSettings | None = None) -> Forecast:
    """Fetch + parse the met.no forecast for ``location`` (coordinates truncated to 4 decimals)."""
    settings = settings or WeatherSettings()
    with _client(settings.user_agent) as client:
        resp = client.get(_METNO, params={"lat": round(location.latitude, 4), "lon": round(location.longitude, 4)})
        resp.raise_for_status()
        series = resp.json().get("properties", {}).get("timeseries") or []

    if not series:
        raise ValueError(f"met.no returned no forecast for ({location.latitude}, {location.longitude})")
    first = series[0]
    instant = first.get("data", {}).get("instant", {}).get("details", {})
    current = CurrentWeather(
        time=first["time"],
        temperature_c=instant.get("air_temperature"),
        condition=_symbol(first),
        wind_speed_mps=instant.get("wind_speed"),
        wind_from_deg=instant.get("wind_from_direction"),
        humidity_pct=instant.get("relative_humidity"),
        precipitation_mm_next_1h=_precip(first),
    )
    hourly = [
        HourlyEntry(
            time=e["time"],
            temperature_c=e.get("data", {}).get("instant", {}).get("details", {}).get("air_temperature"),
            condition=e.get("data", {}).get("next_1_hours", {}).get("summary", {}).get("symbol_code"),
            precipitation_mm=_precip(e),
        )
        for e in series
        if "next_1_hours" in e.get("data", {})
    ][:_HOURLY_MAX]
    return Forecast(location=location, updated=first["time"], current=current, hourly=hourly, daily=_daily(series))
