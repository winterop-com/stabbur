# kodo roadmap

Forward-looking plans and ideas. Kept out of `CLAUDE.md` so it doesn't load
into every session's context. `CLAUDE.md` holds the durable project rules,
architecture, and conventions; this file holds what's next.

## Open / next ideas

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
- **Text / document attachments.** Inline pasted/dropped text files as context
  (not multimodal parts — just prepended/attached text). Simpler than image/audio.
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
    `kodo speak "text" [-o out.wav]` CLI (plays via `afplay` on macOS); a
    `POST /api/speak` endpoint returning WAV; and a **speaker/play icon on
    assistant replies** in the web UI (the "listen" affordance). Start with
    `--tts-oute-default` (auto-managed models) so no library changes are needed;
    add TTS-model detection + vocoder pairing to the library later.
  - **Alternatives (later):** Kokoro (ONNX; needs onnxruntime + kokoro-onnx — more
    voices/quality) and Orpheus-3B (Llama GGUF + a SNAC decoder — larger, more
    expressive). OuteTTS-via-llama-tts is the lowest-friction start.
  - **Done so far:** `kodo.tts` wrapper + `kodo speak` + `POST /api/speak` + the
    Listen button, over `llama-tts`/OuteTTS. Replies are now cleaned to prose
    before synthesis (`tts.speech_text` strips Markdown/code/URLs) so the model
    speaks words, not syntax.
  - **Real multi-voice — adopt Kokoro-82M (recommended).** The OuteTTS/`llama-tts`
    path has **one** default voice; more voices there would need `--tts-speaker-file`
    JSON profiles we don't have and can't easily generate. Investigation across
    Kokoro, Qwen3-TTS, and Dia-1.6B picked **Kokoro-82M** as the fit:
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
