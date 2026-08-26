# Changelog

All notable changes to heim are recorded here. heim is proprietary/source-available
(see [`LICENSE`](LICENSE)); versions follow semantic versioning.

## 0.5.0 — 2026-08-26

A UI pass, and the honesty work that came with it. heim's chat surface stopped looking like a
template: chrome that carries the app's identity, a settings dialog instead of a sparse page,
attachments that say what they take, and — underneath — conventions written down and enforced,
so the next change is consistent without anyone re-deriving them.

Many of these are the same defect wearing different clothes: a control that stated something it
had not checked. A model switch that did not switch, a "ready" that was not ready, a panel
claiming no MCP servers existed while twelve shipped, a status bar asserting "Local runtime"
against a remote host, a filter that verified a truncated file as intact. Each is listed where
it belongs below.

### Attachments

- **PDFs.** Text extraction via pdf.js — bundled locally, so an offline heim still works — or
  rendered page images with "Parse PDF as image", falling back to text when the model cannot see,
  and *forward* to images when a scan has no text layer. Dropping a PDF previously did nothing at
  all: no attachment, no hint, no error.
- **No file is dropped in silence.** Every file either becomes an attachment or produces a named
  reason. Oversized images are downscaled before they are sent.
- **The paperclip says what this model can take** — images, audio, text and code, PDF — with a
  kind this model cannot read greyed rather than absent, since an absent control teaches nothing
  and the point is that a vision model and a text-only one differ.

### Chat history

- **Conversations moved to IndexedDB.** Everything, images included, lived in one ~5MB
  localStorage key, so a fallback silently re-saved transcripts with the attachments thrown away.
  That is deleted: nothing discards data quietly any more. Attachments are Blobs at rest;
  migration verifies every id reads back before removing the old key, and leaves content it
  cannot parse alone.
- **Storage persistence is requested**, and a refusal is reported in Settings rather than assumed
  — otherwise the move trades a 5MB cliff for a bucket the browser may reclaim.
- **The model names the conversation.** A chat was named by slicing 40 characters off its first
  message, once, forever — so an image-only message was "Attachment" permanently. A title you
  type is never overwritten.

### Tools and MCP

- **The twelve bundled servers are visible and toggleable**, with `datetime` seeded on a fresh
  install. The Tools panel previously said "No MCP servers configured" while heim shipped twelve.
  Enabling applies live; disabling cannot detach a running subprocess, so it says so.
- **A server's settings are editable in the panel** instead of by hand in `mcp.json` — including
  the workspace root that decides what `files` can reach, which was previously unknowable.
- **A new chat starts on a baseline**, not on whatever you last switched on. Switching a server
  on is machine-wide and persistent; per-chat selection is now an allow-list.

### System health

- Leads with the two facts anyone opens it for — **which backend, which model** — and folds the
  rest into groups that fly out rather than shoving the menu around. The backend was not reported
  at all, so a dead upstream looked like heim being broken.
- Four failure modes are told apart rather than collapsed into "unreachable", and the probe
  cannot stall the menu.

### Interface

- **Settings is a dialog**, not a primary view competing with Chat, Library and Voice.
- **A status bar and a single top bar** carrying the surface's title; the model badge is gone
  from the header because the composer's selector was a strict superset of it.
- **The sidebar is a fixed 240px.** It was 18% of the viewport, so it grew with the display —
  345px at 1920, 461px at 2560 — while the type stayed put, which is what read as a zoomed-out
  page.
- **Narrow windows get a sheet** instead of a rail squeezed to 61px; no viewport renders a rail
  under 320px, which covers tablets and a half-screen desktop window alike.
- Collapsing a rail no longer re-flows its contents on every frame.

### Colour and conventions

- **Colours follow the theme.** Roughly 35 hardcoded values ignored it entirely, and code blocks
  were pinned to a dark syntax stylesheet in every light palette. `--good-ink` / `--warning-ink`
  exist because the fill colours measure 3.2-4.0:1 as small text, under AA.
- **`docs/ui-conventions.md`**, and a gate that enforces it: oxlint, a class check for what a JS
  linter structurally cannot see, vitest, and the frontend typecheck — which `make check` did not
  run, so it passed on code CI then rejected.
- The named colour set is **Theme** and light/dark is **Mode**, matching the UI and
  `dhis2w-fhir-serve`. Saved appearance choices reset once on upgrade.

### Sampling, serving, docs

