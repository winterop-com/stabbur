# kodo roadmap

Forward-looking plans and ideas. Kept out of `CLAUDE.md` so it doesn't load into
every session's context. `CLAUDE.md` holds the durable project rules, architecture,
and conventions; this file holds what's **next**. Completed work is removed (git
history is the record) — this file is only open threads.

## Next up (concrete, as of 2026-07-05)

The near-term polish queue is clear — the Textual TUI now has full web-UI parity (palette,
`/`-autocomplete, MCP enable/disable/reconnect, `/export`, live `/set` sampling, and model
switching via `/model` / the palette), the frontend rename + mobile drawer shipped, and the
bundled MCP toolset is complete. What's left is the longer-horizon threads below.

## DHIS2 assistant — near-term

The north star (bottom of this file) is the local DHIS2 assistant. **Shipped:** the read-only
`tools-dhis2` benchmark + report (`docs/guides/dhis2-benchmark-report.md`), the
`kodo project new --template dhis2` starter (self-contained uv project; worked instance at
`../kodo-projects/dhis2`), and the full DHIS2 docs guide (`docs/guides/dhis2.md`). Remaining:

1. **`tools-dhis2` write suite.** Create / update / delete against a throwaway/local instance
   (`DHIS2_MCP_READONLY` off) to see which models can safely drive mutations, then fold the winner
   into the project's `[project].model`. **Blocked:** no local writable DHIS2 was reachable (profile
   `local` at http://localhost:8080 is down) and writes to the shared play demos are refused by
   design. Start a local DHIS2, then add `tools-dhis2-write.toml`.

## Open issues

- **Audio-specialist models don't process audio.** [High] gemma-4-12B transcribes audio
  fine, but Ultravox 500s (`image input is not supported`) and Voxtral silently ignores the
  audio — likely a `llama-server` mmproj-routing issue for their audio-only projectors; needs
  a runtime/projector investigation. (No audio-specialist model is currently in the library to
  reproduce against.)

## Internal MCP servers — the "normal toolset" (complete)

All planned bundled servers have shipped, each a workspace member following the
`kodo-mcp-datetime` template (src layout, advertise-only `mcp_servers` plugin hook so
`kodo mcp list` / `mcp add` / tool pickers pick it up) with a `tools-<name>` benchmark suite:
`datetime`, `utils`, `search`, `web` (optional `--extra web`), `memory` (persistent notes),
`weather-yr` (met.no/yr.no weather), `files` (read-only browse/read/search under a root, writes
behind `KODO_FILES_WRITABLE`), and `exec` (sandboxed Python via the shared `kodo-sandbox` lib —
no network, read-only fs, capped mem/cpu/pids, timeout; Docker-gated).

New ones are easy to add on this template — future ideas: a shell variant of `exec`, a
`kodo-mcp-http` (allowlisted fetch), a git server. Cross-cutting rules still hold: keep each
dependency-light and stdio-only (heavy ones behind an extra, like `web`); config via
`pydantic-settings` (`KODO_*`); anything that executes or fetches gets a sandbox/allowlist.

## Voice follow-ups

The voice back-half shipped (registry/import, `kodo library pull voice`, mlx-audio + Kokoro-ONNX
runtimes, `/v1/audio/*` endpoints, the web Voice studio, chat dictation + speak-replies). Open:

