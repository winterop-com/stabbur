# Changelog

All notable changes to heim are recorded here. heim is proprietary/source-available
(see [`LICENSE`](LICENSE)); versions follow semantic versioning.

## 0.3.0 — 2026-07-13

First tagged release, and a rename. The project is now **heim** — German / Old Norse for **"home"**:
your models, your tools, and your data live at home, on your own box. It was developed as `kodo`;
`kodo` is unavailable on PyPI, so the first public release ships as `heim`. Headline: **DHIS2
writes, gated** — the assistant can now mutate a DHIS2 instance, safely, behind per-action human
confirmation. heim is a single self-contained distribution.

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

- Added a PyPI publish workflow (tag `v*` → build → publish via Trusted Publishing/OIDC) and this
  changelog. CI (`make check`) and docs auto-deploy were already in place.
