# Project rules

1. **No emojis.** Never use emojis anywhere — code, comments, docs, commit messages, PR descriptions, chat output.
2. **No Claude Code attribution.** Do not add `Co-Authored-By: Claude ...`, "Generated with Claude Code", or any similar attribution to commits, PRs, or files.
3. **Conventional Commits** for all git activity — commit messages, branch names, and PR titles.
   - Format: `<type>(<scope>)?: <description>` (e.g. `feat(ci): add docker publish workflow`, `fix(main): correct db path creation`).
   - Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`, `build`, `perf`, `style`, `revert`.
   - Branch names: `<type>/<short-description>` (e.g. `feat/makefile-and-ci`, `fix/sqlite-path`).
4. **Pydantic only, never `@dataclass`.** All data containers — config records, API response shapes, internal value types — must use `pydantic.BaseModel`. Reasons: consistent validation, JSON (de)serialization, FastAPI integration, predictable equality / repr. If you spot an existing `@dataclass` (legacy), convert it the next time you touch the file. Use `model_config = ConfigDict(frozen=True)` when you'd reach for `@dataclass(frozen=True)`.

## Goal

Build and maintain a **full local library of LLM models** by downloading /
backing up from **Hugging Face**, **Ollama**, and **LM Studio**. Browse the
library via a Typer CLI and a small FastAPI service; chat with any model, with
tool/MCP support. North star: a local, self-hosted DHIS2 assistant (see `ROADMAP.md`).

The library location is set via `KODO_LIBRARY_ROOT` (required — kodo refuses to
run without one rather than silently using a local folder). An external drive is
the intended home; moving to a different machine or drive is just a change to
`KODO_LIBRARY_ROOT`, no code changes required. Model weights must never be committed.

## Stack & conventions

- Python 3.13, `uv` (with the `uv_build` backend), **src layout**.
- **Typer** CLI; entry point `kodo = "kodo.cli:app"` (guarded via `kodo.cli:main`).
- **FastAPI + Pydantic + pydantic-settings** for the browse/serve layer.
- **huggingface_hub** is the canonical HF client (resumable, checksummed).
- Lint/type/test config mirrors `../../chap-sdk/chapkit`: ruff (120 cols,
  google docstrings), mypy + pyright (strict), pytest. Run `make lint` and
  `make test` before committing.

## Where things live (don't duplicate them here)

CLAUDE.md is durable rules + the mental model + non-obvious gotchas. Everything
else has a home — put detail there, not here:

- **`docs/architecture.md`** — the module map and internals: sources-vs-library,
  `ModelRef` identity + per-item scan fault isolation, serving/`ServerManager`/proxy,
  the one-parser-one-writer `kodo.toml`, the import-time HF-cache side effect, the
  process supervisor (group kill, pidfile, orphan sweep) and per-library `flock`.
- **`ROADMAP.md`** — forward-looking plans (north-star DHIS2 assistant, phased build
  order, open issues). Update it when plans change.
- **`CHROME.md`** — the Chrome/browser-extension design (side-panel client, `/api/chat`
  contract, CORS vs cross-site guard, live-session SameSite analysis).
- **`docs/`** (mkdocs site) — `getting-started.md`, `cli.md`, `benchmarks.md`, guides.
- **git history** — what shipped and when. Don't keep a changelog in this file.

## Libraries & projects (the mental model)

Two distinct, composable concepts (see `kodo.library.roots`):

- **A Library is a self-contained, portable store**: model files **plus their own
  metadata** (tags in `<root>/.kodo/tags.json`) — move the drive to another machine
  and the tags come along. Nothing about a library is "local" or "external"; it's just
  a *location*. The **default library** is `KODO_LIBRARY_ROOT` (per-machine config).
- **A Project (`./kodo.toml`) composes libraries + defines an assistant.** It lists
  `libraries = [...]` in priority order — project-relative paths plus the `@shared`
  token for the machine default — so it can keep hot models next to it *and* use the big
  archive. `[project]` (model + system prompt) and `[voice]` define the assistant; **tools
  live in `.mcp.json`** (standard `mcpServers` JSON, see below), not in `kodo.toml`. A project
  references models **by name**, never by path — so it's portable/committable. Outside a
  project, just the default library is used.

`library.scan()` reads across the resolved libraries (first match wins); each model
records its `library_root` so tags read/write against the right library. All **portable
data** — models, tags, runtime assets like the Kokoro TTS model (`<root>/tts/kokoro`) —
lives in a library so it travels with the drive. Two things live outside a library, and
they're different: **ephemeral machine-local runtime state** (`~/.kodo/runtimes/`, used by
`kodo.supervisor` to reap runtimes orphaned by a crashed kodo — a pid means nothing on another
machine, so it must not travel), and **durable machine config** (`~/.config/kodo/config.toml`
via `kodo.userconfig`, written by `kodo config` / `kodo setup`: the per-machine `library_root`
+ `default_model` defaults, the lowest-priority `Settings` source). Keep the three-way split:
portable → library; transient machine state → `~/.kodo`; machine defaults → `~/.config/kodo`.

## Library organization

Format-centric for LM Studio / HF; Ollama keeps its native (restorable) layout:

- `<root>/gguf/…`, `<root>/mlx/…`, `<root>/safetensors/<publisher>/<repo>/…` — **both**
  LM Studio and HF pull here (HF detects format from the Hub file list), so one canonical
  copy per `(model, format)` across sources.
- `<root>/huggingface/<repo_id>/…` — full snapshot; now only the **fallback** for a repo
  with no recognizable weights. The scanner still reads it, so older HF pulls keep working.
- `<root>/ollama/manifests|blobs/…` — Ollama's content-addressed store, so it stays
  restorable; shared blobs are preserved on `--move`.

`pull --move` deletes the local source after a verified (byte-for-byte) copy. Every pull
writes a `.kodo/` sidecar (`metadata.json` + `model-card.md`); for Ollama the card is
**generated** from the manifest's text layers (system prompt, template, params, license).

**exFAT** is the recommended filesystem (macOS + Linux read/write; eject cleanly, no
journaling). No symlinks/hardlinks on exFAT, so dedup is "store once, copy to each
runtime", never by link. Mount path differs on Linux — set `KODO_LIBRARY_ROOT` per machine.

## Formats & the shared-library direction

Default policy: keep **GGUF + MLX** (ready-to-run); safetensors on demand (the
convert/fine-tune source, 2-4x the quant). Format is a per-model choice, not "keep all".

- **GGUF** — cross-runtime quant (Ollama, LM Studio, llama.cpp; Mac + Linux). The portable
  backbone. **MLX** — Apple Silicon native (just HF repos, so the HF source covers them),
  fastest on the Mac. **safetensors** — original full-precision weights.
- Sharing reality: LM Studio reads loose GGUF/MLX directly; Ollama imports a GGUF into its
  own blob store and won't run a loose file in place. So the win is one canonical library
  copy we *install into* whichever runtime, not one file used live by all. Making Ollama /
  LM Studio / mlx_lm *consumers* fed from the canonical library is the larger open direction
  (see `ROADMAP.md`).

## UI — web-first, plus a Textual terminal chat

The browser is the primary, full-featured surface (library browse + chat), via
`kodo serve --ui`. The interactive terminal chat (`kodo chat`) is a **Textual TUI**
(`chat_tui.py`) reusing the same runtime + agent loop; `kodo chat -p` is a plain scripted
one-shot (no TUI).

- **`kodo serve --ui`** — full app: browse the library (grouped by format) + chat with any
  model (pick + switch).
- **`kodo serve --ui --model <name>`** — *locked* single-model mode: no picker, stable
  OpenAI `/v1`, configurable CORS. The intended backend for the Chrome extension.

Stack: **Vite + React + Tailwind v4 + shadcn/ui**, built with **Bun** (`bun` is the frontend
package manager + runner — `make frontend` runs `bun install && bun run build`; there is no npm
lockfile) to `frontend/dist` and served by `serve --ui` (API routes take precedence, SPA is the
catch-all). **One SPA, four surfaces**
(build the chat UI once, wrap it): web (`serve --ui`), Chrome extension (MV3 side panel,
locked `/v1`), and Tauri + Electron desktop wrappers (maneki's pattern; kodo's should also
launch/embed `kodo serve`). Chat UI uses shadcn's official chat components paired with a
**hand-rolled OpenAI SSE fetch loop** — our backends emit raw OpenAI SSE, which the Vercel
AI SDK / AI Elements / assistant-ui don't expect (impedance mismatch), so we avoid them.

## Running models — llama.cpp first, mlx_lm for MLX

Serving is OpenAI-compatible so any client (and our SPA) can attach. Runtimes are
**external processes kodo spawns**, not imported libs.

- **GGUF → llama.cpp `llama-server`** — primary, cross-platform, OpenAI `/v1`, tool calling
  (`--jinja` default), and a native router mode (`--models-dir`, hot-swap by name). A C++
  binary (`brew install llama.cpp`).
- **MLX → `mlx_lm.server` (text) / `mlx_vlm.server` (multimodal)** — Apple Silicon only.
  Vision-capable MLX checkpoints can't be loaded by text-only `mlx_lm` (it errors on the
  extra params and silently returns empty), so kodo routes them to `mlx-vlm` by the detected
  `vision` capability (`kodo.capabilities`). The MLX runtimes are an optional, platform-gated
  extra (`uv sync --extra mlx`) — no Linux wheels, so never hard deps; a missing one yields an
  install hint, not a hang.
- Ollama per-tensor models (e.g. `gemma4:12b-mlx`) need Ollama itself; single-GGUF Ollama
  models run via llama.cpp.

## Tools / MCP (required, even for kodo itself)

kodo supports **tool/function calling and acts as an MCP client**, generically (any MCP
server), not just DHIS2. llama-server does OpenAI-style tool calling (`--jinja`); kodo runs
the **agent loop** (model emits `tool_call` → kodo executes via the MCP client → feeds the
result back → continues), streamed to the chat UI. kodo owns the client + loop so every
surface (web, extension, CLI) stays thin and tools work uniformly.

**Config is the ecosystem-standard `mcpServers` JSON** (`kodo.mcpservers`), the same shape
Claude Desktop / Claude Code / Cursor use — so a server's README snippet pastes straight in.
Two levels **merge**: machine-global `~/.config/kodo/mcp.json` (what free-play chat gets;
`kodo mcp add --global`) and per-project `./.mcp.json` (`kodo mcp add`); a project name
overrides a global one, and CLI `--mcp` layers on top. `kodo.toml` no longer carries tools.
Bundled first-party servers (`kodo-mcp-*`, base deps) are entered by package name; `kodo setup`
seeds a minimal global default (`datetime`).

## Gotchas worth knowing

Non-obvious landmines that aren't self-evident from the code:

- **Voice.** Runtime is in-process mlx-audio (Apple) + Kokoro-ONNX (cross-platform, the
  lightweight chat voice). Dia seeds only apply via `mx.random.seed()` — `generate_audio`
  ignores a `seed` kwarg, so pin one or Dia varies every run. A leading `[S1]` degrades
  mlx-audio Dia (plain text is more reliable; keep nonverbal cues mid-line — a trailing one
  clips). Qwen3-TTS is unsupported (registry `supported=False`; mlx-audio can't load its
  speech tokenizer) and is rejected at the synthesis choke point.
- **Capabilities.** Tool detection requires a tool-*calling* marker (`tool_call` /
  `function_call` / `available_tools`), not a bare "tools" — the latter false-flagged audio
  specialists.
- **Two model families.** Chat (text in/out; some read vision/audio, some call tools) and
  Voice (TTS speaks, STT transcribes; audio in/out, not chat). Voice runs on demand, never in
  the chat runtime — the top-bar "No chat model" badge is Chat-only.

## Dev workflow

- `make check` is the CI gate (read-only, also run in `.github/workflows/ci.yml`); `make lint`
  mutates locally. Run `make lint` and `make test` before committing.
