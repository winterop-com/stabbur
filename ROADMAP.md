# heim roadmap

Forward-looking plans and ideas. Kept out of `CLAUDE.md` so it doesn't load into
every session's context. `CLAUDE.md` holds the durable project rules, architecture,
and conventions; this file holds what's **next**. Completed work is removed (git
history + `docs/` are the record) — this file is only open threads.

## The browser extension: built (pending review), follow-ups open

The MV3 side-panel extension shipped at `extension/` (branch `feat/chrome-extension`):
WXT + the shared SPA chat core, connection state machine, typed-SSE chat, DHIS2 page context +
target match/mismatch via the new generic `GET /api/assistant`, narrow same-origin session reads,
remote-heim bearer support, and a two-tier Playwright E2E suite (mock + live against play42,
read-only). Verified prompt catalog: `docs/guides/extension-prompts.md`. Design + status deltas:
**[`CHROME.md`](CHROME.md)** (see its Status sections).

**Round 2 shipped (2026-07-11):** the **"Use my login"** bind flow — read-only PAT minted in the
target tab's own context (`POST /api/apiToken`), installed via a domain-generic `POST
/api/assistant/bind` (heim runs `d2w profile add … --auth pat --local` with the token in env),
with a plain-language consent card, unbind (revoke + profile removal), and a **session-cookie
fallback** riding a new `session` auth kind in `dhis2w-client`/`d2w` (shipped in **dhis2w 1.0.0**
on PyPI, so `uvx dhis2w-mcp-bridge` and `d2w profile add --auth session` work out of the box). Also: declared
`[assistant.probe]` recipes replaced the round-1 hardcoded identity endpoints, browser-user vs
tool-account identity labels, compact collapsed tool-result chips, and a Settings backend switcher.
The whole bind flow is covered live against play42 (`extension/e2e/live/live.spec.ts`).

**Round 3 shipped (2026-07-12): writes, gated.** The round-2 "writes last, behind explicit
per-action confirmation" plan is now implemented. A **per-action confirmation gate** fronts every
mutating tool call across all chat surfaces (web `serve --ui`, the Chrome side panel, the Textual
TUI): a write-enabled assistant prompts Approve/Deny before each gated call, and a declined call
returns `error: user declined this action` so the model continues. Backend: an awaitable
`on_confirm` in the agent loop, a `confirm`/`confirm_resolved` SSE event pair, and `POST
/api/chat/confirm` resolving a per-generation future (300s -> auto-deny, `HEIM_CONFIRM_TIMEOUT`).
The scripted `heim chat -p` has no confirm channel, so it **fail-safe denies** gated writes unless
`--allow-writes` is passed. The gate is **generic and fail-safe**: heim reads each MCP tool's
`readOnlyHint` and requires confirmation for any tool not marked read-only, defaulting
**unannotated** tools to needs-confirmation; the policy is a tri-state `all|writes|none` that
defaults from the assistant (readonly/free-play -> `none`, write-enabled -> `writes`). No DHIS2
logic entered heim core. On the DHIS2 side: the **write bind** mints a `methods_full` PAT when the
assistant is write-enabled (the binding records read-vs-write scope, shown in the extension's
Acting-as chip), and the session-cookie fallback may also write (a session credential can't be
method-scoped, so the confirmation gate is its guardrail). CSRF: an optional `X-XSRF-TOKEN`
double-submit — the extension captures the `XSRF-TOKEN` cookie at a session-write bind and passes
it via `DHIS2_SESSION_XSRF` into the stored d2w profile (shipped in dhis2w across v41/v42/v43);
inert when the instance doesn't issue the cookie, so it only future-proofs a hardened one. Live
write tests run against a local/non-protected instance (play42 refuses writes as a
`DHIS2_MCP_PROTECTED_HOSTS` host); coverage is mock e2e + the `tools-dhis2-write` benchmark
(`docs/guides/dhis2-benchmark-report.md`).

Open follow-ups, roughly in order:

- **Browser identity vs tool-account identity are uncorrelated (a design footgun).** By default the
  tool channel authenticates as the pinned d2w profile (e.g. `play42` = admin / "John Traore"),
  **regardless of who is actually logged into the page** the panel is attached to. So the read chat
  silently acts as the profile, not the viewer — confusing, and potentially dangerous (a user
  believes they are acting as themselves while reads/writes run as admin). The round-3 identity
  labels ("Browser session user" vs "Tool account") only *surface* the mismatch; they don't resolve
  it. "Use my login" is the one path that aligns the two (mint a PAT as the browser user), but it is
  opt-in and per-session. Decide the default posture before the assistant is pointed at real
  (non-demo) instances — options: (a) warn/refuse when the tool account != the signed-in browser
  user for a matched instance; (b) auto-prompt to bind when a logged-in session is detected for the
  target; (c) make the browser-user binding the default for DHIS2 targets rather than a shared
  profile. Note: "auto-login the browser using the profile creds" is the wrong fix — it would leak
  profile secrets to the extension and defeats the purpose of binding to the *human's* identity.
- **Multi-profile: match the tab URL to the right d2w profile (or a list).** Today heim serve is
  single-target: one project = one `[assistant]` = one `base_url` = one `DHIS2_PROFILE` pinned at
  serve start. But d2w profiles already ARE a multi-profile store (`~/.config/dhis2/profiles.toml` —
  many named profiles, each a base_url + auth). Evolve heim into "a registry of profiles,
  auto-selected by the page you are on": browse dev → staging → a country's prod and heim uses the
  matching creds; when several profiles hit the same URL (admin vs clerk role) offer a picker. Cheap
  parts (mostly present): the extension already knows the active tab URL (page context) and already
  has a backend-switcher UI to reuse; matching is origin / longest-path prefix against each profile's
  base_url, returning a list on ties. Real work: (1) profile discovery in heim (read `d2w profile
  list` / profiles.toml into a registry); (2) a URL-aware endpoint (`/api/assistants` or
  `/api/assistant?url=<tab>`) so the extension asks "which profile(s) fit this page?"; (3) **per-tab
  tool routing — the gating decision**, since the bridge is spawned with a fixed `DHIS2_PROFILE`.
  Routing options: (a) N bridges, one per profile, namespaced (`play42__dhis2_cli`, …) — entirely in
  heim, but the model sees many tools; (b) a per-call `--profile` arg on `dhis2_cli` / the typed
  server — cleanest, but a dhis2w change; (c) re-select the profile on tab switch — medium. Extension
  + matching ≈ a few days; option (a) needs no dhis2w change and is the pragmatic MVP. This subsumes
  and generalizes the single-target assistant model.
- **Bind UX: a clear "sign in first" state.** When the browser has no live session for the target,
  the in-tab PAT mint fails with a bare `status 0` (the POST is redirected to the login page and the
  fetch comes back opaque). Detect the no-session case (probe the identity endpoint, or classify the
  redirect) and show "Sign in to `<instance>` first, then Use my login" instead of a raw status.
- **Reads also prompt under the single-tool bridge (the next write-UX step).** The default
  `dhis2w-mcp-bridge` exposes one **unannotated** tool (`dhis2_cli`), so under a write-enabled
  assistant the fail-safe gate prompts on **every** dhis2 call — reads included, not just
  mutations. The **typed `dhis2w-mcp` (>= 1.3.0) now fixes this**: it stamps `readOnlyHint=True` on
  its ~104 read operations (verified — `metadata_data_element_get` True, `..._create` False), which
  heim's gate already honors, so a write assistant on that server confirms only writes. The
  tradeoff is its ~315-tool surface (heavy for small models). The default single-tool bridge still
  can't be annotated per-op (one dynamic tool), and the `dhis2w-mcp-router` is a 2-tool dispatcher
  whose generic `call_tool` can't be read-only — so on those, reads-prompt remains inherent
  (friction, not danger — reads are safe and shown). Pair with
  the write-reliability work below.
- **MCP resource for the target** — now unblocked: **dhis2w 1.0.0 has shipped**. Add a
  `dhis2://target` resource to `dhis2w-mcp-bridge` + a generic MCP-resource proxy in heim,
  replacing the `[assistant.verify]` tool-call path without changing the `/api/assistant` contract.