- **Qwen3-TTS support.** Flagged `supported=False`: mlx-audio's `load_model` doesn't wire up its
  separate speech tokenizer (`Qwen3TTSSpeechTokenizer` in the repo's `speech_tokenizer/`), so
  `generate_audio` errors. Enable by loading the tokenizer + `model.load_speech_tokenizer(...)`.
- **Dia self-contained on the drive.** Dia loads its DAC codec
  (`mlx-community/descript-audio-codec-44khz`, 293MB) from `~/.cache/huggingface`, not the
  library — mlx-audio hardcodes the repo id. For offline portability, point `HF_HUB_CACHE` at a
  drive dir **at process startup** (its cache constants are import-time) and seed it once.
- **Richer audio UI.** [ElevenLabs UI](https://ui.elevenlabs.io/) is a shadcn/Tailwind audio
  component registry (waveform player, orb, …) on the same stack — a natural polish pass.
- **Voice cloning in the Textual TUI.** Reachable from the web UI + CLI (`kodo voice speak
  --model dia --ref-audio … --ref-text …`); add a TUI affordance too.
- **Newer mlx-audio models** ([models page](https://blaizzy.github.io/mlx-audio/models/)). A batch
  of these was validated 2026-07-04 and **added to the registry** (see the model catalog):
  `soprano`, `chatterbox` (emotion), `spark`, `csm` (cloning), `parakeet`, `qwen3-asr`,
  `distil-whisper`. Still open:
  - **Get the not-yet-working ones running** — they load but mlx-audio's high-level `generate`
    produces no audio without bespoke args: `qwen3-tts`, KittenTTS (needs a named voice),
    OuteTTS-1.0-mlx, Qwen3-TTS-VoiceDesign (needs a voice-design prompt), Voxtral-TTS.
  - **Speaker diarization** (MOSS-Transcribe-Diarize — gated repo, needs auth; VibeVoice-ASR) —
    who-said-what + timestamps, a new capability. Plus **speech enhancement** (DeepFilterNet /
    MossFormer2-SE) to denoise mic input before STT, and **endpoint detection** (Smart Turn) for
    better turn-taking than the silence-based VAD recorder.

- **Expressive / emotion-controllable voices (future).** Kokoro/OuteTTS give natural prosody but no
  emotion knob. **Chatterbox** (above) is the most promising path — it has an intensity param and runs
  natively on MLX. Heavier alternatives stay PyTorch/GPU-leaning: instruction-prompted (CosyVoice 2,
  Parler-TTS, Qwen3-TTS VoiceDesign), tag-based (Orpheus-3B, Dia non-verbals). A deliberate later
  add-on, not a replacement for the Kokoro baseline.

## Other open ideas

- **CLI chat export.** Done for the interactive TUI (`/export [file]` writes the transcript to
  Markdown). Still open: PDF export (the web UI has it) and a non-interactive `kodo chat --save`
  for the `-p` one-shot path.
- **Rich tags via a tag registry (future).** Keep the current key insight: assignments stay
  **string references** (`{model: [tag_names]}` in `tags.json`). To make tags first-class (custom
  color/description/icon/grouping), add a **separate normalized registry** keyed by tag name
  (`{tag: {color, …}}`), NOT per-model tag objects. Non-breaking: a `GET /api/tags/registry`
  feeds the UI, which prefers a registry color, else the derived one. Do this only once there's a
  second registry field to justify it (YAGNI) — derived color covers today's case. Pairs with a
  small color-picker / `kodo library tag --color`. Also: a curated default tag set seeded from
  `docs/guides/models.md`.
- **Format-centric shared library (the big refactor).** The dedup'd, format-keyed library
  described in `CLAUDE.md` under "Formats, runtimes & the shared library" — one canonical copy
  per (model × format), installed into whichever runtime, instead of a tree per source.
- Auto-fetch HF model cards for LM Studio models (infer the repo from the path).
- A "want list" / sync command to (re-)download a declared set of models.
- Verify/repair: re-check sizes & checksums against metadata.

## North-star roadmap

End goal: a **local, self-hosted DHIS2 assistant** — your own model + DHIS2 tools in a
Chrome side-panel:

```
Chrome extension (side panel, shadcn chat)
  → kodo (serve --ui --model X): runs the model + MCP client + agent loop
      → MCP server from ../dhis2w-utils  → DHIS2 instance
```

The DHIS2 MCP side is built in `~/dev/local/dhis2w-utils` (uv workspace):

- **`dhis2w-mcp-bridge`** — one tool `dhis2_cli(args, profile)` shelling out to `d2w`; for
  small local models. The default target for kodo + a small model. (Wired + verified against
  the `play42` profile via `kodo mcp add dhis2`.)
- **`dhis2w-mcp-router`** — 2 meta-tools (`search_tools`/`call_tool`), lazy typed discovery,
  single guarded chokepoint + **read-only mode**.
- **`dhis2w-mcp`** — full ~304 typed tools (big-context hosts).
- `dhis2w-browser` — Playwright DHIS2 automation (for the extension's later "act on the page"
  tier).

**Build order:**

1. **Phase 1 — kodo + web chat UI + generic tool/MCP support** (agent loop + MCP client,
   pointable at any MCP server; `serve --ui` and locked `serve --ui --model X` with CORS).
   Essentially complete.
2. **Phase 2 — DHIS2 + Chrome extension** [next]: point kodo's MCP client at
   `dhis2w-mcp-bridge`/`-router` (bridge wiring done); package the chat UI as the MV3 side-panel
   extension against the locked `/v1`.
3. Later: extension page-context, then page-actions (via `dhis2w-browser`).
