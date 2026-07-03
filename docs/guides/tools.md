# Tools (MCP)

kodo is an **MCP client**: a chat model can call tools that kodo runs as MCP
servers (the agent loop executes the call and feeds the result back). Tools come
from two places:

- **Installed plugins** — MCP servers advertised by installed `kodo-mcp-*` packages
  (`datetime`, `utils`). `kodo mcp list` shows them. (The `benchmark` package is a
  dev/benchmarking tool and does *not* advertise itself as an assistant tool.)
- **A project's `[[mcp]]`** — any MCP server command, listed in `kodo.toml`. This is
  how you attach an external server like DHIS2.

Pick tools interactively with `kodo project init` (the wizard multi-selects from the
installed plugins), or add `[[mcp]]` blocks to `kodo.toml` by hand:

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
DHIS2 tools available. Configure the DHIS2 connection (base URL, credentials/profile)
per the `dhis2w-mcp` package's own docs — kodo just spawns the command.
