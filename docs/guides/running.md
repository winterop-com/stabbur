# Running & chatting

All runtimes expose an **OpenAI-compatible** API at `http://<host>:<port>/v1`, so
any OpenAI client attaches the same way.

| Format | Server | Notes |
| ------ | ------ | ----- |
| GGUF   | llama.cpp `llama-server` | cross-platform |
| MLX    | `mlx_lm.server`          | Apple Silicon only |

`stabbur chat` talks to that server's `/v1` behind a full-screen TUI; `stabbur serve`
runs the web server and proxies `/v1` for the browser UI (and external clients).
stabbur spawns and manages the runtime for you either way.

## Serve a model (OpenAI `/v1`)

```bash
stabbur serve --ui                       # browse + chat in the browser (loads models on demand)
stabbur serve --model <name>             # lock the server to one model; stable /v1 endpoint
stabbur serve --model <name> --port 9000 # pin the port
```

`serve --model` boots straight into one model and exposes a stable
OpenAI-compatible `/v1` (the Chrome-extension backend); `serve --ui` lets you
switch models from the browser. Point any OpenAI client at the printed URL.

## Chat

```bash
stabbur chat <name>                      # clean streaming REPL (/exit to quit)
stabbur chat <name> -p "Summarize MoE"   # one-shot: prints only the answer (pipeable)
stabbur chat <name> -p "..." -n 256      # cap generated tokens
```

The `-p/--prompt` one-shot mirrors `claude -p` — clean stdout for scripting:

```bash
stabbur chat gemma-4-12B-it-QAT-GGUF -p "Say hi" 2>/dev/null | tee out.txt
```

The interactive REPL is a full Textual TUI. Press **Ctrl+P** for the command palette or type
`/help`: switch the running model without leaving chat (`/model <name>`, or "Switch model" in the
palette — the conversation carries over), toggle/reconnect MCP servers (`/mcp`), adjust sampling
live (`/set temperature 0.7`), `/export` the transcript, and more.

## Picking a model name

Use the full `<publisher>/<repo>` name or just the final part; add `--format` to
disambiguate if the same model exists in multiple formats. If a name isn't in the
library but is in a source store, the error tells you the `stabbur library pull` to run.