- **top_k, min_p and repeat_penalty are reachable** from the UI with sliders and plain
  descriptions. `sampling.py` and the agent loop already supported them; only the web request
  did not carry them.
- **`heim library verify` checks what it always claimed to.** It compared nothing, so a truncated
  pull verified clean; `sync --repair` re-pulls what fails.
- The built SPA is served with a cache policy, so an upgrade is not invisible behind a stale
  `index.html`; an unconfigured library answers with its hint instead of a bare 500.
- **`docs/guides/api.md`** — calling heim from your own code, including that a reasoning model
  with a small `max_tokens` returns a valid response containing an empty string.

### Terminal

- The TUI's model picker could not select a second format of a name — with a model kept as both
  GGUF and MLX, choosing the second did nothing at all. Five further fixes alongside it.

## 0.4.0 — 2026-08-26

Headline: **heim runs against models it doesn't host**. `heim serve --upstream <url>` fronts a
remote OpenAI `/v1` — a llama-server in router mode on another box, LM Studio, another heim — so
the agent loop, MCP tools, confirm gate, and UI stay local while the weights live elsewhere. The
chat surfaces gained a per-chat settings panel, a command palette, reasoning-effort control, and
live generation stats; the voice set was cut back to what actually works.

### Remote model hosts

- **`heim serve --upstream <url>`** fronts a remote OpenAI-compatible `/v1` instead of spawning
  local runtimes. `UpstreamManager` mirrors `ServerManager`'s read surface, so the serving routers
  hold either; "loading" a model selects one of the remote's ids.
- **`heim chat --server <url>`** is the CLI/TUI counterpart, for the one-shot `-p` path and the
  interactive TUI. Both prefer the remote's *currently loaded* model, so attaching never evicts
  what is running. A loopback `heim serve` locked to the model is auto-detected for `-p`.
- **`--no-server`** loads the model locally for one run, ignoring a configured chat server and
  skipping the auto-attach. Previously a configured server applied to every run with no way back:
  the documented escape hatch (`--server ""`) never worked, since an empty string fell through to
  the configured URL.
- **A model switch now actually loads the model on the remote.** Selecting only recorded a local
  choice, so heim reported the new model as ready while the remote still served the old one and
  the next message silently paid a full load. A router-mode llama-server has no load endpoint and
  autoloads on the first request naming a model, so the switch issues that request itself —
  skipped when the model is already resident, and a failed load keeps the previous selection.
- Upstream health probes are paced (keep-alive client, TTL + grace hysteresis) and skipped while a
  generation streams, so a busy remote answering `/v1/models` slowly no longer flaps the UI to
  disconnected.

### Chat surfaces

- **Per-chat settings panel** in the web UI, replacing the split global Settings page: parameters
  and tools per conversation, each setting with a real default and a reset.
- **Cmd/Ctrl+K command palette** and named themes, both ported from `dhis2w-fhir-serve`, plus that
  project's radius scale.
- **Reasoning-effort control** across the web UI, TUI, and agent loop, with thinking blocks
  collapsed by default.
- **Live generation stats** — tokens, elapsed, tokens/sec — reported from the runtime's own decode
  rate rather than an average over prompt processing, which under-reported by roughly a third.
- **TUI**: `/model` opens an arrow-key picker (and switches remote models), `/system` shows,
  replaces, or drops the session system prompt while keeping history, and `heim chat -p --save`
  writes the exchange to Markdown through the same renderer as the TUI's `/export`.

### Voice

- **Breaking: Dia and the llama-tts engine are gone**, along with Qwen3-TTS, OuteTTS, Chatterbox,
  CSM, and Soprano. What remains is what held up in practice — Kokoro for chat, mlx-audio on
  Apple. Registry entries mark unsupported models so they are rejected at the synthesis choke
  point rather than failing mid-generation.
- **Speed control** (0.5x–2x) and a voice picker docked in the composer and on the Listen button.

### Library

- **`heim library sync --repair`** re-pulls models that fail verification, for a drive that came
  back with a half-finished copy; `--deep` extends that to re-hashing Ollama blobs.
- **`heim library verify` now checks what it always claimed to.** For GGUF/MLX/safetensors models
  it only checked that weights existed and were non-empty, so a truncated pull verified clean; it
  now compares the size and file count recorded in `.heim/metadata.json`.

### Serve

- **Fixed default port 2222.** A collision is reported rather than silently moved to another port,
  and `heim config set port/host` pins the address per machine.
