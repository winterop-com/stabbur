# kodo roadmap

Forward-looking plans and ideas. Kept out of `CLAUDE.md` so it doesn't load into
every session's context. `CLAUDE.md` holds the durable project rules, architecture,
and conventions; this file holds what's **next**. Completed work is removed (git
history is the record) — this file is only open threads.

## Next up (concrete, as of 2026-07-04)

1. **Rename `packages/kodo-mcp-benchmark` → `kodo-benchmark`.** It's a benchmarking
   tool (the `kodo benchmark` CLI), not an assistant MCP — the `mcp-` in the name is a
   misnomer. Rename the dir, package (`kodo_mcp_benchmark` → `kodo_benchmark`), the entry
   point, and the workspace member/source in `pyproject.toml`. (`kodo-mcp-utils`/`-datetime`
   /`-search`/`-web` are genuine MCP servers — their names are fine.)
2. **Surface uninstalled optional MCP servers in the web health menu**, mirroring what
   `kodo project show` now does (a project listing `web` without `--extra web` reports
   `failed: … make install-web` instead of silently 0 tools). Also: warn in `kodo project
   init` / `kodo mcp add` when adding `web` without the extra installed.
3. **Extend the Textual TUI command palette further.** The palette (`Ctrl+P`), `/` slash-command
   autocomplete, and MCP enable/disable/reconnect have shipped. Still keyboard-only-missing vs the
   web UI: change the **speak-replies (chat) voice**, switch model, adjust sampling
   (temperature/top-p/top-k/…), export the transcript. Reuse the same `/api`-equivalent logic the
   web UI calls so behavior stays consistent across surfaces.
4. Smaller: rename `ModelsView.tsx` → `LibraryView.tsx` (it renders the Library now); a
   drawer-style sidebar for very narrow mobile widths.

### Near-term polish queue (proposed 2026-07-04, ranked)

| # | Item | Size | Why now |
|---|------|------|---------|
| 1 | Rename `kodo-mcp-benchmark` → `kodo-benchmark` | S | The `mcp-` is a misnomer (it's the `kodo benchmark` CLI, not an assistant MCP). Pure rename: dir, package, entry point, workspace member. Low-risk cleanup. |
| 2 | Finish the TUI palette — voice picker, model switch, sampling knobs, `/export` transcript | M | Natural continuation of the palette we shipped; closes the keyboard-parity gap with the web UI. |
| 3 | Surface uninstalled optional MCP servers in web health + warn in `kodo project init` / `mcp add` | M | Mirrors what `kodo project show` already does; stops `web` silently reporting 0 tools when `--extra web` is missing. |
| 4 | New MCP server: `kodo-mcp-memory` — persistent notes in the library | M | High assistant value, self-contained, dependency-light. `-exec`/`-files` need a sandbox first. |
| 5 | Rename `ModelsView.tsx` → `LibraryView.tsx` + mobile drawer sidebar | S | Small frontend tidy-up. |

## DHIS2 assistant — near-term

The north star (bottom of this file) is the local DHIS2 assistant. Concrete next steps:

1. **`tools-dhis2` benchmark suite — pick the best model for the bridge.** A tool-use suite
   (`packages/kodo-mcp-benchmark/.../suites/tools-dhis2.toml`) that attaches `dhis2w-mcp-bridge`
   against the **play42** profile and scores whether a model calls `dhis2_cli` and returns the
   right answer. **Read-only first** (metadata counts, UID/name resolution, version, system name —
   stable structural facts about the Sierra Leone demo); the ground-truth values are a snapshot of
   the play42 dev instance and may need a refresh if it's reset. **Then a write suite** (create /
   update / delete against a throwaway/local instance, `DHIS2_MCP_READONLY` off) to see which
   models can safely drive mutations. Run with `kodo benchmark run tools-dhis2 --all --save` and
   fold the winner into the DHIS2 project's `[project].model`.
2. **`kodo project new --template dhis2` (a DHIS2 starter).** Scaffold a ready-to-run DHIS2
   assistant project: `kodo.toml` with the bridge `[[mcp]]` block, a DHIS2 system prompt, the
   recommended model — **and** scaffold a profiles file (`.dhis2/profiles.toml` project-local, or a
   pointer to `~/.config/dhis2/profiles.toml`) with a commented template so a new user fills in
   base URL + token and runs. Wizard prompts for base URL / profile name / token. Pairs with the
   `--git` flag already on `project new/init`.
3. **Full DHIS2 docs guide.** Expand `docs/guides/dhis2-project.md` (or a new `guides/dhis2.md`)
   into a complete guide: creating a DHIS2 profile (`d2w` profiles, base URL + PAT/token), the
   three bridge tiers (bridge / router / full), read-only vs write, and **plenty of copy-paste
   suggested prompts** (metadata counts, name->UID, analytics, tracker) — linking the official
   dhis2w-utils docs at https://winterop-com.github.io/dhis2w-utils/.

## Open issues

- **Audio-specialist models don't process audio.** [High] gemma-4-12B transcribes audio
  fine, but Ultravox 500s (`image input is not supported`) and Voxtral silently ignores the
  audio — likely a `llama-server` mmproj-routing issue for their audio-only projectors; needs
  a runtime/projector investigation. (No audio-specialist model is currently in the library to
  reproduce against.)

## Internal MCP servers — the remaining "normal toolset"

`datetime`, `utils`, `search` (bundled) and `web` (optional `--extra web`) have shipped.
Each new one is its own workspace member following the `kodo-mcp-datetime` template
(src layout, `__init__`+`__main__`+`app.py`+`plugin.py`), advertises via the `mcp_servers`
plugin hook (so `kodo mcp list` / `mcp add` / tool pickers pick it up with no hardcoding),
and gets a `tools-<name>` benchmark suite. Remaining, roughly in priority order:

1. **`kodo-mcp-exec`** — run a Python (later shell) snippet and return stdout: a calculator /
   scratchpad. **Reuse the benchmark's Docker sandbox** — extract `kodo_mcp_benchmark.core
   .run_code` into a shared `kodo-mcp-sandbox` lib both depend on (no network, capped
   mem/cpu/pids, timeout). Gated on Docker like the benchmark.
2. **`kodo-mcp-files`** — list/read/search files under one configured workspace root,
   read-only by default. **Security:** contain every path with `safe_join` (the guard already
   in `sources/base.py`); never escape the root; opt-in writes behind a flag.
3. **`kodo-mcp-memory`** — a tiny persistent notes / key-value store the assistant can read
   and write, saved *in the library* (travels with the drive, per the no-`~/.kodo` rule), so it
   has durable scratch memory across sessions.
4. **`kodo-mcp-weather-yr`** — weather via yr.no (met.no). A good "real API" exemplar.

Cross-cutting: keep each server dependency-light and stdio-only (heavy ones optional behind an
extra, like `web`); config via `pydantic-settings` (`KODO_*`); pure servers stay plain packages
(advertise-only plugin, no `PluginContext`); anything that executes or fetches gets a
sandbox/allowlist before it ships.

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

- **CLI chat export.** The web UI exports a conversation to Markdown/PDF; the REPL has no
  transcript to export yet. Needs the REPL to persist transcripts first (a `/export` slash
  command or `kodo chat --save`).
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
