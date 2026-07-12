# kodo roadmap

Forward-looking plans and ideas. Kept out of `CLAUDE.md` so it doesn't load into
every session's context. `CLAUDE.md` holds the durable project rules, architecture,
and conventions; this file holds what's **next**. Completed work is removed (git
history + `docs/` are the record) — this file is only open threads.

## The browser extension: built (pending review), follow-ups open

The MV3 side-panel extension shipped at `extension/` (branch `feat/chrome-extension`):
WXT + the shared SPA chat core, connection state machine, typed-SSE chat, DHIS2 page context +
target match/mismatch via the new generic `GET /api/assistant`, narrow same-origin session reads,
remote-kodo bearer support, and a two-tier Playwright E2E suite (mock + live against play42,
read-only). Verified prompt catalog: `docs/guides/extension-prompts.md`. Design + status deltas:
**[`CHROME.md`](CHROME.md)** (see its Status sections).

**Round 2 shipped (2026-07-11):** the **"Use my login"** bind flow — read-only PAT minted in the
target tab's own context (`POST /api/apiToken`), installed via a domain-generic `POST
/api/assistant/bind` (kodo runs `d2w profile add … --auth pat --local` with the token in env),
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
/api/chat/confirm` resolving a per-generation future (300s -> auto-deny, `KODO_CONFIRM_TIMEOUT`).
The scripted `kodo chat -p` has no confirm channel, so it **fail-safe denies** gated writes unless
`--allow-writes` is passed. The gate is **generic and fail-safe**: kodo reads each MCP tool's
`readOnlyHint` and requires confirmation for any tool not marked read-only, defaulting
**unannotated** tools to needs-confirmation; the policy is a tri-state `all|writes|none` that
defaults from the assistant (readonly/free-play -> `none`, write-enabled -> `writes`). No DHIS2
logic entered kodo core. On the DHIS2 side: the **write bind** mints a `methods_full` PAT when the
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

- **Reads also prompt under the single-tool bridge (the next write-UX step).** The default
  `dhis2w-mcp-bridge` exposes one **unannotated** tool (`dhis2_cli`), so under a write-enabled
  assistant the fail-safe gate prompts on **every** dhis2 call — reads included, not just
  mutations. **No current dhis2w server fixes this** (verified live, 2026-07-12): the
  `dhis2w-mcp-router` is a 2-tool dispatcher (`search_tools` / `call_tool`) — its `call_tool` is
  generic and cannot be marked read-only — and even the 104-tool `dhis2w-mcp` ships **zero**
  `readOnlyHint` annotations. The real remedy is a **dhis2w change**: annotate read operations with
  `readOnlyHint=True` (most naturally per-op in `dhis2w-mcp`), which kodo's gate already honors.
  Until then reads-prompt is inherent (friction, not danger — reads are safe and shown). Pair with
  the write-reliability work below.
- **MCP resource for the target** — now unblocked: **dhis2w 1.0.0 has shipped**. Add a
  `dhis2://target` resource to `dhis2w-mcp-bridge` + a generic MCP-resource proxy in kodo,
  replacing the `[assistant.verify]` tool-call path without changing the `/api/assistant` contract.
- **Packaging** — Web Store (unlisted first), pinned manifest key, Firefox `sidebar_action` target
  via the WXT multi-target build.
- **Re-run the tools-dhis2 benchmark against the new tool-output shape.** Tool results now reach
  the model as compact JSON instead of the old repr text; the "Ornith-1.0-9B 12/12" result cited
  in the dhis2 template's model choice predates that change. Likely fine or better (JSON is what
  these models were tool-trained on), but per "actually test every path" the sweep needs a re-run
  before the claim is cited again.
- **Live-E2E the bind mint tail in-browser.** The live spec asserts login/tab-match/consent but
  skips the in-tab mint when headless: `chrome.scripting.executeScript` on the play tab needs host
  access, which comes from `activeTab` at runtime (toolbar click) and cannot be granted reliably
  headless via `chrome.permissions.request`. The tail is covered by the mock UI spec + an
  out-of-band live proof with the exact payloads; a real fix is driving the action click via CDP
  or a test-only granted-permissions profile.

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

