"""Tests for the weather parsing + settings (no network — met.no is stubbed)."""

from typing import Any

import pytest

from heim.mcp_servers.weather_yr.core import Location, WeatherSettings, fetch_forecast


def _sample_metno() -> dict[str, Any]:
    """A minimal met.no compact response spanning two UTC dates."""
    return {
        "properties": {
            "timeseries": [
                {
                    "time": "2026-07-05T10:00:00Z",
                    "data": {
                        "instant": {"details": {"air_temperature": 19.5, "wind_speed": 4.0, "relative_humidity": 44.0}},
                        "next_1_hours": {
                            "summary": {"symbol_code": "partlycloudy_day"},
                            "details": {"precipitation_amount": 0.0},
                        },
                    },
                },
                {
                    "time": "2026-07-05T12:00:00Z",
                    "data": {
                        "instant": {"details": {"air_temperature": 22.0}},
                        "next_1_hours": {
                            "summary": {"symbol_code": "clearsky_day"},
                            "details": {"precipitation_amount": 0.0},
                        },
                    },
                },
                {
                    "time": "2026-07-06T12:00:00Z",
                    "data": {
                        "instant": {"details": {"air_temperature": 15.0}},
                        "next_6_hours": {
                            "summary": {"symbol_code": "lightrain"},
                            "details": {"precipitation_amount": 1.2},
                        },
                    },
                },
            ]
        }
    }


class _FakeClient:
    """A stand-in for ``httpx.Client``: records request params, returns the sample forecast."""

    last_params: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: Any) -> None: ...

    def get(self, url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> "_FakeClient":
        _FakeClient.last_params = params or {}
        return self

    def raise_for_status(self) -> None: ...
    def json(self) -> dict[str, Any]:
        return _sample_metno()


def test_settings_default_user_agent() -> None:
    assert "heim-mcp-weather-yr" in WeatherSettings().user_agent


def test_fetch_forecast_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("heim.mcp_servers.weather_yr.core.httpx.Client", _FakeClient)
    fc = fetch_forecast(Location(name="Oslo", latitude=59.9133, longitude=10.739))

    assert fc.location.name == "Oslo"
    assert fc.updated == "2026-07-05T10:00:00Z"
    assert fc.current.temperature_c == 19.5
    assert fc.current.condition == "partlycloudy_day"
    assert fc.current.humidity_pct == 44.0
    # hourly: only the two entries carrying next_1_hours
    assert [h.condition for h in fc.hourly] == ["partlycloudy_day", "clearsky_day"]
    # daily: two dates; the 5th's max/min from its two entries, the 6th's condition from next_6_hours
    days = {d.date: d for d in fc.daily}
    assert set(days) == {"2026-07-05", "2026-07-06"}
    assert days["2026-07-05"].max_c == 22.0
    assert days["2026-07-05"].min_c == 19.5
    assert days["2026-07-06"].condition == "lightrain"


def test_coordinates_truncated_to_4dp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("heim.mcp_servers.weather_yr.core.httpx.Client", _FakeClient)
    fetch_forecast(Location(name="x", latitude=59.913330, longitude=10.738970))
    assert _FakeClient.last_params["lat"] == 59.9133  # met.no policy: 4 decimals
    assert _FakeClient.last_params["lon"] == 10.739
