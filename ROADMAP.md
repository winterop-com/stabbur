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

- **`pull` can replace a different quant of the same repo.** A pull writes to the repo's
  library path and the copy replaces the destination, so pulling one quant of a repo the
  library already holds in another quant silently destroys the held one. `pull --all`
  deliberately skips this case (and now says so); the by-name path still carries the hazard.
  Real fix is per-quant destination paths, which touches library layout — decide before 1.0.0.

- **Quant choice is invisible to Load.** The card and Details now tell the truth about
  multi-quant repos ("2 quants · N GB total", file list marking what Load opens), but choosing
  which quant to load still needs a control and an API.

- **`benchmark run` requires a library model.** It ignores the configured upstream, so an
  upstream-only setup cannot benchmark the models it actually uses.

- **Import-time config defaults.** `frontend_dir`, `lmstudio_models_dir`, and
  `runtime_state_dir` defaults evaluate when `stabbur.config` is imported, so XDG variables
  set later in-process are ignored; `stabbur/__init__` also primes the settings cache at
  import. Harmless for the CLI (env is set before Python starts), sharp-edged for embedders
  and tests. Needs lazy defaults.

- **Extension still verifies assistants via GET.** The guard now treats `?verify=1` as
  mutating and POST routes exist; the extension works through its allow-listed origin but
  should migrate to the POST shape.

- **`/api/status` model id is unqualified.** In upstream locked mode it shows the remote id
  rather than the stabbur name, and two backends serving the same name are indistinguishable —
  wants the backend name alongside the model.

- **Tinted light themes run `-ink` tokens slightly under contrast.** `--good-ink` and
  `--warning-ink` clear 4.5:1 on white but land at ~3.9-4.1:1 on the indigo/paper/contrast/
  terminal light grounds. Fixing it means re-tuning the ten theme blocks together.

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

Day-to-day models are served by a LAN box (`gpu-box:8080`, llama-server in router mode); the CLI,
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

## Multiple backends at once (several upstreams + the local library)

