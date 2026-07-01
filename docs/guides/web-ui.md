# Web UI (`serve --ui`)

`kodo serve` runs the FastAPI app: a browse API, an OpenAI `/v1` proxy to the
loaded model, and — with `--ui` — the browser single-page app (a React/Vite +
Tailwind chat UI with a model picker).

**Build the UI once** (it's not committed — only its source is):

```bash
make frontend                         # npm install + build → frontend/dist
```

Then serve it:

```bash
kodo serve --ui                       # browse + chat in the browser, switch models
kodo serve --ui --port 8000           # pin the port for a stable URL/bookmark
kodo serve --ui --model <name>        # locked to one model (extension backend)
```

For UI development, `make frontend-dev` runs Vite with hot reload (it proxies
`/api` + `/v1` to `KODO_DEV_API` or `:8000`, so run `kodo serve --port 8000`
alongside).

By default `serve` **auto-picks a free port** and prints the URL on startup, so it
never collides with your other services. Pass `--port` (or set `KODO_PORT` /
`port` in `kodo.toml`) to pin a stable address — useful for a browser bookmark or
the Chrome-extension origin. `--host` overrides the bind address.

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
`mlx_lm.server` on an internal port (auto-picked free by default; pin it with
`runtime_port` / `--runtime-port`) and proxies to it.

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
