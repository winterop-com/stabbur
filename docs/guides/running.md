# Running & chatting

All runtimes expose an **OpenAI-compatible** API at `http://<host>:<port>/v1`, so
any OpenAI client attaches the same way.

| Format | Server | Notes |
| ------ | ------ | ----- |
| GGUF   | llama.cpp `llama-server` | cross-platform |
| MLX    | `mlx_lm.server`          | Apple Silicon only |

`kodo chat` talks to that server's `/v1` behind a full-screen TUI; `kodo serve`
runs the web server and proxies `/v1` for the browser UI (and external clients).
kodo spawns and manages the runtime for you either way.

## Serve a model (OpenAI `/v1`)

```bash
kodo serve --ui                       # browse + chat in the browser (loads models on demand)
kodo serve --model <name>             # lock the server to one model; stable /v1 endpoint
kodo serve --model <name> --port 9000 # pin the port
```

`serve --model` boots straight into one model and exposes a stable
OpenAI-compatible `/v1` (the Chrome-extension backend); `serve --ui` lets you
switch models from the browser. Point any OpenAI client at the printed URL.

## Chat

```bash
kodo chat <name>                      # clean streaming REPL (/exit to quit)
kodo chat <name> -p "Summarize MoE"   # one-shot: prints only the answer (pipeable)
kodo chat <name> -p "..." -n 256      # cap generated tokens
```

The `-p/--prompt` one-shot mirrors `claude -p` — clean stdout for scripting:

```bash
kodo chat gemma-4-12B-it-QAT-GGUF -p "Say hi" 2>/dev/null | tee out.txt
```

## Picking a model name

Use the full `<publisher>/<repo>` name or just the final part; add `--format` to
disambiguate if the same model exists in multiple formats. If a name isn't in the
library but is in a source store, the error tells you the `kodo library pull` to run.