Today a server is one or the other: `app.py` picks `UpstreamManager(settings.upstream)` **or**
`ServerManager(...)`, `upstream` is a single `str | None`, and in upstream mode the local library
is invisible (`/api/library` returns the remote's ids). Asked for by a user who wants a laptop's
library and one or more remote hosts in the same picker.

The manager is a narrow seam, but **not a free one** — two things found by building step 1
that the first draft of this plan got wrong:

The two managers do not share an interface. They share a *core* of seven members (`base_url`,
`current`, `last_error`, `n_ctx`, `ready`, `state`, `stop`) plus an asymmetric half: `load` is
local-only, and `load_by_name`, `models`, `select_loaded`, `touch` are upstream-only. Every call
site for the asymmetric half already sits behind a type check, so "harmonizing" the two onto one
flat surface silently reroutes the routers. A facade must model the asymmetry, not erase it.

And a facade does **not** drop in without touching the routes. Eight sites branch on the backend
*type* (`app.py` x2, `proxy.py`, `core.py` x4, `chat.py`), and the moment the manager is wrapped
every one of them goes False — silently. That is a wrong-answer failure, not a crash: status
stops reporting `upstream`, `/api/library` lists the wrong collection, `/v1/models` changes
branch. Converting them to ask the facade instead is mechanical but mandatory, and it is why
step 1 is only done once the seam is actually in use.

**Identity is the hard part, not plumbing.** A model is `ModelRef(name, model_format)` today;
two hosts both serving `gemma-4-12b` collide the moment they are listed together.

**Decided: `model@backend`, split on the LAST `@`.** Used everywhere a model is named —
`/api/load/{name:path}`, the OpenAI `model` field (so `/v1` clients can select a backend too),
and the SPA's picker. An unqualified name resolves within the ACTIVE backend, and fails with a 409 naming both
candidates as qualified ids when several backends serve it — never a silent pick.

An earlier draft said a bare name should resolve to whichever single backend serves it. Building
it showed why that is wrong: one `/api/load` would then silently repoint `/v1` and `/api/chat` at
another host, which is a far larger side effect than "resolve a name". Switching backends should
take saying so.

The separator is forced, not chosen. Both obvious ones are already spoken for: `/` by
publisher/repo (`unsloth/Qwen3.5-4B-GGUF`) and `:` by Ollama tags (`gemma4:12b-mlx`). An earlier
draft of this plan said `backend:model`, which would have made `gemma4:12b-mlx` ambiguous —
is `gemma4` a backend or a model? — and needed a split-on-first-colon rule to disambiguate.
`@` is unused by either convention, so `gemma4:12b-mlx@workstation` parses with no special case.

Open decisions, in the order they block things:

- **Decided: both.** `[[backends]]` tables (project `stabbur.toml` and the machine config, layered
  by the usual `Settings` precedence) plus a repeatable `--upstream`, whose name is derived from the
  host's first label (`http://gpu-box:8080/v1` -> `gpu-box`; IP literals keep every digit). The local
  library is implicit whenever a library is *configured* — not merely when `STABBUR_LIBRARY_ROOT`
  is set, since a project's own `libraries = [...]` configures one too — and is named `local`,
  because the qualifier lands in committed `model@local` references and must not vary per machine.

  Two sub-decisions worth keeping: two different URLs deriving the same name is a hard error
  naming both (never an auto-suffix, never a silent pick), and the layers *replace* rather than
  merge, exactly as `libraries` does. If "machine remote + project remote" turns out common, a
  `@shared`-style opt-in token is the follow-up.

  Note `[[backends]]` in `stabbur.toml` puts a machine-specific URL in the file whose point is
  being portable. The machine config is the honest home for a host like `gpu-box`; the project file
  is right only when a team shares the host. It should not go in the `project init` scaffold.
- **Decided: "loaded" stays singular** — one model this stabbur is currently pointed at.
  Backends may independently hold things resident (a remote router always does); stabbur tracks
  one selection, `/api/status` keeps one answer, and `/v1` keeps one proxy target.

  The case for plural was fast switching, and it largely does not exist. Remote "loading" is
  only a *selection* — the remote holds what it holds regardless — so switching between remotes
  is already free. And local is singular by construction: `ServerManager.load()` calls `stop()`
  before spawning. Plural would therefore mean several llama-servers resident at once, which on
  a machine where one model is a third of RAM is not fast switching but an OOM (observed:
  loading two test models beside a resident one killed a running server).

  Plural also costs `/v1` its transparency. It forwards to one `base_url` byte-for-byte today,
  which is why any OpenAI client works unmodified; several live backends would mean parsing
  every request body to route on `model`, and making the runtime reservation per-backend.

  If fast local switching is ever the real want, the answer is llama-server's router mode
  locally — built for it, and it manages the memory itself — not plural backends here.
- **Decided and built: a down backend degrades to a row.** `/api/library` probes backends
  concurrently with a per-backend timeout and returns HTTP 200 with one `unavailable` row
  carrying the reason — measured at 5.04s against a black-holed host, where it previously 502'd
  after 15s. `/api/library` became `async` for the concurrency; three slow backends now cost one
  timeout, not three.

  One asymmetry left deliberately: `LibraryNotConfigured` is re-raised rather than degraded,
  because burying it in an anonymous row would lose the hint naming `STABBUR_LIBRARY_ROOT`. So an
  unconfigured local library still fails a whole listing. Worth revisiting.

**Not this feature:** fan-out to several models, cross-backend fallback, or load balancing.
Those are a router's job — and a `llama-server` in router mode already covers "many models
behind one URL", which is what most of the ask turns out to be.

Build order: the facade around a single backend first (no behaviour change, proves the seam),
then declaration + merged listing, then qualified ids and load resolution, then the picker
grouping by origin, then docs. Related: **Two stores**, above — the drive and the router box
being separate collections is the same split seen from the library side.

## Page actions (and WebMCP)

Design: **[`PAGEACTIONS.md`](PAGEACTIONS.md)**. The WebMCP question is settled separately —
**watch, don't build**, [`WEBMCP.md`](WEBMCP.md) — and page actions were never blocked on it.
`page_read` ships end to end. Open, in order:

- **Finish `page_navigate`.** The server half is registered, gated and URL-validated; the
  extension implements `page_read` alone, so what is missing is a handler in
  `extension/lib/pageActions.ts` plus the frame's `args` plumbed through `executePageAction`.
- **Label page content as untrusted in a successful read.** A failed read already frames the
  wall's own words that way; a successful one hands back bare JSON. Cheap, and it should land
  before any acting action does.
- **Mutating actions (`page_click` / `page_fill`)** only if a case appears that an API cannot
  serve. The forced gate is already in place, and consuming the read's opaque `ref`s is what
  would turn their containment property from intent into fact.

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
- **The TUI has no turn stats.** The web UI reports what a turn cost — tokens, wall time and
  tokens/sec (`MessageItem.tsx`, fed by the agent loop's `on_usage` sink) — and the Textual chat
  shows none of it. The data is already there: `agent.run` takes `on_usage` and the runtimes
  return a final usage chunk when asked (`stream_options.include_usage`), so this is a display
  gap in `chat_tui/`, not a plumbing one. Decide where it goes in a full-screen TUI — a footer
  segment is the obvious slot, but it competes with the existing status line.

- **Chat export.** Still open: PDF export in the TUI (the web UI has it, via the browser's
  own print pipeline — the TUI has no equivalent, so this needs a real renderer decision).
- **Rich tags — the last mile.** Assigning a tag a color/icon ships as `stabbur library tag-style`;
  what is left is a color-picker in the web UI and a curated default tag set seeded from
  `docs/guides/models.md`.
- **Skills: is there anything to build, or is it MCP plus a prompt?** Asked directly, and the
  first job is deciding whether "skill" names something stabbur lacks. A skill in the Claude Code
  sense is packaged instructions that load on demand — which here decomposes into things that
  already exist (a project's system prompt, `.mcp.json` tool sets, per-chat tool enabling) plus
  one thing that does not: *selective* loading, so a model sees the instructions for the task at
  hand rather than every instruction at once. That matters more for small local models than for
  frontier ones, since the prompt budget is the scarce resource. Open: whether the unit is a
  project, a file convention, or an MCP server that serves instructions as resources; and whether
  selection is the user's (a picker) or the model's (a tool call that loads a skill).

- **More bundled MCP servers?** Twelve ship today. Adding one is cheap, but each is a tool in
  every model's context by default, and tool-choice accuracy on small local models degrades as
  the list grows — the DHIS2 benchmark already shows a 315-tool surface being too heavy. So the
  question is not "which server next" but what earns a slot: the answer is probably fewer,
  broader servers plus better per-chat enabling, not more entries. Candidates worth weighing
  against that: a filesystem-write server (the current `files` is read-oriented), a
  SQLite/dataset server for local analysis, and a calendar/mail reader. Each needs a reason it
  beats "the user pastes it in".

- **Diagrams beyond mermaid.** Mermaid renders today (lazy-loaded, deferred while streaming,
  falls back to source). What is missing is everything else a model might emit: Graphviz/DOT,
  PlantUML, and plain SVG. DOT is the most likely next — models emit it readily and
  `@viz-js/viz` is WASM, so it stays a client-side render with no new service. PlantUML needs a
  server, which is a different decision (nothing leaves the box is the whole premise). Also
  open: what to do with a fence in a language we cannot render — today it falls through to a
  code block, which is the right default and should stay.

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
