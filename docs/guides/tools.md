# Tools (MCP)

stabbur is an **MCP client**: a chat model can call tools that stabbur runs as MCP
servers (the agent loop executes the call and feeds the result back).

Tool config is the ecosystem-standard **`mcpServers` JSON** — the same shape Claude
Desktop, Claude Code, and Cursor use, so a server's README snippet pastes straight in.
It lives at two levels that **merge**:

- **Global** — `~/.config/stabbur/mcp.json`, the machine-wide default toolset every chat
  gets (what `sb setup` seeds and `sb mcp add --global` writes).
- **Project** — `.mcp.json` next to the `stabbur.toml` stabbur finds by walking up from the
  current directory (`./.mcp.json` when there is no project), the assistant's own tools
  (`sb mcp add`). A project entry overrides a global one of the same name.

The bundled first-party `stabbur-mcp-*` servers (`datetime`, `utils`, `memory`,
`weather-yr`, `search`, `files`, `git`, `exec`, `http`) are always available (base deps) and entered
by package name. (The `benchmark` package is a dev tool and does *not* advertise itself
as an assistant tool.) `stabbur.toml` does **not** carry tools.

## Browse and add — `sb mcp`

`sb mcp list` shows two groups: **installed plugins first** (first-party
`stabbur-mcp-*` packages that stabbur controls — the recommended set, no external runtime),
then an **external catalog** of third-party servers (DHIS2, `fetch`, `git`, `sqlite`,
`filesystem`, …) as a fallback for tools stabbur doesn't ship yet. A `✓` marks a server
already in the current project's `.mcp.json`.

```bash
sb mcp list             # installed plugins, then external catalog (✓ = already in .mcp.json)
sb mcp add utils        # add to this project's ./.mcp.json
sb mcp add --global datetime   # add to the machine-global ~/.config/stabbur/mcp.json
sb mcp add dhis2        # external server — prints a "setup:" hint when it needs config
sb mcp remove utils     # drop it again (--global for the machine layer)
```

`sb mcp add` resolves the name against installed plugins first, then the external
catalog, and writes an entry to `.mcp.json` (or the global file with `--global`).
External commands may carry a placeholder (a path, a DHIS2 profile) — the `setup:` hint
says what to edit. You can also pick plugin tools interactively in the `sb init`
wizard, or write the JSON by hand:

### First-party servers

stabbur bundles pure-stdlib/light plugins — always available:

- **`datetime`**, **`utils`** — date/time/calendar and text/encoding/hashing/math helpers.
- **`memory`** (`stabbur-mcp-memory`) — persistent notes / key-value memory the assistant reads and
  writes (`memory_set/get/list/search/delete`). Saved as a JSON file in the library
  (`<STABBUR_LIBRARY_ROOT>/.stabbur/memory/notes.json`), so it travels with the drive and survives across
  sessions; override the location with `STABBUR_MEMORY_DIR`.
- **`search`** (`stabbur-mcp-search`) — `search(query)` returns titled web results (title, URL,
  snippet). Zero-config via DuckDuckGo (no key); set `STABBUR_SEARCH_BRAVE_KEY` /
  `STABBUR_SEARCH_EXA_KEY` to use the Brave/Exa APIs. Pairs with `web` — search, then read the
  winner.
- **`weather-yr`** (`stabbur-mcp-weather-yr`) — `weather_forecast(place)` /
  `weather_forecast_at(lat, lon)` return current conditions + hourly + daily forecast from the
  free met.no (yr.no) API (place names geocoded via OpenStreetMap). No key; fixed endpoints, so
  no arbitrary-fetch surface.
- **`files`** (`stabbur-mcp-files`) — `list_files`, `read_file`, `search_files` under one configured
  root (`STABBUR_FILES_ROOT`, default the current directory). Every path is contained to the root
  (no `..` escapes); reads refuse binary/oversized files. Read-only unless `STABBUR_FILES_WRITABLE`.
- **`git`** (`stabbur-mcp-git`) — `git_status`, `git_log`, `git_diff`, `git_show`, `git_branches`,
  `git_ls_files`, `git_blame` for **read-only** inspection of one repository (`STABBUR_GIT_REPO_ROOT`,
  default the current directory). Each tool builds a fixed `git -C <root> …` argv (no arbitrary
  subcommand passthrough, no `fetch`/`clone`/`push`), path args are contained to the repo (no `..`
  escapes), and output is timeout- and size-capped. Writes are gated behind `STABBUR_GIT_ALLOW_WRITE`
  (off by default; no mutating tool ships today).
- **`exec`** (`stabbur-mcp-exec`) — `run_python(code, stdin)` runs a snippet in a locked-down Docker
  sandbox (no network, read-only filesystem, capped memory/CPU/pids, timeout) and returns its
  output — a calculator / scratchpad. Needs a running Docker daemon.
- **`http`** (`stabbur-mcp-http`) — `http_get(url, headers?)` / `http_head(url, headers?)` fetch an
  **allowlisted** URL (SSRF-guarded) and return its status, final URL, content-type, and body text
  (capped). Fail-closed: the allowlist is empty by default, so nothing is reachable until you set
  `STABBUR_MCP_HTTP_ALLOWLIST` (a host matches an entry exactly or as a subdomain). Private/loopback
  hosts are refused, the connection is pinned to the vetted IP (anti-DNS-rebinding), and redirects
  are re-vetted at every hop; `STABBUR_MCP_HTTP_ALLOW_PRIVATE=1` opts into internal hosts.

