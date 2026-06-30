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
`LOCAL_LLM_BACKUP_ROOT` changes (e.g. `/Volumes/llm-5tb/library`); no code
changes required. `data/` is gitignored — model weights must never be committed.

## Stack & conventions

- Python 3.13, `uv` (with the `uv_build` backend), **src layout**.
- **Typer** CLI; entry point `local-llm = "local_llm.cli:app"`.
- **FastAPI + Pydantic + pydantic-settings** for the browse layer.
- **huggingface_hub** is the canonical HF client (resumable, checksummed).
- Lint/type/test config mirrors `../../chap-sdk/chapkit`: ruff (120 cols,
  google docstrings), mypy + pyright (strict), pytest. Run `make lint` and
  `make test` before committing.

## Layout

```
src/local_llm/
├── config.py    # Settings (LOCAL_LLM_* env vars); backup_root = data/ default
├── models.py    # ModelSource, ModelFormat, ModelEntry, Catalog, PullResult
├── catalog.py   # aggregates list/pull across sources
├── library.py   # scans the on-drive library (gguf/ mlx/ ...)
├── runtime.py   # serves a model (llama.cpp / mlx_lm)
├── cards.py     # model-card + metadata sidecar helpers
├── cli.py       # Typer app: list / pull / library / run / serve
├── app.py       # FastAPI factory
├── routers/     # health.py, catalog.py
└── sources/     # base.py + huggingface.py / ollama.py / lmstudio.py
```

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

Every pull writes a `.local-llm/` sidecar (`metadata.json` + `model-card.md`):

- HF / LM Studio: the card is the existing `README.md`; metadata records it.
- Ollama: the card is **generated** from the manifest's text layers — system
  prompt, template, parameters, license (the info needed to run the model).
  Its sidecar lives at `data/ollama/.library/<model_tag>/`.

## Storage location

- Source code: stays on the Mac + GitHub (small, version-controlled).
- Model library: on a 5TB **exFAT** WD Passport, mounted `/Volumes/LLM` on this
  Mac → `LOCAL_LLM_BACKUP_ROOT=/Volumes/LLM/Library` (set in gitignored `.env`).
  exFAT chosen for Mac + Linux read/write; allocation block 256 KB (good for
  large weights). No journaling — eject cleanly. No symlinks/hardlinks on the
  volume, so dedup must be by "store once, copy to each runtime", not by link.
  On Linux the mount path differs; set `LOCAL_LLM_BACKUP_ROOT` per machine.

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

## UI — web-first (Textual dropped)

Decision: **one browser interface for everything.** Textual/TUI is dropped. The
single entry point is `llm serve --ui`:

- **`llm serve --ui`** — full app: browse the library (grouped by format,
  pull/availability) + chat with any model (pick + switch).
- **`llm serve --ui --model <name>`** — *locked* single-model mode: no picker,
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
   loading one shared SPA that talks to the local server). For local-llm the
   desktop app should ideally also launch/embed `local-llm serve` so it's
   one-click, vs maneki's connect-to-any-server client model.

All surfaces point at local-llm's local server; the SPA is the single shared UI.

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
- **MLX → `mlx_lm.server`** — Apple Silicon only, OpenAI `/v1`.
- Ollama new per-tensor models (e.g. `gemma4:12b-mlx`) aren't a single GGUF and
  need Ollama itself; single-GGUF Ollama models run via llama.cpp.

`serve --ui` orchestrates: pick a model → FastAPI starts the right runtime and
proxies `/v1` so the SPA is single-origin.

## Tools / MCP (required, even for local-llm itself)

local-llm must support **tool/function calling and act as an MCP client** — this
is in scope for Phase 1, generically (any MCP server), not just DHIS2.

- llama-server does OpenAI-style tool calling (`--jinja`); local-llm runs the
  **agent loop**: model emits `tool_call` → local-llm executes it via the MCP
  client → feeds the result back → model continues. Streamed to the chat UI.
- The chat layer renders tool activity from the start (shadcn `Marker`/`Bubble`).
- local-llm owns the MCP client + loop so every client (web UI, extension, CLI)
  stays thin and tools work uniformly.

## North-star roadmap

End goal: a **local, self-hosted DHIS2 assistant** — your own model + DHIS2 tools
in a Chrome side-panel:

```
Chrome extension (side panel, shadcn chat)
  → local-llm (serve --ui --model X): runs the model + MCP client + agent loop
      → MCP server from ../dhis2w-utils  → DHIS2 instance
```

The DHIS2 MCP side is already built in `~/dev/local/dhis2w-utils` (uv workspace):
- **`dhis2w-mcp-bridge`** — one tool `dhis2_cli(args, profile)` shelling out to
  `d2w`; built for small local models (8k context, progressive `--help`). The
  default target for local-llm + a small model.
- **`dhis2w-mcp-router`** — 2 meta-tools (`search_tools`/`call_tool`), lazy typed
  discovery, single guarded chokepoint + **read-only mode** (gates DHIS2 writes).
- **`dhis2w-mcp`** — full ~304 typed tools (big-context hosts).
- `dhis2w-browser` — Playwright DHIS2 automation (relevant to the extension's
  later "act on the page" tier).

**Build order (decided):**
1. **Phase 1 — finish local-llm + web chat UI**, including generic tool/MCP
   support (agent loop + MCP client, pointable at any MCP server). `serve --ui`
   and `serve --ui --model X` (locked, extension-ready, CORS).
2. **Phase 2 — DHIS2 + Chrome extension**: point local-llm's MCP client at
   `dhis2w-mcp-bridge`/`-router`; package the chat UI as the side-panel extension
   against the locked `/v1`.
3. Later: extension page-context, then page-actions (via `dhis2w-browser`).

## Open / next ideas

- **Projects (assistant definitions)** — two units: the *global* **library**
  (models on the drive) vs a *local* **project** (`./local-llm.toml`: a library
  model + MCP servers + system prompt + serve settings). Projects make assistants
  reproducible/shareable; the north-star DHIS2 assistant is just a project. Keep
  the project file a thin manifest, not a framework.
- **`llm init`** — scaffold a project in the cwd and ensure its model is in the
  library; when undecided, offer a curated **2–3** tiny starter models (compact
  GGUF, MLX for Apple Silicon, a tool-capable one). On-ramp: clone → `llm init` →
  `llm serve --ui`. Idempotent: pull only models missing from the library; no
  cwd/`~/.config` "ran" flag (any optional marker lives in `<backup_root>/.local-llm/`).
- Refactor toward the format-centric shared library above (the big one).
- `make check` is the CI gate (read-only); `make lint` mutates locally.
- Auto-fetch HF model cards for LM Studio models (infer repo from path).
- A "want list" / sync command to (re-)download a declared set of models.
- Verify/repair: re-check sizes & checksums against metadata.
