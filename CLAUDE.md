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
library via a Typer CLI and a small FastAPI service.

Downloads currently land in the project-local `data/` directory. **Later this
moves to a dedicated 5TB WD Passport external drive** — when that happens, only
`KODO_LIBRARY_ROOT` changes (e.g. `/Volumes/kodo-5tb/library`); no code
changes required. `data/` is gitignored — model weights must never be committed.

## Stack & conventions

- Python 3.13, `uv` (with the `uv_build` backend), **src layout**.
- **Typer** CLI; entry point `kodo = "kodo.cli:app"`.
- **FastAPI + Pydantic + pydantic-settings** for the browse layer.
- **huggingface_hub** is the canonical HF client (resumable, checksummed).
- Lint/type/test config mirrors `../../chap-sdk/chapkit`: ruff (120 cols,
  google docstrings), mypy + pyright (strict), pytest. Run `make lint` and
  `make test` before committing.

## Layout

```
src/kodo/
├── config.py    # Settings (KODO_* env vars); library_root = data/ default
├── models.py    # ModelSource, ModelFormat, ModelEntry, Catalog, PullResult
├── catalog.py   # aggregates list/pull across sources
├── library.py   # scans the on-drive library (gguf/ mlx/ ...)
├── runtime.py   # serves a model (llama.cpp / mlx_lm)
├── cards.py     # model-card + metadata sidecar helpers
├── cli.py       # Typer app: chat / serve / doctor + library|audio|project groups
├── app.py       # FastAPI factory
├── routers/     # health.py, catalog.py
└── sources/     # base.py + huggingface.py / ollama.py / lmstudio.py
```

## Libraries & projects (the model)

Two distinct, composable concepts (see `kodo.library.roots`):

- **A Library is a self-contained, portable store**: model files **plus their own
  metadata** (tags in `<root>/.kodo/tags.json`) — so the whole thing travels (move
  the drive to another machine and the tags come along). Nothing about a library
  is "local" or "external"; it's just a *location*. The **default library** is
  `KODO_LIBRARY_ROOT` (per-machine config).
- **A Project (`./kodo.toml`) composes libraries + defines an assistant.** It lists
  `libraries = [...]` in priority order — paths relative to the project (e.g.
  `"models"`, a project-local store) plus the `@shared` token for the machine
  default — so a project can keep hot models next to it *and* use the big archive.
  Outside a project, just the default library is used. `[project]` (model name +
  system prompt) and `[[mcp]]` define the assistant. A project references library
  models **by name**, never by path — so it's portable/committable.

`library.scan()` reads across the resolved libraries, first match wins, and each
model records its `library_root` so tags read/write against the right library.
`kodo project init` scaffolds `kodo.toml` + a `models/` project-local library;
`kodo library pull` targets the project-local library by default (`--shared` for
the shared one). There is **no** global `~/.kodo/library` — that was removed;
`~/.kodo` is only a machine cache now (e.g. Kokoro TTS assets).

## Library organization

LM Studio / HF land in a **format-centric** layout; Ollama keeps its native
(restorable) layout:

- `<root>/gguf/<publisher>/<repo>/…` and `<root>/mlx/<publisher>/<repo>/…` —
  format-centric (LM Studio pulls; HF too once wired).
- `<root>/huggingface/<repo_id>/…` — full snapshot (HF pull, current behavior).
- `<root>/ollama/manifests/…` + `<root>/ollama/blobs/…` — Ollama's native
  content-addressed layout so it stays **restorable**; shared blobs are
  preserved on `--move`.

`pull --move` deletes the local source after a verified (byte-for-byte) copy.

## Model cards / instructions

Every pull writes a `.kodo/` sidecar (`metadata.json` + `model-card.md`):

- HF / LM Studio: the card is the existing `README.md`; metadata records it.
- Ollama: the card is **generated** from the manifest's text layers — system
  prompt, template, parameters, license (the info needed to run the model).
  Its sidecar lives at `data/ollama/.library/<model_tag>/`.

## Storage location

- Source code: stays on the Mac + GitHub (small, version-controlled).
- Model library: on a 5TB **exFAT** WD Passport, mounted `/Volumes/LLM` on this
  Mac → `KODO_LIBRARY_ROOT=/Volumes/LLM/Library` (set in gitignored `.env`).
  exFAT chosen for Mac + Linux read/write; allocation block 256 KB (good for
  large weights). No journaling — eject cleanly. No symlinks/hardlinks on the
  volume, so dedup must be by "store once, copy to each runtime", not by link.
  On Linux the mount path differs; set `KODO_LIBRARY_ROOT` per machine.

## Formats, runtimes & the shared library (intended direction)

The current code stores a separate tree per *source* (huggingface/ ollama/
lmstudio/), which duplicates weights. The intended direction is a **format-
centric, deduplicated library** keyed by `(model × format)`, sourced from HF,
with Ollama / LM Studio / mlx_lm as *consumers* fed from it:

- **GGUF** — cross-runtime quant (Ollama, LM Studio, llama.cpp; Mac + Linux).
  The portable backbone; the most shareable tier.
- **MLX** — Apple Silicon native (HF `mlx-community` repos; LM Studio on Mac,
  `mlx_lm`). Apple-only but fastest on the Mac. No separate downloader needed —
  MLX models are just HF repos, so the HF source already covers them.
- **safetensors** — original full-precision weights; the convert/fine-tune
  source. Large (2–4× the quant). Keep only for models we'll re-quantize or
  fine-tune, not blanket for every model.

Default library policy: keep **GGUF + MLX** (ready-to-run); safetensors on
demand. Format should be a per-model choice, not "keep all".