Next: stronger write models; a dhis2w-side change to annotate read operations with `readOnlyHint`
(none of the current servers do — see the follow-up above) so kodo's gate narrows prompts to real
mutations; and richer verification that asserts real DHIS2 state, not just a `LIFECYCLE_OK`
completion token.

## Open issues

- **Audio-specialist models don't process audio.** [High] gemma-4-12B transcribes audio fine, but
  Ultravox 500s (`image input is not supported`) and Voxtral silently ignores the audio. **Code
  investigation (2026-07-05):** kodo's path looks correct — `capabilities()` reads the projector's
  `clip.has_audio_encoder` flag to detect audio, `build_command` passes `--mmproj <projector>`
  uniformly (llama-server's mtmd handles vision + audio; installed llama-server is b9870, which has
  audio support), and the agent sends OpenAI `input_audio` parts. So the fault is most likely
  **downstream of kodo** — llama.cpp/mtmd support for these specific architectures (Ultravox's
  Whisper-style encoder, Voxtral) — or a projector-selection edge case (`pick_gguf` finds the mmproj
  by a `mmproj*` filename; a repo naming it otherwise would be missed → no `--mmproj` → audio fails).
  **Verification plan (needs a small audio-specialist GGUF in the library — still blocked):**
  (1) pull one + its projector; (2) confirm `capabilities()` reports `audio=True` and which mmproj;
  (3) run `llama-server -m <model> --mmproj <audio-proj>` and curl an `input_audio` request to
  isolate kodo vs llama.cpp; (4) if it fails llama-server-alone → upstream issue; if it works alone
  → fix kodo's mmproj-pick (match by `clip.has_audio_encoder`, not filename) or the content shape.

- **`kodo-mcp-web` browser path can't pin DNS.** [deferred residual — only matters if exposing
  kodo beyond a trusted LAN] The static fetch path is fixed (resolve once, vet, connect to the
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
  web UI has it) and a non-interactive `kodo chat --save` for the `-p` one-shot path.
- **Rich tags — the last mile.** The normalized tag registry (`{tag: {color, icon, …}}` +
  `GET /api/tags/registry`) already ships; the UI prefers a registry color, else a name-derived one.
  Open: a color-picker / `kodo library tag --color`, and a curated default tag set seeded from
  `docs/guides/models.md`.
- **More MCP servers** — a `kodo-mcp-http` (allowlisted fetch) and a git server, on the same
  `kodo-mcp-*` template (dependency-light, stdio-only, `pydantic-settings` config, sandbox/allowlist
  anything that executes or fetches).
- **Want-list drive rebuild.** `kodo library manifest`/`sync` ship. A natural next step: a
  `--verify`/repair pass that re-pulls only models failing `kodo library verify --deep`.

## North-star

End goal: a **local, self-hosted DHIS2 assistant** — your own model + DHIS2 tools in a
Chrome side panel:

```
Chrome extension (side panel, shadcn chat)
  → kodo (serve --ui --model X): runs the model + MCP client + agent loop
      → MCP server from ../dhis2w-utils  → DHIS2 instance
```

**Build order:**

1. **Phase 1 — kodo + web chat UI + generic tool/MCP support.** Done: the library, pull/run/chat,
   `serve --ui` and locked `serve --ui --model X`, the `/v1` proxy, the agent loop + MCP client
   pointable at any server, the bundled toolset, voice, and the Textual TUI.
2. **Phase 2 — DHIS2 + Chrome extension** [built 2026-07-10, pending review]: the MV3 side-panel
   extension at `extension/`, including page-context and session reads (see the top of this file +
   `CHROME.md`).
3. **Later** — read-only PAT-minting ("Use my login") shipped in round 2; gated writes (per-action
   confirmation + write bind + CSRF double-submit) shipped in round 3; next is page-actions via
   `dhis2w-browser`; packaging/stores.
