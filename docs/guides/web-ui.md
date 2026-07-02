# Web UI (`serve --ui`)

`kodo serve` runs the FastAPI app: a browse API, an OpenAI `/v1` proxy to the
loaded model, and — with `--ui` — the browser single-page app (a React/Vite +
Tailwind chat UI with a model picker).

![kodo web UI](../assets/web-ui.png)

## Features

- **Model picker** — grouped by format, with per-model **capability icons**
  (tools · vision · audio), a rich hover tooltip (format/size/context/caps),
  **filter chips** to narrow by capability, and an **eject** action to free memory.
- **Tools** — a per-server fly-out menu with a master switch, per-server
  toggle-all, and per-tool switches (scales from 3 tools to hundreds).
- **Multimodal input** — for vision/audio models, attach **images** and **audio**
  via drag-drop, paste, or the picker, or **record from the mic** (auto-stops on
  silence). Images open fullscreen on click; audio plays inline. kodo nudges you
  toward an audio-specialist model when one fits better.
- **Text / document attachments** — drop, paste, or pick **text/code files**
  (`.md`, `.py`, `.json`, `.csv`, …) with **any** model: their contents are inlined
  into the prompt as fenced blocks (the message shows a filename chip, not the raw
  text), so a plain chat model can read a file as context.
- **Listen (text-to-speech)** — a speaker button on each reply reads it aloud
  (Markdown/code is stripped first so only the prose is spoken); the settings rail
  picks the **voice**. With the optional Kokoro extra (`make install-tts`) that's a
  picker of **54 built-in voices** across 9 languages (grouped by language); otherwise
  it falls back to `llama-tts`/OuteTTS.
- **Mermaid diagrams** — ```` ```mermaid ```` fenced blocks render as live
  diagrams (theme-aware, lazy-loaded), with a source/diagram toggle and copy;
  invalid syntax falls back to the source.
- **Export** — a download menu in the top bar exports the open conversation as
  **Markdown** (source-form: roles, code fences, reasoning, tool activity, and a
  model + params header) or **PDF** (a styled, self-contained document opened in a
  print window for "Save as PDF"; mermaid diagrams are rendered to inline SVG).
  Both run client-side from conversation state.
- **Settings rail** — system prompt (authoritative; blank = none), sampling
  (with model-recommended defaults shown), and **context length** (presets +
  custom; reloads the model). Settings are **per conversation**, so each chat
  starts fresh.
- **System health** — a status dot opening the same checks as `kodo doctor`.

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
| `GET /api/status` | runtime state (`stopped`/`loading`/`ready`), model, n_ctx, error |
| `GET /api/library` | runnable models + capabilities (vision/audio/tools/context) |
| `GET /api/model?name=` | one model's card + metadata + recommended sampling |
| `POST /api/load/{name}` | load/switch a model (`?n_ctx=` sets context; locked → 409) |
| `POST /api/unload` | eject the loaded model (frees memory) |
| `POST /api/chat` | server-side agent loop (tools + multimodal) → typed SSE |
| `GET /api/tools` | attached MCP tools (namespaced `<server>__<tool>`) |
| `GET /api/doctor` | system-health report (mirrors `kodo doctor`) |
| `GET /api/voices`, `POST /api/speak` | list voices (Kokoro + OuteTTS); synthesize text → WAV |
| `POST /v1/{path}` | stream-proxied to the loaded runtime's `/v1` |
| `GET /health`, `GET /docs` | health check, OpenAPI docs |

So the SPA only ever talks to `serve`'s origin; `serve` starts `llama-server` /
`mlx_lm.server` on an internal port (auto-picked free by default; pin it with
`runtime_port` / `--runtime-port`) and proxies to it.

## Locked single-model mode

```bash
kodo serve --ui --model <name>        # or: make run MODEL=<name>
```

Locks the server to one model: no switching and a stable `/v1`. This is the
intended backend for the [Chrome extension](../roadmap.md) — the extension's side
panel points at this endpoint. Set `cors_origins` to the extension's origin so it
can call across origins (see below).

## Cross-origin access

By default the server is **same-origin only**: the web UI is served by this same
app so it needs no CORS, and mutating `/api` / `/v1` calls that a browser marks
**cross-site** (via `Sec-Fetch-Site`) are rejected with `403`. This stops a random
website you visit from driving your local models or MCP tools from the browser
(even a no-preflight "simple" request can execute server-side otherwise). Non-browser
clients (curl, the CLI) and same-origin requests are unaffected.

To allow a real cross-origin caller (the Chrome extension, or a separate dev
server), list its origin in `cors_origins` (`kodo.toml` or `KODO_CORS_ORIGINS`) —
that both enables CORS for it and exempts it from the cross-site guard. `["*"]`
re-opens it to everything; avoid that outside throwaway local testing.