One heavier first-party server is **optional**:

- **`web`** (`stabbur-mcp-web`) — `read_url(url)` returns a page's main content as Markdown. It
  tries a cheap static HTTP GET first and only falls back to a **headless browser**
  (Playwright/Chromium) for JavaScript-rendered pages, so simple pages skip the browser.
  Because the browser is heavy, it's shipped as an extra: `make install-web` (or `uv sync
  --extra web` then `playwright install chromium`) — `sb mcp list` shows it with that hint
  even before it's installed. Once installed it advertises itself, so `sb mcp add web` wires
  it into a project. An SSRF guard refuses private/loopback hosts (the static and browser paths,
  and every browser subrequest); `STABBUR_WEB_ALLOW_PRIVATE=1` opts into internal hosts.


```json
{
  "mcpServers": {
    "datetime": { "command": "stabbur-mcp-datetime" }
  }
}
```

### Hand-editing the file

These files are yours to edit, and `sb mcp add` / `sb mcp remove` are careful with what they
find there: they change exactly the one entry you named and write the rest of the file back
byte for byte — a `$schema`, an `inputs` block, per-server fields stabbur has no opinion about
(`autoApprove`, `timeout`), your key order, and the entries for every other server.

Two things that follow from that:

- **Disabling a server.** A project can switch off a machine-global server by name, with either
  `"playwright": null` or `"playwright": { "disabled": true }`. Adding some *other* server
  afterwards leaves that marker alone, so a tool you deliberately turned off stays off.
- **Entries stabbur can't run yet.** A remote/HTTP server (`{ "type": "http", "url": "…" }`) is
  **skipped with a warning**, not an error — every other server in the file keeps working, and the
  entry itself is preserved on write, ready for when stabbur grows a remote transport. The same
  goes for an entry that's simply malformed: it names the culprit and moves on.

### Settings, without editing JSON

Several of these servers read environment variables that decide **what they can reach** —
`STABBUR_FILES_ROOT` is the only directory the assistant can browse, `STABBUR_MCP_HTTP_ALLOWLIST` the
only hosts it can fetch. Each server declares those variables, so the Tools panel in
`sb serve --ui` shows the value **actually in force** on the server's card (an unset
`STABBUR_FILES_ROOT` resolves to the directory `sb serve` was launched in — worth checking before
wondering why the assistant answered about the wrong project) and lets you change it there.

The edit is written to the machine-global `~/.config/stabbur/mcp.json` as an `env` entry on the
server, exactly as if you had typed it:

```json
{
  "mcpServers": {
    "files": { "command": "stabbur-mcp-files", "env": { "STABBUR_FILES_ROOT": "/Users/me/dev" } }
  }
}
```

Two caveats the panel states rather than hides: a server has to be **switched on** before it can be
configured (its settings live in that entry), and a server that is **already running** keeps the
environment it was started with — the change is saved but needs a `sb serve` restart. A server
switched on by a project's own `.mcp.json` is edited there; stabbur never rewrites a project file from
the web UI.

## DHIS2

The DHIS2 MCP comes in three sizes — **match it to the model's context + tool
ability** (a small model drowns in 300 tools; a big-context model can use them all):

| Server | Tools | Use with |
| --- | --- | --- |
| **`dhis2w-mcp`** | ~304 typed tools | big-context, tool-strong models (gpt-oss-20b, Qwen3-Coder-30B) |
| **`dhis2w-mcp-router`** | 2 meta-tools (`search_tools` / `call_tool`, lazy) | mid models — discovers tools on demand |
| **`dhis2w-mcp-bridge`** | 1 tool (`dhis2_cli`, shells out) | smaller models — one call, minimal schema |

Install the server(s) as CLI tools (they're `uv` tools), then attach the one that
fits your model:

```bash
uv tool install dhis2w-mcp            # full — for a big-context model
uv tool install dhis2w-mcp-bridge     # bridge — for a smaller model
```

Then add it to a project's `.mcp.json` (`sb mcp add dhis2`, or select it in
`sb init`). For a big model:

```json
{
  "mcpServers": {
    "dhis2": { "command": "dhis2w-mcp" }
  }
}
```

For a smaller model, swap in the bridge — `"command": "dhis2w-mcp-bridge"`.
`sb serve --ui` (or `sb chat`) in that directory then binds the model with the
DHIS2 tools available.

### Selecting the target server (profiles)

The DHIS2 servers pick their target from a **profile** in
`~/.config/dhis2/profiles.toml` (base URL + credentials, kept on the machine), read
from the `DHIS2_PROFILE` environment variable. The `mcpServers` entry has a first-class
`env` object — put the profile there; `DHIS2_MCP_READONLY=1` keeps it query-only:

```json
{
  "mcpServers": {
    "dhis2": {
      "command": "dhis2w-mcp-bridge",
      "env": { "DHIS2_PROFILE": "play42", "DHIS2_MCP_READONLY": "1" }
    }
  }
}
```

Swap the profile name (e.g. `play43`) to retarget. In a non-uv project the command is
`uvx` with `"args": ["dhis2w-mcp-bridge"]` (no persistent install). For a full end-to-end
walkthrough — scaffold, local model copy, wire the bridge, and confirm the model calls it —
see the [DHIS2 assistant worked example](dhis2-project.md).
