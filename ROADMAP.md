# kodo roadmap

Forward-looking plans and ideas. Kept out of `CLAUDE.md` so it doesn't load into
every session's context. `CLAUDE.md` holds the durable project rules, architecture,
and conventions; this file holds what's **next**. Completed work is removed (git
history + `docs/` are the record) — this file is only open threads.

## Next: the browser extension (Phase 2)

Phase 1 (kodo + web chat UI + generic tool/MCP support) is done, so the platform is ready for the
north-star assistant (diagram at the bottom). In place:

- **Locked single-model serve** — `kodo serve --ui --model X` exposes a stable OpenAI `/v1` and
  `/api/chat` behind a cross-site guard + bearer auth (see `CHROME.md`) — the intended extension backend.
- **Agent loop + MCP client** — tools from any MCP server (`.mcp.json`), streamed tool activity, each
  call bounded by a timeout; a vision model now also *sees* images a tool returns (e.g. screenshots).
- **Reproducible assistants** — `kodo project new --template {dhis2,dhis2-write,browse,…}` binds a
  model + tools + system prompt into a committable project.

**The work:** package the existing SPA (`frontend/`) as an **MV3 Chrome side-panel extension**
pointed at a locked `/v1`. The side-panel-client vs cookie-relay design, the `/api/chat` contract +
409 handling, CORS vs cross-site-guard mechanics, and the live-session SameSite analysis are all
worked out in **[`CHROME.md`](CHROME.md)** — start there.

The DHIS2 MCP servers to point it at live in `~/dev/local/dhis2w-utils` (bridge wiring done, verified
via `kodo mcp add dhis2`):

- **`dhis2w-mcp-bridge`** — one `dhis2_cli` tool shelling out to `d2w`; the default for small models.
- **`dhis2w-mcp-router`** — 2 meta-tools (`search_tools`/`call_tool`), lazy typed discovery, a single
  guarded chokepoint + read-only mode.
- **`dhis2w-mcp`** — the full ~304 typed tools (big-context hosts).
- `dhis2w-browser` — Playwright DHIS2 automation, for the later "act on the page" tier.

## DHIS2 write reliability

Small local models drive DHIS2 **reads** near-perfectly but **writes are much harder**: the
multi-step create→(rename/link)→delete→confirm lifecycle trips them up, and every model tested left
residue (incomplete deletes). Size does not help — the 12B gemma is the best writer, while the two
biggest tested (27B dense, 35B-A3B MoE) tie-or-lose and leave the most residue (they over-generate,
loop, and drop the completion protocol). Full results: `docs/guides/dhis2-benchmark-report.md`. Even
the best isn't yet trustworthy for unattended writes; the `dhis2-write` project keeps a small default
and notes gemma-4-12B as the stronger write driver.

Next: stronger write models; a guarded write chokepoint (`dhis2w-mcp-router` read-only-by-default);
and richer verification that asserts real DHIS2 state, not just a `LIFECYCLE_OK` completion token.

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
2. **Phase 2 — DHIS2 + Chrome extension** [next]: package the SPA as the MV3 side-panel extension
   against the locked `/v1` (see the top of this file + `CHROME.md`).
3. **Later** — extension page-context, then page-actions via `dhis2w-browser`.
