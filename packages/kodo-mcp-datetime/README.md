# kodo-mcp-datetime

A small [MCP](https://modelcontextprotocol.io) server exposing date, time,
timezone, and calendar tools — pure stdlib, served over stdio. Kodo is the MCP
*client*; this is one of the servers it ships with so tools work out of the box.

Tools: current time (any IANA timezone), timezone offsets, DST-correct time
conversion, calendars, week numbers, leap years, and date arithmetic.

Run it standalone over stdio (any MCP host), or point kodo at it:

```
kodo-mcp-datetime                     # or: python -m kodo_mcp_datetime
kodo chat --mcp kodo-mcp-datetime     # kodo spawns it and exposes its tools
```

## This package is the template for new MCP servers

Every in-repo MCP server is its own uv workspace member with this exact shape:

```
packages/kodo-mcp-<name>/
  pyproject.toml                       # name = kodo-mcp-<name>; script -> kodo_mcp_<name>:main
  README.md
  src/kodo_mcp_<name>/
    __init__.py                        # re-exports `mcp` and `main` from app
    __main__.py                        # enables `python -m kodo_mcp_<name>`
    app.py                             # the server: builds `mcp`, registers @mcp.tool, defines main()
  tests/test_<name>.py                 # in-memory FastMCP Client hitting the tools
```

To add one (e.g. `kodo-mcp-weather-yr`):

1. Copy this directory to `packages/kodo-mcp-weather-yr/`.
2. Rename the package dir to `src/kodo_mcp_weather_yr/` and update `name` /
   `[project.scripts]` in `pyproject.toml` (both sides: `kodo-mcp-weather-yr =
   "kodo_mcp_weather_yr:main"`).
3. Replace the tools in `app.py` with your own `@mcp.tool` functions.
4. In the **root** `pyproject.toml`, add the package to `[project].dependencies`
   and to `[tool.uv.sources]` (`kodo-mcp-weather-yr = { workspace = true }`) so
   kodo bundles it and the script lands on `PATH`.
5. `uv sync`, then `make lint` / `make test` — the workspace globs pick it up.

Keep the servers dependency-light and stdio-only; kodo owns the client and the
agent loop, so a server just exposes tools.