- **Packaging** — Web Store (unlisted first), pinned manifest key, Firefox `sidebar_action` target
  via the WXT multi-target build.
- **Re-run the tools-dhis2 benchmark against the new tool-output shape.** Tool results now reach
  the model as compact JSON instead of the old repr text; the "Ornith-1.0-9B 12/12" result cited
  in the dhis2 template's model choice predates that change. Likely fine or better (JSON is what
  these models were tool-trained on), but per "actually test every path" the sweep needs a re-run
  before the claim is cited again.
- **Bind mint tail — resolved, with a dev-tooling note.** The in-tab mint (`chrome.scripting.
  executeScript` on the target tab) needs host access. At real runtime that comes from `activeTab`
  when the user opens the side panel via the **toolbar icon** on the target tab — so real installs
  mint fine. The blocker was only automation, where the panel is opened as a *tab* (no `activeTab`):
  fixed by the **test-only `HEIM_E2E=1` build**, which puts the target origins in static
  `host_permissions` (combine with `HEIM_FLAVOR=dhis2` for a branded automation build). The live
  spec is scoped to the bound-state proof; the full mint→verify→unbind cycle is covered by
  `e2e/mock/bind.spec.ts` + interactive manual verification. Dev ergonomics follow-up: promote the
  throwaway `extension/e2e/manual.ts` (headed browser + `heim serve` + extension, wired to play42)
  into a supported `heim ext-dev` launcher for interactive testing.