Sharing reality: LM Studio reads loose GGUF/MLX directly; Ollama imports a GGUF
into its own content-addressed blob store (`ollama create` / `ollama pull
hf.co/...`) and won't run a loose file in place. So the win is one canonical
library copy that we *install into* whichever runtime, not a single file used
live by all.

## UI — web-first, plus a Textual terminal chat

Decision: **the browser is the primary, full-featured surface** (library browse +
chat), via `kodo serve --ui`. The **terminal chat (`kodo chat`, interactive) is a
Textual TUI** (`src/kodo/chat_tui.py`) — a scrolling markdown transcript, a
multi-line input (Enter sends; Shift+Return / Ctrl-J / trailing backslash insert a
newline), live tool/reasoning activity, and a context footer. It reuses the same
runtime + agent loop; `kodo chat -p` stays a plain scripted one-shot (no TUI).

Note: an earlier decision dropped Textual entirely in favour of web-only; that was
**reversed** — Textual is the right tool for a terminal chat (a line-editor-plus
surface, not a full-screen app that fights the web direction), and is available for
other TUI surfaces later. The browser remains the canonical rich UI.

The web app's single entry point is `kodo serve --ui`:

- **`kodo serve --ui`** — full app: browse the library (grouped by format,
  pull/availability) + chat with any model (pick + switch).
- **`kodo serve --ui --model <name>`** — *locked* single-model mode: no picker,
  bound to one model, exposing a stable OpenAI endpoint. Intended as the backend
  for a **Chrome extension** later (so: configurable CORS for the extension
  origin; stable `/v1`).

Stack: **Vite + React + Tailwind v4 + shadcn/ui**, built to `frontend/dist` and
served by `serve --ui` (FastAPI mounts it; API routes take precedence, SPA is
the catch-all). Inspiration: `../../chap-sdk/chapkit/frontend/`.

**One SPA, four surfaces** (build the chat UI once, wrap it):
1. **Web** — `serve --ui` serves `frontend/dist`.
2. **Chrome extension** — MV3 side panel loads the same bundle (locked `/v1`).
3. **Desktop** — Tauri + Electron wrappers, following maneki's pattern
   (`~/dev/local/maneki/desktop/{tauri,electron,react}` — parallel wrappers
   loading one shared SPA that talks to the local server). For kodo the
   desktop app should ideally also launch/embed `kodo serve` so it's
   one-click, vs maneki's connect-to-any-server client model.

All surfaces point at kodo's local server; the SPA is the single shared UI.

Chat UI: shadcn's **official chat components (shipped 2026-06)** —
`MessageScroller`, `Message`, `Bubble`, `Attachment`, `Marker`
(`npx shadcn@latest add message-scroller message bubble attachment marker`).
They're backend-agnostic (bring-your-own data); `MessageScroller` owns the hard
streaming/auto-scroll UX. Pair with a **hand-rolled OpenAI SSE fetch loop** —
our backends emit raw OpenAI SSE, and the Vercel AI SDK / AI Elements /
assistant-ui all expect the AI-SDK stream format (impedance mismatch), so we
avoid them.

## Running models — llama.cpp first, mlx_lm for MLX

Serving is OpenAI-compatible so any client (and our SPA) can attach:

- **GGUF → llama.cpp `llama-server`** — primary, cross-platform, OpenAI `/v1`,
  built-in web chat UI, tool calling (`--jinja` default), experimental **MCP
  host** in its web UI, and a native **router mode** (`--models-dir`, hot-swap by
  model name) worth adopting for "one server, all GGUF".
- **MLX → `mlx_lm.server` (text) / `mlx_vlm.server` (multimodal)** — Apple
  Silicon only, OpenAI `/v1`. Multimodal MLX checkpoints (a `vision_config`;
  weights under `language_model.*`) can't be loaded by text-only `mlx_lm` — it
  errors on the extra params and the request silently returns empty — so kodo
  routes vision-capable MLX to `mlx-vlm`. Runtime is chosen by the detected
  `vision` capability (`kodo.capabilities`).
- Ollama new per-tensor models (e.g. `gemma4:12b-mlx`) aren't a single GGUF and
  need Ollama itself; single-GGUF Ollama models run via llama.cpp.

Runtimes are **external processes kodo spawns**, not imported libs. `llama-server`
is a C++ binary (`brew install llama.cpp`). The MLX runtimes are an optional,
platform-gated extra (`uv sync --extra mlx` / `make install-mlx`) — never hard
deps, since they have no Linux wheels. kodo finds them on `PATH`; a missing one
yields an install hint, not a hang.

`serve --ui` orchestrates: pick a model → FastAPI starts the right runtime and
proxies `/v1` so the SPA is single-origin.

## Tools / MCP (required, even for kodo itself)

kodo must support **tool/function calling and act as an MCP client** — this
is in scope for Phase 1, generically (any MCP server), not just DHIS2.

- llama-server does OpenAI-style tool calling (`--jinja`); kodo runs the
  **agent loop**: model emits `tool_call` → kodo executes it via the MCP
  client → feeds the result back → model continues. Streamed to the chat UI.
- The chat layer renders tool activity from the start (shadcn `Marker`/`Bubble`).
- kodo owns the MCP client + loop so every client (web UI, extension, CLI)
  stays thin and tools work uniformly.

## Roadmap

Forward-looking plans — the north-star DHIS2 assistant, the phased build order,
and open/next ideas — live in `ROADMAP.md`, not here, so they don't load into
every session's context. Update `ROADMAP.md` when plans change.

## Dev workflow

- `make check` is the CI gate (read-only); `make lint` mutates locally.
- Run `make lint` and `make test` before committing.
