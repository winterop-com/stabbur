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

The library location is set via `HEIM_LIBRARY_ROOT` (required — heim refuses to
run without one rather than silently using a local folder). An external drive is
the intended home; moving to a different machine or drive is just a change to
`HEIM_LIBRARY_ROOT`, no code changes required. Model weights must never be committed.

## Stack & conventions

- **`uv` is the one and only tool runner and installer.** Every command, script, doc,
  and README uses `uv` — `uv tool install`, `uv run`, `uv sync`, `uvx`. NEVER use bare
  `pip` (or `python -m pip`); at absolute worst use `uv pip`, but prefer `uv sync` /
  `uv add`. This applies to install instructions shown to users too.
- **Proprietary, all rights reserved** (see `LICENSE`) — copyright Morten Hansen. The repo
  is source-available, NOT open-source: no one may use/run/redistribute it without written
  permission. Do NOT publish heim (or the bundled `heim-mcp-*`) to public PyPI; the
  `Private :: Do Not Upload` classifier enforces this. Install is from source via
  `uv tool install` from the repo/git, never `pip install heim`.
- Python 3.13, `uv` (with the `uv_build` backend), **src layout**.
- **Typer** CLI; entry point `heim = "heim.cli:app"` (guarded via `heim.cli:main`).
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
  the one-parser-one-writer `heim.toml`, the import-time HF-cache side effect, the
  process supervisor (group kill, pidfile, orphan sweep) and per-library `flock`.
- **`ROADMAP.md`** — forward-looking plans (north-star DHIS2 assistant, phased build
  order, open issues). Update it when plans change.
- **`CHROME.md`** — the Chrome/browser-extension design (side-panel client, `/api/chat`
  contract, CORS vs cross-site guard, live-session SameSite analysis).
- **`docs/`** (mkdocs site) — `getting-started.md`, `cli.md`, `benchmarks.md`, guides.
- **git history** — what shipped and when. Don't keep a changelog in this file.

## Libraries & projects (the mental model)

Two distinct, composable concepts (see `heim.library.roots`):

- **A Library is a self-contained, portable store**: model files **plus their own
  metadata** (tags in `<root>/.heim/tags.json`) — move the drive to another machine
  and the tags come along. Nothing about a library is "local" or "external"; it's just
  a *location*. The **default library** is `HEIM_LIBRARY_ROOT` (per-machine config).
- **A Project (`./heim.toml`) composes libraries + defines an assistant.** It lists
  `libraries = [...]` in priority order — project-relative paths plus the `@shared`
  token for the machine default — so it can keep hot models next to it *and* use the big
  archive. `[project]` (model + system prompt) and `[voice]` define the assistant; **tools
  live in `.mcp.json`** (standard `mcpServers` JSON, see below), not in `heim.toml`. A project
  references models **by name**, never by path — so it's portable/committable. Outside a
  project, just the default library is used.

`library.scan()` reads across the resolved libraries (first match wins); each model
records its `library_root` so tags read/write against the right library. All **portable
data** — models, tags, runtime assets like the Kokoro TTS model (`<root>/tts/kokoro`) —
lives in a library so it travels with the drive. Two things live outside a library, and
they're different: **ephemeral machine-local runtime state** (pidfiles + logs under
`$XDG_RUNTIME_DIR/heim/runtimes`, else `~/.cache/heim/runtimes`; used by `heim.runtime.supervisor` to
reap runtimes orphaned by a crashed heim — a pid means nothing on another machine, so it must
not travel), and **durable machine config** (`~/.config/heim/config.toml` via `heim.userconfig`,
written by `heim config` / `heim setup`: the per-machine `library_root` + `default_model`
defaults, the lowest-priority `Settings` source). Keep the three-way split: portable → library;
transient machine state → XDG runtime/cache; machine defaults → `~/.config/heim` (XDG config).

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
writes a `.heim/` sidecar (`metadata.json` + `model-card.md`); for Ollama the card is
**generated** from the manifest's text layers (system prompt, template, params, license).

**exFAT** is the recommended filesystem (macOS + Linux read/write; eject cleanly, no
journaling). No symlinks/hardlinks on exFAT, so dedup is "store once, copy to each
runtime", never by link. Mount path differs on Linux — set `HEIM_LIBRARY_ROOT` per machine.

## Formats & the shared-library direction

Default policy: keep **GGUF + MLX** (ready-to-run); safetensors on demand (the
convert/fine-tune source, 2-4x the quant). Format is a per-model choice, not "keep all".

- **GGUF** — cross-runtime quant (Ollama, LM Studio, llama.cpp; Mac + Linux). The portable
  backbone. **MLX** — Apple Silicon native (just HF repos, so the HF source covers them),
  fastest on the Mac. **safetensors** — original full-precision weights.
- Sharing reality: LM Studio reads loose GGUF/MLX directly; Ollama imports a GGUF into its
  own blob store and won't run a loose file in place. So the win is one canonical library
  copy we *install into* whichever runtime, not one file used live by all. All three consumers
  are fed from the canonical copy: `heim library install/uninstall <model> --to/--from
  {ollama,lmstudio}` (Ollama imports via a Modelfile, LM Studio gets a zero-copy symlink) and
  mlx_lm runs a loose MLX copy in place; `heim library formats` reports the per-model policy.

## UI — web-first, plus a Textual terminal chat