The DHIS2 MCP servers it points at are the published PyPI packages (`uvx dhis2w-mcp-bridge` is the
default; router/full-server for bigger models); source lives in `~/dev/local/dhis2w-utils`.

## DHIS2 write reliability

Small local models drive DHIS2 **reads** near-perfectly but **writes are much harder**, and the
honest number is worse than an earlier weak scorer suggested. Under scoring that **verifies real
DHIS2 state** (create really persisted, then really absent at the end — not just a self-reported
`LIFECYCLE_OK` token), the strongest writer `gemma-4-12B` completes **0 of 7** lifecycles: it
reliably *creates* but does not reliably *delete*, leaving residue on every problem. The delete half
of the multi-step create→(rename/link)→delete lifecycle is where it fails. Full results + the
scoring correction: `docs/guides/dhis2-benchmark-report.md`.

Crucially, the write **path** is proven end-to-end (a live Chrome-panel test creates a metadata
object, approved through the gate, and read-back-verifies it persisted) — it is the model's
autonomous *reliability* that is the bottleneck, not the plumbing. The `dhis2-write` project keeps a
small default; no local model is yet trustworthy for unattended writes.

The current answer to "not trustworthy unattended" is the **round-3 per-action confirmation gate**
(above): writes only run once the human approves each mutation — and, crucially, can notice an
*incomplete* cleanup — so the model's weak completion is fronted by a person, not trusted. That is
the guardrail, not the fix.

Next: stronger write models; on the default bridge, reads still prompt (the typed `dhis2w-mcp`
>= 1.3.0 now annotates `readOnlyHint` so its reads skip the gate, but its ~315-tool surface is
heavy for small models — see the follow-up above); and richer verification (already shipped) that
asserts real DHIS2 state, not just a `LIFECYCLE_OK`
completion token.

## Open issues

- **Audio-specialist models don't process audio.** [High] gemma-4-12B transcribes audio fine, but
  Ultravox 500s (`image input is not supported`) and Voxtral silently ignores the audio. **Code
  investigation (2026-07-05):** heim's path looks correct — `capabilities()` reads the projector's
  `clip.has_audio_encoder` flag to detect audio, `build_command` passes `--mmproj <projector>`
  uniformly (llama-server's mtmd handles vision + audio; installed llama-server is b9870, which has
  audio support), and the agent sends OpenAI `input_audio` parts. So the fault is most likely
  **downstream of heim** — llama.cpp/mtmd support for these specific architectures (Ultravox's
  Whisper-style encoder, Voxtral) — or a projector-selection edge case (`pick_gguf` finds the mmproj
  by a `mmproj*` filename; a repo naming it otherwise would be missed → no `--mmproj` → audio fails).
  **Verification plan (needs a small audio-specialist GGUF in the library — still blocked):**
  (1) pull one + its projector; (2) confirm `capabilities()` reports `audio=True` and which mmproj;
  (3) run `llama-server -m <model> --mmproj <audio-proj>` and curl an `input_audio` request to
  isolate heim vs llama.cpp; (4) if it fails llama-server-alone → upstream issue; if it works alone
  → fix heim's mmproj-pick (match by `clip.has_audio_encoder`, not filename) or the content shape.

