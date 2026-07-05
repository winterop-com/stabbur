# Tools (MCP)

kodo is an **MCP client**: a chat model can call tools that kodo runs as MCP
servers (the agent loop executes the call and feeds the result back). Tools come
from two places:

- **Installed plugins** — MCP servers advertised by installed `kodo-mcp-*` packages
  (`datetime`, `utils`, `memory`). (The `benchmark` package is a dev/benchmarking tool and
  does *not* advertise itself as an assistant tool.)
- **A project's `[[mcp]]`** — any MCP server command, listed in `kodo.toml`. This is
  how you attach an external server like DHIS2.

## Browse and add — `kodo mcp`

`kodo mcp list` shows two groups: **installed plugins first** (first-party
`kodo-mcp-*` packages that kodo controls — the recommended set, no external runtime),
then an **external catalog** of third-party servers (DHIS2, `fetch`, `git`, `sqlite`,
`filesystem`, …) as a fallback for tools kodo doesn't ship yet. A `✓` marks a server
already in the current directory's `kodo.toml`.

```bash
kodo mcp list          # installed plugins, then external catalog (✓ = already in kodo.toml)
kodo mcp add utils     # a first-party plugin
kodo mcp add dhis2     # an external server — prints a "setup:" hint when it needs config
```

`kodo mcp add` resolves the name against installed plugins first, then the external
catalog, and appends a `[[mcp]]` block. External commands may carry a placeholder (a
path, a DHIS2 profile) — the `setup:` hint says what to edit. You can also pick plugin
tools interactively in the `kodo project init` wizard, or write `[[mcp]]` blocks by hand:

### First-party servers

kodo bundles pure-stdlib/light plugins — always available:

- **`datetime`**, **`utils`** — date/time/calendar and text/encoding/hashing/math helpers.
- **`memory`** (`kodo-mcp-memory`) — persistent notes / key-value memory the assistant reads and
  writes (`memory_set/get/list/search/delete`). Saved as a JSON file in the library
  (`<KODO_LIBRARY_ROOT>/.kodo/memory/notes.json`), so it travels with the drive and survives across
  sessions; override the location with `KODO_MEMORY_DIR`.
- **`search`** (`kodo-mcp-search`) — `search(query)` returns titled web results (title, URL,
  snippet). Zero-config via DuckDuckGo (no key); set `KODO_SEARCH_BRAVE_KEY` /
  `KODO_SEARCH_EXA_KEY` to use the Brave/Exa APIs. Pairs with `web` — search, then read the
  winner.

One heavier first-party server is **optional**:

- **`web`** (`kodo-mcp-web`) — `read_url(url)` returns a page's main content as Markdown. It
  tries a cheap static HTTP GET first and only falls back to a **headless browser**
  (Playwright/Chromium) for JavaScript-rendered pages, so simple pages skip the browser.
  Because the browser is heavy, it's shipped as an extra: `make install-web` (or `uv sync
  --extra web` then `playwright install chromium`) — `kodo mcp list` shows it with that hint
  even before it's installed. Once installed it advertises itself, so `kodo mcp add web` wires
  it into a project. An SSRF guard refuses private/loopback hosts (the static and browser paths,
  and every browser subrequest); `KODO_WEB_ALLOW_PRIVATE=1` opts into internal hosts.


```toml
[[mcp]]
name = "datetime"
command = "kodo-mcp-datetime"
```

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

Then add it to a project (or select it in `kodo project init`). For a big model:

```toml
# kodo.toml
[project]
model = "unsloth/gpt-oss-20b-GGUF"

[[mcp]]
name = "dhis2"
command = "dhis2w-mcp"
```

For a smaller model, swap in the bridge:

```toml
[[mcp]]
name = "dhis2"
command = "dhis2w-mcp-bridge"
```

`kodo serve --ui` (or `kodo chat`) in that directory then binds the model with the
DHIS2 tools available.

### Selecting the target server (profiles)

The DHIS2 servers pick their target from a **profile** in
`~/.config/dhis2/profiles.toml` (base URL + credentials, kept on the machine), read
from the `DHIS2_PROFILE` environment variable. Because `[[mcp]].command` has no `env`
field, carry the profile with an `env` prefix (kodo splits `command` like a shell
line, so this just works). `DHIS2_MCP_READONLY=1` keeps it query-only:

```toml
[[mcp]]
name = "dhis2"
command = "dhis2w-mcp-bridge"
env = { DHIS2_PROFILE = "play42", DHIS2_MCP_READONLY = "1" }
```

Per-server settings go in the `env` table; swap the profile name (e.g. `play43`) to
retarget. In a non-uv project the command is `uvx dhis2w-mcp-bridge` (no persistent install);
the older single-string form (`command = "env DHIS2_PROFILE=play42 … uvx dhis2w-mcp-bridge"`)
still works. For a full end-to-end walkthrough — scaffold,
local model copy, wire the bridge, and confirm the model calls it — see the
[DHIS2 assistant worked example](dhis2-project.md).
