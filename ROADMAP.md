# kodo roadmap

Forward-looking plans and ideas. Kept out of `CLAUDE.md` so it doesn't load
into every session's context. `CLAUDE.md` holds the durable project rules,
architecture, and conventions; this file holds what's next.

## Next up (open threads, as of 2026-07-03)

Concrete, agreed next actions — pick these up first.

1. **Free-play MCP tools (agreed direction, not built).** Installed MCP plugins
   (`datetime`, `utils`) are advertised but only a *project*'s `[[mcp]]` spawns them —
   free-play (`kodo serve` with no project) has zero tools, so a model can't even find
   today's date. Wire the advertised servers into free-play so they show in the Tools
   control, **`datetime` on by default** (pure/read-only), the rest available-but-off.
   (`benchmark` no longer advertises itself as an assistant tool — it's dev-only.)
2. **Rename `packages/kodo-mcp-benchmark` → `kodo-benchmark`.** It's a benchmarking
   tool (the `kodo benchmark` CLI), not an assistant MCP — the `mcp-` in the name is now
   a misnomer. Rename the dir, package (`kodo_mcp_benchmark` → `kodo_benchmark`), the
   entry point, and the workspace member/source in `pyproject.toml`. Do this **after**
   the repo rename to avoid churn-on-churn. Same question for `kodo-mcp-utils`/`datetime`
   (those *are* MCP servers, so their names are fine).
3. **DHIS2 MCP install docs.** Document installing `dhis2w-mcp` and `dhis2w-mcp-bridge`
   as project tools (per target model — big-context vs bridge), now that there's no
   Chrome extension yet. Started in `docs/guides/` — flesh out with the real commands.
4. **Voice as a project option.** Voice is orthogonal to the chat model (runs on demand),
   so it stays available in projects by default. Add optional `kodo.toml` knobs:
   `[voice] enabled = false` (hide the Voice surface for a pure-text assistant) and
   `[project] chat_voice = "kokoro:af_heart"` (pin the speak-replies voice).
5. **Repo rename `local-llm` → `kodo`.** Repo files (this, README, CLAUDE.md, docs, .env)
   travel with the rename; the `.claude` auto-memory (keyed by the old path) does not —
   copy `~/.claude/projects/-Users-morteoh-dev-local-local-llm/` →
   `…-local-kodo/` to keep memory + session history. `.env`'s `KODO_LIBRARY_ROOT` points
   at the drive (path-independent), so nothing else breaks. No hard-coded repo paths in src.
6. Smaller: rename the `ModelsView.tsx` file to `LibraryView.tsx` (it renders the Library
   now); a drawer-style sidebar for very narrow mobile widths.

## QA findings (2026-07-02 full run-through)

From a playwright pass over the whole library (see `docs/guides/models.md` for the
per-model matrix). Ordered roughly by impact:

- **Audio-specialist models don't process audio.** [STILL OPEN — High] gemma-4-12B
  transcribes audio fine, but Ultravox 500s (`image input is not supported`) and Voxtral
  silently ignores the audio. Likely a `llama-server` mmproj-routing issue for their
  audio-only projectors — needs a runtime/projector investigation. (No audio-specialist
  model is currently in the library to reproduce against.)
- **Capability-detection** (`kodo.capabilities`): [partly resolved 2026-07-03] the
  audio-specialist tools **false positive is fixed** — tool detection now requires a
  tool-*calling* marker (`tool_call`/`function_call`/`available_tools`), not a bare mention
  of "tools" (which Ultravox/Voxtral include in passing). The cross-format *vision* "mismatch"
  (e.g. Qwen3.5-4B: GGUF text-only vs MLX `vision_config` present) turned out to be a **real
  build difference**, not a detection bug — the MLX build genuinely ships a vision config, the
  GGUF doesn't — so detection is reading accurate metadata. No change warranted there.

Resolved in the 2026-07-03 pass:
- **Attachments dropped before caps load — FIXED.** `kindOf` now accepts image/audio
  optimistically while capabilities are still loading (`|| !accept.known`).
- **Broken MLX vision cryptic error — FIXED.** `friendlyRuntimeError` maps the raw
  tensor-key mismatch dump to "This MLX build couldn't be loaded … try the GGUF."
- **Favicon — FIXED.** `<link rel=icon>` + `public/favicon.svg` + a `/favicon.ico`
  route (browsers probe it regardless); `/favicon.ico` now 200s.
- Confirmed-good this run: model picker + filters, model switching, MLX text +
  tools, GGUF vision + audio (gemma), reasoning display, TTS/Listen (Kokoro),
  project system-prompt default, cross-site guard. Load speed is drive-bound
  (local ~2 s vs drive tens-of-seconds-to-minutes) — keep hot models on `local_root`.

## Open / next ideas

- **Projects as locked, purpose-built configs.** Today a project (`kodo.toml`) auto-loads
  its `[project].model` but still shows the full model picker and all tools — so opening a
  repo to "just play with voice" force-loads e.g. gemma4. Direction: a project should be a
  *specific assistant* — bind to its model (hide/lock the picker, like `serve --ui --model`),
  its tools, and its surfaces, so it's tailored to the use case (the DHIS2 assistant, etc.).
  Working *without* a project stays the free-play mode (all models, no auto-load) — this is
  the current testing setup (no `kodo.toml`; library via `KODO_LIBRARY_ROOT` in `.env`).
  Decide the UX: locked project vs. an unlocked "suggested default" a project can pick.

- **Voice as a first-class model category (TTS + STT).** Voice models do audio in/out, not
  next-token prediction, so they're their own category — distinct from audio-*input* chat
  LLMs (Voxtral/ultravox), which stay under the generative caps. Foundation landed:
  `kodo/voice/` with a **declarative registry** (`registry.py`) — one `VoiceModel` entry per
  model — and **discovery** (`catalog.py`) that finds each in the HF cache vs the library's
  `voice/` bucket. Design decisions:

  - **Voice modes** (the key axis, per how a TTS voice is chosen): `preset` (Kokoro's 54
    named voices, OuteTTS), `clone` (voice from a reference clip), `seeded` (a *new random
    voice each run* unless a seed is pinned — this is Dia's default; that's why "Dia sounds
    different every time"). Dia = seeded + cloneable + multi-speaker (`[S1]/[S2]`).
  - **Kokoro stays the in-chat voice** (`chat_default`): 82M/~340MB, runs happily *alongside*
    a large chat LLM. We do NOT load a 6GB voice model next to a 12GB LLM just to speak a
    reply. Heavy models (Dia, …) are for the standalone Voice section.
  - **Extensible by one entry**: adding a voice model is a `VoiceModel` in the registry (plus
    a runtime backend adapter only when its backend is new). Backends: `kokoro-onnx`
    (cross-platform, built-in), `mlx-audio` (Apple Silicon — Dia/Kokoro/Qwen3-TTS/Whisper),
    `llama-tts` (GGUF TTS + vocoder).

  Remaining phases (build after the current benchmark work; runtime needs serving):
  1. `kodo voice list` (done in module; wire the CLI) + `kodo voice import` — copy models
     from the HF cache into `<library>/voice/<repo>` so they're portable + organized, and
     dedup (the two 6GB Dia copies collapse to the MLX one).
  2. **Unify the store, keep category lenses (decided: option B).** The library is the
     *store* — it holds everything (LLMs + voice), not LLMs only. So the library scan gains
     a `voice/` bucket + voice-category detection (tts/stt sub-type, metadata from the
     registry), and `kodo library ls` shows a **Voice group** alongside the format groups —
     a 6GB Dia shouldn't be invisible to `library ls` when it's sitting in the library.
     `kodo library rm/tag` then cover voice models for free. Voice-*specific* verbs (import,
     synth, transcribe, voices) stay under `kodo voice`. Mirrors the web UI: one library, a
     Models section + a Voice section. Also fold the legacy `kodo audio` TTS group into
     `kodo voice speak` (keep an `audio` alias); reserve the word "audio" for the chat-LLM
     *input* capability (Voxtral/ultravox), distinct from the voice category.
  3. **mlx-audio runtime** (an external process kodo spawns, like llama-server) + OpenAI
     audio endpoints `/v1/audio/speech` (TTS) and `/v1/audio/transcriptions` (STT), so the
     SPA and any client use one standard.
  4. **Voice section in the web UI** (peer of Models): model cards, then a model-aware
     playground — Kokoro voice picker, Dia dialogue editor + nonverbal palette + clone-from-
     clip + seed control, Whisper drop/record → transcript.
  5. **Chat voice layer** done right: composer toggles for mic input (Whisper → prompt) and
     speak-replies (Kokoro by default), using whichever voice you set as default.
  6. Stretch: a `voice` benchmark suite — STT word-error-rate, and a TTS round-trip
     intelligibility check (TTS → Whisper → compare), plus RTF/latency (reuses the timing we
     already capture).

  **Status (2026-07-03):** phases 1–5 shipped — registry/import, unified library scan
  (`kodo voice`), in-process mlx-audio runtime (synth/clone/transcribe), OpenAI
  `/v1/audio/*` endpoints with **ffmpeg format export** (wav/mp3/opus/flac/ogg/aac), the
  web **Voice studio** (Playwright-verified), and the chat dictation mic + Kokoro
  speak-replies. See `docs/guides/voice.md`.

  Voice follow-ups:
  - **Qwen3-TTS support.** Currently flagged `supported=False` in the registry: mlx-audio's
    high-level `load_model` doesn't wire up its separate speech tokenizer (`Qwen3TTSSpeechTokenizer`
    lives in the repo's `speech_tokenizer/`), so `generate_audio` errors and returns nothing.
    Enable by loading the tokenizer and `model.load_speech_tokenizer(...)` before generating.
  - **Dia self-contained on the drive.** Dia loads its DAC codec (`mlx-community/descript-audio-codec-44khz`,
    293MB) from `~/.cache/huggingface`, not the library — mlx-audio hardcodes the repo id.
    For offline portability, point `HF_HUB_CACHE` at a drive dir **at process startup** (its
    cache constants are import-time; setting it late is a no-op) and seed it once from the cache.
  - **Richer audio UI.** [ElevenLabs UI](https://ui.elevenlabs.io/) is a shadcn/Tailwind audio
    component registry (waveform player, orb, etc.) on the same stack — a natural polish pass
    for the Voice studio's inline player and a chat voice-bar.
  - **Voice cloning in the Textual TUI.** Cloning is reachable from the web UI and the CLI
    (`kodo voice speak --model dia --ref-audio … --ref-text …`); add a TUI affordance too.

- **More MCP servers — the default "normal toolset".** The assistant should ship a
  small, dependable set of built-in tools beyond `datetime`. Each is its own workspace
  member following the `kodo-mcp-datetime` template (src layout, `__init__`+`__main__`+
  `app.py`), advertises itself via the `mcp_servers` plugin hook (so `kodo mcp list` /
  `--mcp <name>` / tool pickers pick it up with no hardcoding), and gets a matching
  `tools-<name>` benchmark suite (like `tools-datetime`) so its tool-calling is measured.

  Proposed set, roughly in priority order:

  1. **`kodo-mcp-fetch`** — fetch a URL and return readable text/markdown (grounding /
     "read this page"). httpx + a readability/markdownify step. **Security:** SSRF guard
     (block private/loopback/link-local IPs and non-http(s) schemes), size + redirect
     caps, timeout. Optional allowlist via config.
  2. **`kodo-mcp-search`** — web search returning titled snippets + URLs. Pairs with
     fetch (search → fetch the winner). Pluggable backend (DuckDuckGo HTML with no key,
     or Brave/Exa via a `KODO_SEARCH_*` key in pydantic-settings). Degrade with a clear
     hint when unconfigured, like the mlx/tts extras.
  3. **`kodo-mcp-exec`** — run a Python (later shell) snippet and return stdout: a
     calculator / scratchpad. **Reuse the benchmark's Docker sandbox** — extract
     `kodo_mcp_benchmark.core.run_code` into a shared `kodo-mcp-sandbox` lib both depend
     on (no network, capped mem/cpu/pids, timeout). Gated on Docker like the benchmark.
  4. **`kodo-mcp-files`** — list/read/search files under one configured workspace root,
     read-only by default. **Security:** contain every path with `safe_join` (the guard
     already in `sources/base.py`); never escape the root; opt-in writes behind a flag.
  5. **`kodo-mcp-memory`** — a tiny persistent notes / key-value store the assistant can
     read and write, saved *in the library* (travels with the drive, per the no-`~/.kodo`
     rule), so it has durable scratch memory across sessions.
  6. **`kodo-mcp-weather-yr`** — weather via yr.no (met.no). Good "real API" exemplar and
     already named as a wanted server.

  Cross-cutting: keep each server dependency-light and stdio-only; config via
  `pydantic-settings` (`KODO_*`); pure servers stay plain packages (advertise-only
  plugin, no `PluginContext`); anything that executes or fetches gets a sandbox/allowlist
  before it ships. A project's `kodo.toml` can then compose which servers its assistant
  turns on by default (vs. every server always-on), and the web UI tool picker lists them
  from the `mcp_servers` advertisements.

- **Terminal chat is a Textual TUI — DONE.** `kodo chat` (interactive) now runs a
  full-screen Textual app (`src/kodo/chat_tui.py`): scrolling markdown transcript,
  multi-line input (Enter sends; Shift+Return / Ctrl-J / trailing backslash =
  newline), collapsible reasoning ("thought for Ns"), live tool activity, and a
  context footer. `kodo chat -p` stays a plain scripted one-shot. This **reversed**
  the earlier "Textual dropped, web-only" decision (see CLAUDE.md) — Textual is the
  right layer for a terminal chat, and is now available for other TUI surfaces
  later (e.g. a `kodo tui` library browser) if we want them; the browser stays the
  canonical rich UI.

- **Models view + user tags — DONE.** A **Models** entry in the web sidebar opens
  a full-panel card grid (grouped by format, like `kodo library ls`): each card shows
  format, size, capabilities, context, and its user tags; clicking loads the model
  and drops into chat. Tags are **user metadata** (distinct from the auto-detected
  `vision`/`audio`/`tools` caps), stored in one library-level index on the
  always-local root (`<local_root>/.kodo/tags.json`) so they survive the drive
  being offline. Editable inline on each card and via the CLI (`kodo library tag <model>
  --add tested --remove broken`, `kodo library tag <model>` to list, `--clear`); shown in
  `kodo library ls -d` and as **filter chips** atop the Models grid (AND filter). Served on
  each `/api/library` model + `POST /api/tags` to set. Chip **color is derived** from
  the tag name (stable hash into a fixed palette) — so every `tested` is the same
  color everywhere, zero storage. Tag **filter chips** appear both atop the Models
  grid and in the in-composer model picker (AND filter, alongside the capability
  chips). *Still open:* a curated default tag set / seeding from
  `docs/guides/models.md`.

- **Rich tags via a tag registry (future).** Keep the current design's key insight:
  assignments stay **string references** (`{model: [tag_names]}` in `tags.json`) —
  simple, greppable, what the CLI writes. To make tags first-class (custom color,
  description, icon, grouping), add a **separate, normalized registry** keyed by tag
  name (`{tag: {color, description, ...}}`), NOT per-model tag objects (which would
  duplicate metadata across every model and let colors drift). This is fully
  **non-breaking**: the `tags: string[]` wire type and assignments are untouched; a
  new registry endpoint (`GET /api/tags/registry`) feeds the UI, which prefers a
  registry color when present and falls back to the derived one. Pairs with a small
  color-picker / tag-manager UI and, on the CLI, `kodo library tag --color`. Do this only
  once there's a second registry field to justify it (YAGNI) — derived color covers
  the common case today.

- **Image attachments — DONE (web + CLI).** Drag/paste/pick images into the
  composer (gated on the model's detected `vision` capability), rendered as
  thumbnails and click-to-fullscreen in the thread; `kodo chat --image/-i` on the
  CLI. Sent as OpenAI `image_url` content parts; `agent.user_content` builds them.
- **Audio input — DONE (web + CLI).** Attach audio to audio-capable models: an
  `audio` capability detected from the GGUF mmproj (`clip.has_audio_encoder`) or a
  config `audio_config`, the composer/CLI accept audio (drag/paste/pick,
  `kodo chat --audio`, REPL drag-drop), sent as OpenAI `input_audio` parts.
  Verified with ggml-org Ultravox-1B (transcribes a clip); the gemma-4-12B GGUF
  is both vision and audio.
- **Text / document attachments — DONE (web).** Drop/paste/pick text & code files
  (matched by MIME or extension) with any model — their contents inline into the
  prompt as fenced blocks (a plain string, not multimodal parts), so even a
  non-multimodal model reads them; the message shows a filename chip, not the raw
  text. **CLI parity done:** drag a text/code file into the `kodo chat` REPL and
  its path is detected + inlined (same as image/audio drag-drop) — no flag needed.
- **Text-to-speech (TTS) — SPIKE DONE; recommend OuteTTS via `llama-tts`.**
  Finding: llama.cpp ships **`llama-tts`** (already on this machine via brew), and
  it generates speech end-to-end from the existing runtime family — no new engine.
  Verified PoC: `llama-tts --tts-oute-default -p "..." -o out.wav` produced a
  3.56s / 24 kHz WAV in ~2 s. A TTS model here is a small GGUF (~400 MB OuteTTS)
  paired with a vocoder GGUF (~130 MB WavTokenizer); `--tts-oute-default`
  auto-fetches both.
  - **Fit:** reuses kodo's "spawn a llama.cpp binary" pattern, but `llama-tts` is
    a **one-shot CLI** (writes a WAV), not an OpenAI server — so the plumbing is a
    subprocess wrapper (text → WAV bytes), not a `/v1` proxy.
  - **Proposed first cut:** a `kodo.tts` wrapper around `llama-tts`; a
    `kodo audio speak "text" [-o out.wav]` CLI (plays via `afplay` on macOS); a
    `POST /api/speak` endpoint returning WAV; and a **speaker/play icon on
    assistant replies** in the web UI (the "listen" affordance). Start with
    `--tts-oute-default` (auto-managed models) so no library changes are needed;
    add TTS-model detection + vocoder pairing to the library later.
  - **Alternatives (later):** Kokoro (ONNX; needs onnxruntime + kokoro-onnx — more
    voices/quality) and Orpheus-3B (Llama GGUF + a SNAC decoder — larger, more
    expressive). OuteTTS-via-llama-tts is the lowest-friction start.
  - **Done so far:** `kodo.tts` wrapper + `kodo audio speak` + `POST /api/speak` + the
    Listen button, over `llama-tts`/OuteTTS. Replies are now cleaned to prose
    before synthesis (`tts.speech_text` strips Markdown/code/URLs) so the model
    speaks words, not syntax.
  - **Real multi-voice — Kokoro-82M. [done]** Added as an optional, cross-platform
    engine (`make install-tts` → `kokoro-onnx` + `onnxruntime` + `soundfile`;
    espeak-ng bundled via `espeakng-loader`, no system dep). `kodo.kokoro`
    auto-fetches the fp32 model (~310 MB, faster + better than int8 on CPU) into
    the always-local library, exposes the **54 built-in voices** (`GET /api/voices`,
    `kodo audio voices`), and routes `POST /api/speak` / `kodo audio speak -v <voice>` to it.
    The web settings "Voice" control is now a real picker grouped by language;
    OuteTTS/`llama-tts` stays as a fallback engine. The original evaluation
    (Kokoro vs Qwen3-TTS vs Dia-1.6B) that led here:
    - **Fully local**, Apache-2.0, tiny (quantized ONNX ~80 MB + a ~27 MB voices
      file). **54 built-in named voices, no reference audio needed** — a real
      "pick a voice" list (e.g. `af_heart`, `am_michael`, `bf_emma`, `jf_alpha`,
      `zf_xiaobei`; prefix = language+gender), across 9 languages (en-US/en-GB,
      ja, zh, es, fr, hi, it, pt-BR). **CPU-friendly (~2x realtime), one
      onnxruntime backend on both Apple Silicon and Linux** — no per-platform fork.
    - **Plan:** a platform-gated `--extra tts` (`kokoro-onnx` + `onnxruntime` +
      `soundfile`; `espeak-ng` as a system dep with a PATH hint, mirroring our
      "missing runtime → hint, not hang" pattern). Pull the two ONNX assets into
      the library (HF-sourced, fits the existing pull model). Add a second TTS
      engine alongside `llama-tts`: expose the fixed voice list (`GET` voices →
      `{id, name, language, gender}`), turn the settings "Voice" select into a
      real voice picker, and pass `voice_id` through `/api/speak` to
      `Kokoro(...).create(text, voice=voice_id)`. Keep OuteTTS as a fallback.
    - **Rejected/deferred:** **Qwen3-TTS** (viable but heavier — PyTorch/GPU or an
      MLX split, only 9 preset voices; keep as a fallback if we later want voice
      cloning / voice design / Korean-German-Russian). **Dia-1.6B** rejected for a
      voice picker (GPU-only ~10 GB, English-only, no named voices — needs an audio
      prompt for cloning + `[S1]`/`[S2]` dialogue tags). **Supertonic** — watch
      (CPU-speed alternative; Kokoro still wins on CPU quality).
  - **Expressive / emotion-controllable voices (future).** Kokoro (and OuteTTS)
    give natural prosody but **no emotion knob** — each voice has a fixed style;
    you can't ask for "angry" or "happy". Real emotion control needs a heavier
    class of model: **instruction-prompted** (CosyVoice 2, Parler-TTS, Qwen3-TTS
    VoiceDesign — "say this sadly"), **tag-based** (Orpheus-3B with `<laugh>` /
    `<sigh>`; Dia non-verbals + emotional dialogue), or **intensity-controlled**
    (Chatterbox's exaggeration param). All are PyTorch, mostly GPU-leaning, and
    less cross-platform than Kokoro — so this is a deliberate later add-on for
    when expressiveness matters more than the lightweight local footprint, not a
    replacement for the Kokoro baseline.
- **Chat session export — Markdown + PDF.** [web UI done] Top-bar download menu
  exports the open conversation to **Markdown** (source-form: roles, code fences,
  reasoning, tool activity, model + params header) or **PDF** (a styled,
  self-contained HTML document opened in a print window for "Save as PDF" — no
  heavy dep; `renderToStaticMarkup` is lazy-loaded). Both are pure client-side
  from conversation state. *Still open:* a `kodo` CLI export — conversations are
  browser-only today, so this needs the REPL to persist transcripts first (e.g. a
  `/export` slash command or `kodo chat --save`).
- **Mermaid diagram rendering in Markdown.** [done] ```` ```mermaid ```` fences
  render as diagrams (mermaid.js, lazy-loaded into its own chunk so it only loads
  when a diagram appears), theme-aware (re-renders on light/dark toggle), with a
  source/diagram toggle + copy and a graceful fallback to the source on invalid
  syntax. PDF export renders diagrams to inline SVG; Markdown export keeps the
  fenced source.
- **Projects (assistant definitions)** — two units: the *global* **library**
  (models on the drive) vs a *local* **project** (`./kodo.toml`: a library
  model + MCP servers + system prompt + serve settings). Projects make assistants
  reproducible/shareable; the north-star DHIS2 assistant is just a project. Keep
  the project file a thin manifest, not a framework.
- **`kodo init`** — scaffold a project in the cwd and ensure its model is in the
  library; when undecided, offer a curated 2-3 tiny starter models (compact
  GGUF, MLX for Apple Silicon, a tool-capable one). On-ramp: clone → `kodo init` →
  `kodo serve --ui`. Idempotent: pull only models missing from the library; no
  cwd/`~/.config` "ran" flag (any optional marker lives in `<library_root>/.kodo/`).
- Refactor toward the format-centric shared library (the big one) described in
  `CLAUDE.md` under "Formats, runtimes & the shared library".
- Auto-fetch HF model cards for LM Studio models (infer repo from path).
- A "want list" / sync command to (re-)download a declared set of models.
- Verify/repair: re-check sizes & checksums against metadata.

## North-star roadmap

End goal: a **local, self-hosted DHIS2 assistant** — your own model + DHIS2 tools
in a Chrome side-panel:

```
Chrome extension (side panel, shadcn chat)
  → kodo (serve --ui --model X): runs the model + MCP client + agent loop
      → MCP server from ../dhis2w-utils  → DHIS2 instance
```

The DHIS2 MCP side is already built in `~/dev/local/dhis2w-utils` (uv workspace):

- **`dhis2w-mcp-bridge`** — one tool `dhis2_cli(args, profile)` shelling out to
  `d2w`; built for small local models (8k context, progressive `--help`). The
  default target for kodo + a small model.
- **`dhis2w-mcp-router`** — 2 meta-tools (`search_tools`/`call_tool`), lazy typed
  discovery, single guarded chokepoint + **read-only mode** (gates DHIS2 writes).
- **`dhis2w-mcp`** — full ~304 typed tools (big-context hosts).
- `dhis2w-browser` — Playwright DHIS2 automation (relevant to the extension's
  later "act on the page" tier).

**Build order (decided):**

1. **Phase 1 — finish kodo + web chat UI**, including generic tool/MCP support
   (agent loop + MCP client, pointable at any MCP server). `serve --ui` and
   `serve --ui --model X` (locked, extension-ready, CORS).
2. **Phase 2 — DHIS2 + Chrome extension**: point kodo's MCP client at
   `dhis2w-mcp-bridge`/`-router`; package the chat UI as the side-panel extension
   against the locked `/v1`.
3. Later: extension page-context, then page-actions (via `dhis2w-browser`).
