# stabbur roadmap

Forward-looking plans and ideas. Kept out of `CLAUDE.md` so it doesn't load into
every session's context. `CLAUDE.md` holds the durable project rules, architecture,
and conventions; this file holds what's **next**. Completed work is removed (git
history + `docs/` are the record) — this file is only open threads.

## Browser extension follow-ups

Design + status: **[`CHROME.md`](CHROME.md)**. Decided direction: **the assistant acts as
whoever is logged into the tab** — the "Use my login" bind becomes the default path (consent
once per instance, mint a PAT in the tab's own session, reuse until expiry/revoke); the
shared/pre-provisioned profile survives only as the no-browser-context fallback (CLI, TUI,
bench, remote stabbur). Work items, in build order:

- **Bind UX: a clear "sign in first" state** (first build item — its no-session detection is
  also the building block for the auto-offer below). With no live session, the in-tab PAT mint
  fails with a bare `status 0` (the POST redirects to the login page and the fetch comes back
  opaque), which `classifyMint` cannot distinguish from a hard error. Detect the no-session
  case (pre-mint probe via the `[assistant.probe]` session read, and/or classify the login
  redirect in `mintInPage`) and show "Sign in to `<instance>` first, then Use my login".
- **Act-as-you by default.** On panel open against a matched tab with a live session and no
  (or stale) binding, auto-offer "Use your `<instance>` login?" — consent once, mint a
  read-only PAT in the tab, install via the existing bind, reuse silently. Sound because:
  (a) **drift re-check** — on panel open compare the probe identity against
  `binding.username` and re-offer bind on drift (re-login, shared machine); (b) one token per
  instance, reused until expiry/401, revoked on unbind; (c) label the active credential on
  the non-panel surfaces too: panel = the browser user, CLI/TUI/bench = the pinned fallback
  profile — the split is a feature only if visible.
- **Write-scope re-mint.** PAT method scope is fixed at mint, so a cached read-only token
  cannot escalate: a write-enabled assistant triggers an explicit `methods_full` re-mint
  behind the existing allow-writes consent; keep one cached token per (instance, scope). The
  session-cookie fallback stays per-session rebind (cookies can't be method-scoped).
- **Multi-target extension wiring.** The server-side registry (`[[assistants]]`, URL-aware
  endpoints, per-turn routing) exists; the extension still needs auto-select on tab switch,
  a tie picker, and per-target bind. The per-instance token cache for act-as-you (one minted
  profile per `base_url`) lands with this wave.
- **Reads prompt under the single-tool bridge.** The default `dhis2w-mcp-bridge` exposes one
  unannotated tool, so a write-enabled assistant confirms every call — reads included. The
  typed `dhis2w-mcp` (>= 1.3.0) annotates `readOnlyHint` and fixes this, but its ~315-tool
  surface is heavy for small models; on the bridge/router, reads-prompt remains inherent.
- **MCP resource for the target.** Add a `dhis2://target` resource to `dhis2w-mcp-bridge` +
  a generic MCP-resource proxy in stabbur, replacing the `[assistant.verify]` tool-call path
  without changing the `/api/assistant` contract.
- **Packaging** — Web Store (unlisted first), pinned manifest key, Firefox `sidebar_action`
  target via the WXT multi-target build.

## DHIS2 write reliability

Small local models drive DHIS2 reads near-perfectly, but none is yet trustworthy for
unattended writes: under scoring that verifies real DHIS2 state, the strongest local writer
completes 0 of 7 create→rename→delete lifecycles (it creates but doesn't reliably delete).
The write path itself is proven end-to-end; the per-action confirmation gate is the guardrail,
not the fix. Open: stronger write models. Results: `docs/guides/dhis2-benchmark-report.md`.

## Open issues

- **Relicense to open source at 1.0.0.** [Decided] The current `LICENSE` permits running and
  evaluating but reserves redistribution, hosting-as-a-service, and commercial use — the interim
  step that made publishing to PyPI coherent. Pick the target licence (MIT or Apache-2.0; Apache
  adds an explicit patent grant) and drop the reserved-rights section when 1.0.0 is cut.

- **`stabbur-mcp-web` browser path can't pin DNS.** [deferred — matters only if exposing stabbur
  beyond a trusted LAN] The static fetch path pins the resolved IP, but Chromium resolves its
  own connections, so a rebinding window remains on the Playwright path. A full fix would
  fulfill intercepted routes through the pinned httpx client (heavy; breaks streaming) —
  revisit only if the exposure model changes.

## Voice follow-ups

- **Scout the expressive TTS slot.** Nothing so far beats Kokoro's quality-per-millisecond
  (current set: Kokoro in-chat default, Spark for gender + pinned-seed voice creation).
  mlx-audio ships many untried families (vibevoice, voxcpm2, higgs_audio_v3, zonos2, …);
  audition before adopting, and verify registry metadata against real synthesis first.
- **New audio capabilities** — **speaker diarization** (MOSS-Transcribe-Diarize — gated repo, needs
  auth; VibeVoice-ASR) for who-said-what + timestamps; **speech enhancement** (DeepFilterNet /
  MossFormer2-SE) to denoise mic input before STT; **endpoint detection** (Smart Turn) for better
  turn-taking than the silence-based VAD recorder.
- **Polish** — voice cloning affordance in the Textual TUI (already in the web UI + CLI); a richer
  audio UI from [ElevenLabs UI](https://ui.elevenlabs.io/) (shadcn/Tailwind waveform/orb components).

## Remote model host (llama-server router on another box)

Day-to-day models are served by a LAN box (`msai:1234`, llama-server in router mode); the CLI,
TUI, and `stabbur serve --upstream` all front it. Open threads:

- **Remote model metadata.** Cards/tags/`n_ctx` are library concepts — a remote model shows
  none, and the SPA size column is a dash. Decide what a remote row should surface.
- **Two stores.** The T9 library (`stabbur library`) and the router box's `/data/lab/models` are
  separate collections; `stabbur library manifest`/`sync` could feed the router box so the drive
  stays the canonical archive.

## Web UI

- **A design pass on the colours.** The plumbing is done — the semantic and syntax variables
  exist, no component holds a hardcoded colour, and `docs/ui-conventions.md` records the rules —
  so retuning a value now actually moves the app, which it did not before. The four ported
  palettes are still mechanically correct and visually unrefined, and the empty chat is a
  heading and a box. `dhis2w-fhir-serve` is the source of truth for the shared variables.
- **`--good-ink` / `--warning-ink` should flow back to dhis2w-fhir-serve**, or the source is
  behind the copy. They exist because `--good`/`--warning` are tuned as fills and measure
  3.2-4.0:1 as small text on a light card, under the 4.5 AA needs; the same gap is in fhir.
- **A shared package, eventually.** Both frontends now carry the same variables, the same
  `ui/sheet.tsx`, and the same command palette, kept in step by copying. At two apps a package
  costs more than it saves; at three or four, extract the variable blocks and the primitives
  both keep re-deriving so a fix lands once.

## Page actions (and WebMCP)

Design + assessment: **[`WEBMCP.md`](WEBMCP.md)**. Short version: WebMCP is **watch, don't
build** — it inverts UI control rather than providing it, and stabbur (already an MCP client)
isn't the side that's missing anything; DHIS2 exposing tools is. Page actions themselves are
not blocked on it — the extension already executes script in the tab. Open work, in order:

- **Read-only page actions first**: navigate to a URL the assistant constructs (app + org unit
  + dataset + period), scroll to and highlight a field it just mentioned. No writes, uses
  permissions the extension already holds, and it is what makes the panel feel *in* the app.
- **Mutating clicks** only if a case appears that REST cannot serve — and then behind the
  existing per-action confirmation gate.
  (The `POST /api/files/script` injection point was checked on 2026-08-26 and is **legacy-page
  only** — modern app-platform SPAs load nothing but their own bundle, so it cannot reach the
  apps that matter. See `WEBMCP.md`.)

## Other open ideas

- **Chat history is browser-local.** Conversations live in this browser's IndexedDB, so they are
  invisible to the CLI and scoped per origin — `:2222` and `:2260` keep separate histories on one
  machine. For a tool whose premise is that your data lives on your own machine, the library is
  the obvious home. Would also make transcripts searchable and exportable without a browser.
- **Encryption at rest, opt-in.** Only worth it once history is worth protecting: a DHIS2
  assistant's transcripts hold tool results, which are real records. `~/dev/mortenoh/lockbox`
  is the pattern (encrypt before IndexedDB; threat model is a lost device). The storage module
  is a single read/write seam with whole-record boundaries and no plaintext index, so the
  encrypt/decrypt pair has one place to go. Never by default.
- **Structured output.** No `response_format` or grammar support, so anything wanting a parseable
  answer — classification, extraction — gets prose and a regex. The gap shows up the moment stabbur
  is used as a service rather than a chat window (see `docs/guides/api.md`).
- **Chat export.** Still open: PDF export in the TUI (the web UI has it, via the browser's
  own print pipeline — the TUI has no equivalent, so this needs a real renderer decision).
- **Rich tags — the last mile.** Assigning a tag a color/icon ships as `stabbur library tag-style`;
  what is left is a color-picker in the web UI and a curated default tag set seeded from
  `docs/guides/models.md`.
- **Paste-long-text-as-file.** llama.cpp's webui turns a long pasted block into an attachment.
  Cheap, but it changes paste behaviour, so it wants its own setting.

## North-star

End goal: a **local, self-hosted DHIS2 assistant** — your own model + DHIS2 tools in a
Chrome side panel:

```
Chrome extension (side panel, shadcn chat)
  → stabbur (serve --ui --model X): runs the model + MCP client + agent loop
      → MCP server from ../dhis2w-utils  → DHIS2 instance
```

Next up: page-actions via `dhis2w-browser` (kept last deliberately — an AI clicking as a
logged-in admin is the highest-blast-radius capability in the design), then packaging/stores.
