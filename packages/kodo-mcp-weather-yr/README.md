# kodo-mcp-weather-yr

An MCP server giving an assistant **weather forecasts** from the free, key-less
[met.no (yr.no)](https://api.met.no/) API.

Tools:

- `weather_forecast(place)` — geocodes a place name (via OpenStreetMap Nominatim), then returns
  the current conditions + hourly (next ~12h) + daily (next ~7 days) forecast.
- `weather_forecast_at(latitude, longitude)` — same, for exact coordinates.

Times are UTC; temperatures Celsius; conditions are met.no `symbol_code`s (e.g. `clearsky_day`).
The endpoints are fixed (the model supplies a place/coords, never a URL), so there's no
arbitrary-fetch surface. Per met.no policy an identifying `User-Agent` is sent — override it with
`KODO_WEATHER_USER_AGENT`.

Run standalone over stdio: `kodo-mcp-weather-yr` (or `python -m kodo_mcp_weather_yr`). Attach it
with `kodo chat --mcp weather-yr`, or `kodo mcp add weather-yr` in a project.
