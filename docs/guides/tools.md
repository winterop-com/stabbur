# Tools (MCP)

kodo is an **MCP client**: a chat model can call tools that kodo runs as MCP
servers (the agent loop executes the call and feeds the result back). Tools come
from two places:

- **Installed plugins** — MCP servers advertised by installed `kodo-mcp-*` packages
  (`datetime`, `utils`). (The `benchmark` package is a dev/benchmarking tool and does
  *not* advertise itself as an assistant tool.)
- **A project's `[[mcp]]`** — any MCP server command, listed in `kodo.toml`. This is
  how you attach an external server like DHIS2.

## Browse and add — `kodo mcp`

`kodo mcp list` shows a **curated catalog** of ready-to-run servers (DHIS2, `fetch`,
`git`, `sqlite`, `filesystem`, …) plus any installed plugins. A `✓` marks a
server already in the current directory's `kodo.toml`.

```bash
kodo mcp list          # curated catalog + installed plugins (✓ = already in kodo.toml)
kodo mcp add fetch     # append its [[mcp]] block to ./kodo.toml (idempotent)
kodo mcp add dhis2     # prints a "setup:" hint when the server needs config
```

`kodo mcp add` resolves the name against the catalog first, then installed plugins,
and appends a `[[mcp]]` block. Curated commands may carry a placeholder (a path, a
DHIS2 profile) — the `setup:` hint says what to edit. You can also pick plugin tools
interactively in the `kodo project init` wizard, or write `[[mcp]]` blocks by hand:

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
command = "env DHIS2_PROFILE=play42 DHIS2_MCP_READONLY=1 uvx dhis2w-mcp-bridge"
```

`uvx dhis2w-mcp-bridge` runs the bridge without a persistent install; swap the
profile name (e.g. `play43`) to retarget. For a full end-to-end walkthrough — scaffold,
local model copy, wire the bridge, and confirm the model calls it — see the
[DHIS2 assistant worked example](dhis2-project.md).