- Restarting immediately after stopping no longer reports the port as in use: the pre-flight now
  probes the way uvicorn binds (`SO_REUSEADDR`), so the `TIME_WAIT` sockets a just-stopped server
  leaves behind stop reading as a live collision. A running server is still detected.

### Assistants and the extension

- **Multi-target assistant registry** — several DHIS2 instances in one project, matching the tab
  to the right instance, with a web target picker and lazy per-target MCP bridge spawn.
- **The extension acts as the logged-in user by default.**
- **`heim ext-dev`** launches an interactive extension test-drive.

### Fixes

- MLX runtimes installed into heim's own environment are found without a global install.
- The `/model` picker no longer crashes when the library holds duplicate model ids.
- A high-effort review of the batch produced 11 findings, all fixed.

### Docs

- **`WEBMCP.md`** — page actions and the agentic web: the three distinct things called WebMCP,
  their maturity, and why the verdict is watch rather than build. DHIS2's `POST /api/files/script`
  was checked and is legacy-page only, so it cannot reach app-platform SPAs.
- `ROADMAP.md` carries open threads only; shipped work lives in git history and here.

### Packaging

- `heim.__version__` now reads from package metadata instead of a hand-maintained constant, which
  had drifted to `0.1.0`.

## 0.3.0 — 2026-07-13

First tagged release, and a rename. The project is now **heim** — German / Old Norse for **"home"**:
your models, your tools, and your data live at home, on your own box (it was developed as `kodo`).
Headline: **DHIS2 writes, gated** — the assistant can now mutate a DHIS2 instance, safely, behind
per-action human confirmation. heim builds as a single self-contained distribution.

### DHIS2 writes, gated (the north-star capability)

- **Per-action write-confirmation gate** across every interactive surface (web UI, Chrome side
  panel, Textual TUI). A write-enabled assistant prompts you to Approve/Deny each gated tool call
  before it runs; a declined call returns `error: user declined this action` and the model
  continues. The gate is generic (driven by MCP `readOnlyHint`, no DHIS2 logic in core) and
  fail-safe (unannotated tools require confirmation; a non-interactive `heim chat -p` denies gated
  writes unless `--allow-writes`; free-play / read-only assistants are never gated). A new
  `POST /api/chat/confirm` resolves each prompt (300s auto-deny, `HEIM_CONFIRM_TIMEOUT`).
- **Write-enabling "Use my login" bind.** The Chrome side panel mints a read-write Personal Access
  Token when you enable writes, and the binding records read-vs-write scope. Session-cookie writes
  are supported with an optional `X-XSRF-TOKEN` double-submit (via dhis2w >= 1.1.0).
- **Read/write-aware gating on the typed DHIS2 server.** With `dhis2w-mcp` >= 1.3.0 (which now
  stamps `readOnlyHint` per operation), a write assistant confirms only writes; reads pass ungated.
  The default single-tool bridge is one dynamic tool, so on it reads still prompt (inherent).

### Honesty about write reliability

- The write benchmark now **verifies real DHIS2 state** (an object was actually created, then
  actually absent at the end) with a real `HEIM_` residue sweep, replacing a weak
  "called-the-tool + emitted-a-token" proxy. Under it the best local model (gemma-4-12B) completes
  **0 of 7** write lifecycles — it reliably creates but does not reliably delete. Reads stay
  **12/12**. The gate makes writes *safe* (a human approves and can catch an incomplete cleanup),
  not *autonomous*.
- The extension's write path is **verified end-to-end live**: a create driven through the panel,
  approved at the gate, persists and read-back-verifies against a real instance.

### Extension

- MV3 Chrome side panel (WXT + shared SPA), generic `heim` and `heim for DHIS2` flavors. Documented
  write flow + generic-site walkthrough, with screenshots.

### Packaging

- **heim is now a single self-contained package.** The bundled first-party MCP tool servers
  (`heim-mcp-*`) and the benchmark harness are vendored into the `heim` distribution
  (`src/heim/mcp_servers/*`, `src/heim/benchmark`) and registered as `heim`'s own console scripts +
  `heim.plugins` entry points — so `heim` has no `heim-*` runtime dependencies and installs from a
  single wheel. The heavy `web` (Playwright) and `benchmark` deps remain opt-in extras.

### CI / release

- Added this changelog and GitHub Releases per tag. CI (`make check`) and docs auto-deploy were
  already in place. heim is proprietary/source-available and is not published to PyPI.
