# Roadmap

## North-star

A **local, self-hosted DHIS2 assistant** — your own model + DHIS2 tools in a
browser side panel:

```
Chrome extension (side panel, shadcn chat)
  → local-llm (serve --ui --model X): model + MCP client + agent loop
      → a DHIS2 MCP server (~/dev/local/dhis2w-utils) → DHIS2
```

The DHIS2 MCP side already exists in `dhis2w-utils`, with servers built for small
local models:

- **`dhis2w-mcp-bridge`** — one tool (`dhis2_cli`) shelling out to `d2w`, with
  progressive `--help` discovery; fits an 8k-context model. The default target.
- **`dhis2w-mcp-router`** — two meta-tools (`search_tools`/`call_tool`), lazy
  typed discovery, a single guarded chokepoint with a **read-only mode** that
  gates DHIS2 writes.
- **`dhis2w-mcp`** — the full ~304 typed tools (for big-context hosts).
- `dhis2w-browser` — Playwright DHIS2 automation (for later "act on the page").

## Phases

1. **Phase 1 — local-llm + web chat UI** (in progress). Library, pull/run/chat,
   `serve --ui` + locked mode, the `/v1` proxy, and **generic tool/MCP support**
   (agent loop + MCP client pointable at any MCP server; tool activity rendered
   in the chat).
2. **Phase 2 — DHIS2 + Chrome extension.** Point the MCP client at
   `dhis2w-mcp-bridge`/`-router`; ship the chat UI as the side-panel extension
   against the locked `/v1`.
3. **Later** — extension page-context, then page-actions via `dhis2w-browser`.

## Curated starter models (`llm init`)

A fresh clone has an empty library and no obvious starting point. `llm init` will
offer a small **curated set of 2–3 models** to try out — kept deliberately tiny,
e.g. a compact GGUF for any machine, an MLX build for Apple Silicon, and a
tool-capable model for the agent/MCP path — and pull the chosen ones into the
library.

- The curated list lives in-repo (versioned), so "what's worth trying" travels
  with the project and stays current.
- `llm init` is the zero-to-chatting on-ramp: clone → `llm init` → `llm run`.
- Just 2–3 entries, clearly labelled by use case and footprint, quick to pull.

## One SPA, many surfaces

The chat UI is built once and wrapped:

- **Web** — `serve --ui` serves `frontend/dist`.
- **Chrome extension** — MV3 side panel loads the same bundle (locked `/v1`).
- **Desktop** — Tauri + Electron wrappers, following maneki's
  `desktop/{tauri,electron,react}` pattern (parallel wrappers, one shared SPA);
  ideally the desktop app also launches `local-llm serve` for one-click use.

## UI stack

Vite + React + Tailwind v4 + **shadcn/ui**, using shadcn's official chat
components (`MessageScroller`, `Message`, `Bubble`, `Attachment`, `Marker`) with a
hand-rolled OpenAI SSE loop — the backends emit raw OpenAI SSE, so we avoid the
Vercel AI SDK stream-format mismatch.
