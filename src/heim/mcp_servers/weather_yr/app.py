"""A FastMCP server exposing weather forecasts from the free met.no (yr.no) APIs over stdio.

Give it a place name (geocoded via OpenStreetMap Nominatim) or coordinates and it returns the
current conditions plus a near-term hourly and multi-day outlook. No API key; the endpoints are
fixed so there is no arbitrary-fetch surface. See :mod:`heim.mcp_servers.weather_yr.core`.

Run standalone over stdio: ``heim-mcp-weather-yr`` (or ``python -m heim.mcp_servers.weather_yr``). Point
heim at it with ``heim chat --mcp weather-yr`` or a ``[[mcp]]`` block.
"""

import asyncio
from typing import Any

from fastmcp import FastMCP

from heim.mcp_servers.weather_yr.core import Location, WeatherSettings, fetch_forecast, geocode

mcp: FastMCP = FastMCP("heim-weather-yr")


@mcp.tool
def weather_forecast(place: str) -> dict[str, Any]:
    """Weather for a named place: current conditions + hourly (next ~12h) + daily (next ~7 days).

    Resolves `place` (a city/town/address) to coordinates, then fetches the met.no forecast. Times
    are UTC; temperatures Celsius; conditions are met.no symbol codes (e.g. "partlycloudy_day",
    "lightrain"). If the place can't be found, `found` is false.
    """
    settings = WeatherSettings()
    location = geocode(place, settings)
    if location is None:
        return {"found": False, "place": place, "error": f"could not find a place named {place!r}"}
    forecast = fetch_forecast(location, settings)
    return {"found": True, **forecast.model_dump()}


@mcp.tool
def weather_forecast_at(latitude: float, longitude: float) -> dict[str, Any]:
    """Weather for exact coordinates: current conditions + hourly (next ~12h) + daily (next ~7 days).

    Use when you already have latitude/longitude. Times are UTC; temperatures Celsius; conditions
    are met.no symbol codes.
    """
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        raise ValueError("latitude must be in [-90, 90] and longitude in [-180, 180]")
    location = Location(name=f"{latitude:.4f}, {longitude:.4f}", latitude=latitude, longitude=longitude)
    return {"found": True, **fetch_forecast(location).model_dump()}


def main() -> None:
    """Run the server over stdio (for an MCP client to spawn); quiet on Ctrl-C."""
    try:
        mcp.run(show_banner=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
