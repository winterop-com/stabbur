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
`../kodo-projects/dhis2`), the full DHIS2 docs guide (`docs/guides/dhis2.md`), and now the
**write** suite + a write-enabled project. Recap:

1. **`tools-dhis2-write` suite — DONE.** Create / rename / delete against a **local** v42 instance
   (`local_basic` profile, `DHIS2_MCP_READONLY` off) — 7 self-cleaning lifecycle problems (each
   creates `KODO_`-prefixed objects then deletes them), graded basics→expert. There's a matching
   write-enabled starter: `kodo project new --template dhis2-write` (and a worked instance at
   `../kodo-projects/dhis2-write`). Results (5 tool-callers that all scored 11-12/12 on the READ
   suite):

   | Model | Write score | Residue left | Notes |
   |---|---|---|---|
   | gemma-4-12B-it-QAT | **4/7 (57%)** | 6 | best writer, and a small one |
   | Qwen3-Coder-30B-A3B | 3/7 (43%) | 7 | |
   | Qwen3.6-27B | 3/7 (43%) | 5 | re-run after the tool-timeout fix (was a hang, n/a) |
   | gpt-oss-20b | 2/7 (29%) | 5 | |
   | Ornith-1.0-9B | 1/7 (14%) | 6 | read winner; weak on writes |
   | Qwen3.6-35B-A3B | 1/7 (14%) | 8 | biggest model tested — **worst** writer, most residue |

   **Headline:** small local models drive DHIS2 **reads** near-perfectly but **writes are much
   harder** — the multi-step create→(rename/link)→delete→confirm lifecycle trips them up, and all
   left residue (incomplete deletes), swept between models. And **size does not help**: the best
   writer is the 12B gemma, while the two biggest (27B dense, 35B-A3B MoE) tie-or-lose and leave the
   most residue — bigger models over-generate, loop, and drop the completion protocol. Scoring is a
   proxy (`expect_tool` called + a `LIFECYCLE_OK` completion token), cross-checked against the actual
   DHIS2 state (residue-left as a cleanliness signal). Even the best (gemma-4-12B) is not yet
   trustworthy for unattended writes; the `dhis2-write` project keeps Ornith as its small default and
   notes gemma-4-12B as the stronger write driver. Next: stronger write models / a guarded write chokepoint
   (`dhis2w-mcp-router` read-only-by-default), and richer verification (assert real state, not just
   the completion token).

   **Tool-call hang — root-caused + fixed.** The Qwen3.6-27B stall (and the earlier gemma-31B one)
   was not the model: the agent loop ran MCP tool calls with **no timeout**, so a wedged tool (the
   DHIS2 bridge shelling out to `d2w`, which stalled on a request) blocked the loop indefinitely —
   llama-server sat idle at 0% CPU. The 600s httpx timeout only covered the model request, not tool
   execution. Fixed by bounding each tool call (`agent.run` `tool_timeout`, default 120s, passed to
   fastmcp's `call_tool(timeout=…)`): a timed-out tool now returns an error to the model and the
   loop continues. Big/MoE models just surfaced it more (slower, more elaborate tool calls). Large
   models weren't re-benchmarked for writes after the fix.

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
- **Dia self-contained on the drive.** [Done] `kodo.hfcache.configure()` (called from the
  package `__init__`, before hf_hub is imported) points `HF_HOME` at
  `<library_root>/.cache/huggingface`, so assets mlx-audio fetches by repo id — Dia's DAC codec
  (`mlx-community/descript-audio-codec-44khz`, 293MB) — land on the drive and travel with it.
  `kodo voice setup` seeds the codec there once (idempotent); verified that Dia's codec then
  resolves from the drive offline and a full Dia synth works. No-op if the user set
  `HF_HOME`/`HF_HUB_CACHE` or there's no real configured library.
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
- **Format-centric shared library.** **Storage done:** HF pulls are now format-centric
  (`gguf/`/`mlx/`/`safetensors/` via `huggingface.hub_format`), matching LM Studio — one copy per
  `(model, format)` on disk instead of a duplicate under `huggingface/`. **Consumers (lifecycle
  done):** `kodo library install <model> --to {ollama,lmstudio}` feeds a runtime from the canonical
  copy; `kodo library installed` shows which runtimes each model is fed into; `kodo library
  uninstall <model> --from {ollama,lmstudio}` reverses it (keeping the library copy). Ollama imports
  the GGUF (Modelfile → `ollama create`, a regenerable copy) / `ollama rm`; **LM Studio** gets a
  zero-copy symlink into the `gguf/`/`mlx/` bucket (which already matches LM Studio's
  `<publisher>/<repo>/<file>` layout; the link is on the machine disk, so exFAT's no-symlink limit
  doesn't apply). **Remaining:** `mlx_lm` (already runs loose MLX in place, so really just docs),
  and a per-model format policy (keep GGUF+MLX ready, safetensors on demand).
  **Migrate pass done:** `kodo library migrate` reorganizes an existing
  `huggingface/` tree into the format buckets (dry-run + `--apply`, dedups copies already in a
  bucket).
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