The browser is the primary, full-featured surface (library browse + chat), via
`heim serve --ui`. The interactive terminal chat (`heim chat`) is a **Textual TUI**
(`chat_tui/`) reusing the same runtime + agent loop; `heim chat -p` is a plain scripted
one-shot (no TUI).

- **`heim serve --ui`** — full app: browse the library (grouped by format) + chat with any
  model (pick + switch).
- **`heim serve --ui --model <name>`** — *locked* single-model mode: no picker, stable
  OpenAI `/v1`, configurable CORS. The intended backend for the Chrome extension.

Stack: **Vite + React + Tailwind v4 + shadcn/ui**, built with **Bun** (`bun` is the frontend
package manager + runner — `make frontend` runs `bun install && bun run build`; there is no npm
lockfile) to `frontend/dist` and served by `serve --ui` (API routes take precedence, SPA is the
catch-all). **One SPA, four surfaces**
(build the chat UI once, wrap it): web (`serve --ui`), Chrome extension (MV3 side panel,
locked `/v1`), and Tauri + Electron desktop wrappers (maneki's pattern; heim's should also
launch/embed `heim serve`). Chat UI uses shadcn's official chat components paired with a
**hand-rolled OpenAI SSE fetch loop** — our backends emit raw OpenAI SSE, which the Vercel
AI SDK / AI Elements / assistant-ui don't expect (impedance mismatch), so we avoid them.

## Running models — llama.cpp first, mlx_lm for MLX

Serving is OpenAI-compatible so any client (and our SPA) can attach. Runtimes are
**external processes heim spawns**, not imported libs. Alternatively **`heim serve
--upstream <url>` fronts a remote OpenAI `/v1`** (e.g. a llama-server in router mode on
another box): heim's agent loop, tools, confirm gate, and UI run locally while the models
run there — `UpstreamManager` (in `heim.server`) mirrors `ServerManager`'s read surface,
and "loading" just selects a remote id. `heim chat --server <url>` is the CLI/TUI
counterpart; both prefer the remote's currently-loaded model so attaching never evicts it.

- **GGUF → llama.cpp `llama-server`** — primary, cross-platform, OpenAI `/v1`, tool calling
  (`--jinja` default), and a native router mode (`--models-dir`, hot-swap by name). A C++
  binary (`brew install llama.cpp`).
- **MLX → `mlx_lm.server` (text) / `mlx_vlm.server` (multimodal)** — Apple Silicon only.
  Vision-capable MLX checkpoints can't be loaded by text-only `mlx_lm` (it errors on the
  extra params and silently returns empty), so heim routes them to `mlx-vlm` by the detected
  `vision` capability (`heim.capabilities`). The MLX runtimes are an optional, platform-gated
  extra (`uv sync --extra mlx`) — no Linux wheels, so never hard deps; a missing one yields an
  install hint, not a hang.
- Ollama per-tensor models (e.g. `gemma4:12b-mlx`) need Ollama itself; single-GGUF Ollama
  models run via llama.cpp.

## Tools / MCP (required, even for heim itself)

heim supports **tool/function calling and acts as an MCP client**, generically (any MCP
server), not just DHIS2. llama-server does OpenAI-style tool calling (`--jinja`); heim runs
the **agent loop** (model emits `tool_call` → heim executes via the MCP client → feeds the
result back → continues), streamed to the chat UI. heim owns the client + loop so every
surface (web, extension, CLI) stays thin and tools work uniformly. A tool result's text goes
back as the `tool` message; any **image** it returns (e.g. a Playwright screenshot) is fed to a
**vision** model as a follow-up user image message so it actually sees it (text-only models get a
note instead — never the raw image), gated on the detected `vision` capability.

**Config is the ecosystem-standard `mcpServers` JSON** (`heim.mcpservers`), the same shape
Claude Desktop / Claude Code / Cursor use — so a server's README snippet pastes straight in.
Two levels **merge**: machine-global `~/.config/heim/mcp.json` (what free-play chat gets;
`heim mcp add --global`) and per-project `./.mcp.json` (`heim mcp add`); a project name
overrides a global one, and CLI `--mcp` layers on top. `heim.toml` no longer carries tools.
Bundled first-party servers (`heim-mcp-*`, base deps) are entered by package name; `heim setup`
seeds a minimal global default (`datetime`).

## Gotchas worth knowing

Non-obvious landmines that aren't self-evident from the code:

- **Voice.** Runtime is in-process mlx-audio (Apple) + Kokoro-ONNX (cross-platform, the
  lightweight chat voice). A seeded model honors a seed only via `mx.random.seed()`
  (`generate_audio` ignores a `seed` kwarg) — and the seed→voice mapping is a function of
  the installed mlx version, so curated seeds do not survive an MLX upgrade. Models the
  registry flags `supported=False` are rejected at the synthesis choke point. Help text and
  docstrings stay model-agnostic: name concrete models only in the registry entries and in
  listings, never in `--help` or generic comments (another machine may hold none of them).
- **Capabilities.** Tool detection requires a tool-*calling* marker (`tool_call` /
  `function_call` / `available_tools`), not a bare "tools" — the latter false-flagged audio
  specialists.
- **Two model families.** Chat (text in/out; some read vision/audio, some call tools) and
  Voice (TTS speaks, STT transcribes; audio in/out, not chat). Voice runs on demand, never in
  the chat runtime — the top-bar "No chat model" badge is Chat-only.

## Dev workflow

- `make check` is the CI gate (read-only, also run in `.github/workflows/ci.yml`); `make lint`
  mutates locally. Run `make lint` and `make test` before committing.
