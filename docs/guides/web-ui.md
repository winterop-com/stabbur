# Web UI (`serve --ui`)

`kodo serve` runs the FastAPI app: a browse API, an OpenAI `/v1` proxy to the
loaded model, and — with `--ui` — the browser single-page app.

```bash
kodo serve --ui                       # browse + chat in the browser, switch models
make run                             # same, via the Makefile
make run MODEL=<name>                # locked to one model (see below)
```

The SPA (built to `frontend/dist`) is served via FastAPI's first-party
`app.frontend()` with `fallback="index.html"`, so client-side routes resolve to
the SPA while API path operations still match first. If it isn't built yet, the
API still runs and `serve --ui` says so.

## Single-origin proxy

The app keeps one stable origin while swapping the underlying runtime:

| Endpoint | Purpose |
| -------- | ------- |
| `GET /api/status` | `{state, model, locked}` — `stopped` / `loading` / `ready` |
| `POST /api/load/{name}` | load or switch to a model (rejected in locked mode) |
| `POST /v1/{path}` | stream-proxied to the loaded runtime's `/v1` |
| `GET /health`, `GET /docs` | health check, OpenAPI docs |

So the SPA only ever talks to `serve`'s origin; `serve` starts `llama-server` /
`mlx_lm.server` on an internal port (`runtime_port`, default 8090) and proxies to
it.

## Locked single-model mode

```bash
kodo serve --ui --model <name>        # or: make run MODEL=<name>
```

Locks the server to one model: no switching, a stable `/v1`, and CORS configured
for cross-origin callers. This is the intended backend for the
[Chrome extension](../roadmap.md) — the extension's side panel points at this
endpoint.

`cors_origins` (default `["*"]`, fine for localhost) controls allowed origins —
set it to the extension origin when locking down.