- **`heim-mcp-web` browser path can't pin DNS.** [deferred residual — only matters if exposing
  heim beyond a trusted LAN] The static fetch path is fixed (resolve once, vet, connect to the
  pinned IP with Host/SNI on the real hostname; blocklist is `not is_global`, covering CGNAT),
  but the Playwright path only re-vets each request via interception — Chromium resolves its own
  connections, so a rebinding window remains there. A full fix would fulfill intercepted routes
  through the pinned httpx client (heavy; breaks streaming) — revisit only if the exposure model
  changes.

## Voice follow-ups

- **Qwen3-TTS support.** Flagged `supported=False`: mlx-audio's `load_model` doesn't wire up its
  separate speech tokenizer (`Qwen3TTSSpeechTokenizer` in the repo's `speech_tokenizer/`), so
  `generate_audio` errors. Enable by loading the tokenizer + `model.load_speech_tokenizer(...)`.
- **Get the not-yet-working mlx-audio models running** — they load but the high-level `generate`
  produces no audio without bespoke args: `qwen3-tts`, KittenTTS (needs a named voice),
  OuteTTS-1.0-mlx, Qwen3-TTS-VoiceDesign (needs a voice-design prompt), Voxtral-TTS.
- **New audio capabilities** — **speaker diarization** (MOSS-Transcribe-Diarize — gated repo, needs
  auth; VibeVoice-ASR) for who-said-what + timestamps; **speech enhancement** (DeepFilterNet /
  MossFormer2-SE) to denoise mic input before STT; **endpoint detection** (Smart Turn) for better
  turn-taking than the silence-based VAD recorder.
- **Expressive / emotion-controllable voices.** Kokoro/OuteTTS give natural prosody but no emotion
  knob. **Chatterbox** (already in the registry) is the most promising path — an intensity param,
  native MLX. Heavier alternatives stay PyTorch/GPU-leaning (CosyVoice 2, Parler-TTS, Orpheus-3B).
  A deliberate later add-on, not a replacement for the Kokoro baseline.
- **Polish** — voice cloning affordance in the Textual TUI (already in the web UI + CLI); a richer
  audio UI from [ElevenLabs UI](https://ui.elevenlabs.io/) (shadcn/Tailwind waveform/orb components).

## Other open ideas

- **Chat export.** `/export` and `/export --thinking` ship in the TUI. Still open: PDF export (the
  web UI has it) and a non-interactive `heim chat --save` for the `-p` one-shot path.
- **Rich tags — the last mile.** The normalized tag registry (`{tag: {color, icon, …}}` +
  `GET /api/tags/registry`) already ships; the UI prefers a registry color, else a name-derived one.
  Open: a color-picker / `heim library tag --color`, and a curated default tag set seeded from
  `docs/guides/models.md`.
- **More MCP servers** — a `heim-mcp-http` (allowlisted fetch) and a git server, on the same
  `heim-mcp-*` template (dependency-light, stdio-only, `pydantic-settings` config, sandbox/allowlist
  anything that executes or fetches).
- **Want-list drive rebuild.** `heim library manifest`/`sync` ship. A natural next step: a
  `--verify`/repair pass that re-pulls only models failing `heim library verify --deep`.

## North-star

End goal: a **local, self-hosted DHIS2 assistant** — your own model + DHIS2 tools in a
Chrome side panel:

```
Chrome extension (side panel, shadcn chat)
  → heim (serve --ui --model X): runs the model + MCP client + agent loop
      → MCP server from ../dhis2w-utils  → DHIS2 instance
```

**Build order:**

1. **Phase 1 — heim + web chat UI + generic tool/MCP support.** Done: the library, pull/run/chat,
   `serve --ui` and locked `serve --ui --model X`, the `/v1` proxy, the agent loop + MCP client
   pointable at any server, the bundled toolset, voice, and the Textual TUI.
2. **Phase 2 — DHIS2 + Chrome extension** [built 2026-07-10, pending review]: the MV3 side-panel
   extension at `extension/`, including page-context and session reads (see the top of this file +
   `CHROME.md`).
3. **Later** — read-only PAT-minting ("Use my login") shipped in round 2; gated writes (per-action
   confirmation + write bind + CSRF double-submit) shipped in round 3; next is page-actions via
   `dhis2w-browser`; packaging/stores.
