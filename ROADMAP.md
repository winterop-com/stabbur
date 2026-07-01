# kodo roadmap

Forward-looking plans and ideas. Kept out of `CLAUDE.md` so it doesn't load
into every session's context. `CLAUDE.md` holds the durable project rules,
architecture, and conventions; this file holds what's next.

## Open / next ideas

- **Image attachments — DONE (web + CLI).** Drag/paste/pick images into the
  composer (gated on the model's detected `vision` capability), rendered as
  thumbnails and click-to-fullscreen in the thread; `kodo chat --image/-i` on the
  CLI. Sent as OpenAI `image_url` content parts; `agent.user_content` builds them.
- **Audio input — next multimodal step.** Same shape as images, for audio-capable
  models (Gemma 3n, Qwen2-Audio, Ultravox, Voxtral). Work: (1) detect an `audio`
  capability (`audio_config` in the model config) alongside `vision`; (2) accept
  audio files in the composer (drag/pick) and a `kodo chat --audio` flag;
  (3) send as OpenAI `input_audio` content parts (base64 + format) — extend
  `agent.user_content`. Runtimes already support it: llama-server via `mtmd`
  (`--mmproj`) and mlx-vlm ("images, audio, and text"). Note: two Gemma 3n MLX
  models already in the library carry `audio_config`, but the E4B checkpoint
  currently fails to load in mlx-vlm (`vision_tower` mismatch) — verify against a
  known-good audio model before claiming end-to-end support.
- **Text / document attachments.** Inline pasted/dropped text files as context
  (not multimodal parts — just prepended/attached text). Simpler than image/audio.
- **Investigate text-to-speech (TTS) models.** Audio *output* — a new modality
  for the library, distinct from audio input above. Candidates to evaluate:
  Kokoro (tiny, fast, ONNX/GGUF), Orpheus-TTS (Llama-based → GGUF via llama.cpp +
  a SNAC decoder), Sesame CSM, Dia, Parler-TTS, Piper. Open questions: runtime
  (most aren't llama.cpp chat servers — they need their own serving path / a
  decoder step), how they fit the `(model x format)` library model, and the UI
  surface (a "speak" action on assistant replies, or a dedicated TTS view). Start
  with a spike on Kokoro or Orpheus to gauge how much new runtime plumbing a
  non-chat model needs before committing.
- **Chat session export — Markdown + PDF.** Export a conversation from the web
  UI (and a `kodo` CLI command) to a shareable file. Markdown first (messages,
  roles, code fences, tool activity, model + params header) — straightforward
  from the client-side conversation state. PDF second (print-to-PDF of a styled
  Markdown render, or a headless renderer) if it can be done without a heavy dep.
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
