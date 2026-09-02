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

The library location is set via `STABBUR_LIBRARY_ROOT` (required — stabbur refuses to
run without one rather than silently using a local folder). An external drive is
the intended home; moving to a different machine or drive is just a change to
`STABBUR_LIBRARY_ROOT`, no code changes required. Model weights must never be committed.

## Stack & conventions

- **`uv` is the one and only tool runner and installer.** Every command, script, doc,
  and README uses `uv` — `uv tool install`, `uv run`, `uv sync`, `uvx`. NEVER use bare
  `pip` (or `python -m pip`); at absolute worst use `uv pip`, but prefer `uv sync` /
  `uv add`. This applies to install instructions shown to users too.
- **Source-available, not open-source** (see `LICENSE`) — copyright Morten Hansen. The repo is
  public and **stabbur is published to PyPI**, so it runs with `uvx stabbur` / `uv tool install
  stabbur`. `LICENSE` grants running and evaluating it on your own hardware — the earlier version
  withheld that, which contradicted publishing to an index people install from. Redistribution,
  hosting it as a service, and commercial use still need written permission. **Going fully
  open-source is planned for 1.0.0**; until then don't describe it as open-source.
- Python 3.13, `uv` (with the `uv_build` backend), **src layout**.
- **Typer** CLI; entry point `stabbur = "stabbur.cli:app"` (guarded via `stabbur.cli:main`).
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
  the one-parser-one-writer `stabbur.toml`, the import-time HF-cache side effect, the
  process supervisor (group kill, pidfile, orphan sweep) and per-library `flock`.
- **`docs/ui-conventions.md`** — the browser UI's rules: the three-size type scale by role
  (no hand-written pixel sizes), what each colour variable means (including the `-ink` fill/text
  split), the shared row/chip/section recipes, and what the two gate checks can and cannot catch.
  Read it before touching `frontend/`.
- **`ROADMAP.md`** — forward-looking plans (north-star DHIS2 assistant, phased build
  order, open issues). Update it when plans change.
- **`CHROME.md`** — the browser-extension design (side-panel client, CORS vs cross-site guard,
  the act-as-the-logged-in-user auth model, the `/api/chat` + confirm contract).
- **`PAGEACTIONS.md`** — tools the model runs in the user's tab: the wire contract, the five
  safety rules (typed actions, the forced gate), and what is built vs unbuilt.
- **`WEBMCP.md`** — the WebMCP decision record: watch, don't build, and why.
- **`docs/`** (mkdocs site) — `getting-started.md`, `cli.md`, `benchmarks.md`, guides.
- **git history** — what shipped and when. Don't keep a changelog in this file.

## Libraries & projects (the mental model)

Two distinct, composable concepts (see `stabbur.library.roots`):

- **A Library is a self-contained, portable store**: model files **plus their own
  metadata** (tags in `<root>/.stabbur/tags.json`) — move the drive to another machine
  and the tags come along. Nothing about a library is "local" or "external"; it's just
  a *location*. The **default library** is `STABBUR_LIBRARY_ROOT` (per-machine config).
- **A Project (`./stabbur.toml`) composes libraries + defines an assistant.** It lists
  `libraries = [...]` in priority order — project-relative paths plus the `@shared`
  token for the machine default — so it can keep hot models next to it *and* use the big
  archive. `[project]` (model + system prompt) and `[voice]` define the assistant; **tools
  live in `.mcp.json`** (standard `mcpServers` JSON, see below), not in `stabbur.toml`. A project
  references models **by name**, never by path — so it's portable/committable. Outside a
  project, just the default library is used. The manifest is found by **walking up** from the cwd
  (`project.discover`; stops at home, at a mount boundary, never searches `/`), so every
  project-relative path — `libraries`, `.mcp.json` — resolves against the *manifest's* directory,
  never the cwd. `stabbur init <dir>` creates a **new** directory (refusing an existing one without
`--force`) and downloads the model and voices into the project's own `library/`, so the manifest
lists that store alone and the directory travels intact; `stabbur configure` edits it afterwards.

`library.scan()` reads across the resolved libraries (first match wins); each model
records its `library_root` so tags read/write against the right library. All **portable
data** — models, tags, runtime assets like the Kokoro TTS model (`<root>/tts/kokoro`) —
lives in a library so it travels with the drive. Two things live outside a library, and
they're different: **ephemeral machine-local runtime state** (pidfiles + logs under
`$XDG_RUNTIME_DIR/stabbur/runtimes`, else `~/.cache/stabbur/runtimes`; used by `stabbur.runtime.supervisor` to
reap runtimes orphaned by a crashed stabbur — a pid means nothing on another machine, so it must
not travel), and **durable machine config** (`~/.config/stabbur/config.toml` via `stabbur.userconfig`,
written by `stabbur config` / `stabbur setup`: the per-machine `library_root` + `default_model`
defaults, the lowest-priority `Settings` source). Keep the three-way split: portable → library;
transient machine state → XDG runtime/cache; machine defaults → `~/.config/stabbur` (XDG config).

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
writes a `.stabbur/` sidecar (`metadata.json` + `model-card.md`); for Ollama the card is
**generated** from the manifest's text layers (system prompt, template, params, license).

**exFAT** is the recommended filesystem (macOS + Linux read/write; eject cleanly, no
journaling). No symlinks/hardlinks on exFAT, so dedup is "store once, copy to each
runtime", never by link. Mount path differs on Linux — set `STABBUR_LIBRARY_ROOT` per machine.

## Formats & the shared-library direction

Default policy: keep **GGUF + MLX** (ready-to-run); safetensors on demand (the
convert/fine-tune source, 2-4x the quant). Format is a per-model choice, not "keep all".

- **GGUF** — cross-runtime quant (Ollama, LM Studio, llama.cpp; Mac + Linux). The portable
  backbone. **MLX** — Apple Silicon native (just HF repos, so the HF source covers them),
  fastest on the Mac. **safetensors** — original full-precision weights.
- Sharing reality: LM Studio reads loose GGUF/MLX directly; Ollama imports a GGUF into its
  own blob store and won't run a loose file in place. So the win is one canonical library
  copy we *install into* whichever runtime, not one file used live by all. All three consumers
  are fed from the canonical copy: `stabbur library install/uninstall <model> --to/--from
  {ollama,lmstudio}` (Ollama imports via a Modelfile, LM Studio gets a zero-copy symlink) and
  mlx_lm runs a loose MLX copy in place; `stabbur library formats` reports the per-model policy.

## UI — web-first, plus a Textual terminal chat

The browser is the primary, full-featured surface (library browse + chat), via
`stabbur serve --ui`. The interactive terminal chat (`stabbur chat`) is a **Textual TUI**
(`chat_tui/`) reusing the same runtime + agent loop; `stabbur chat -p` is a plain scripted
one-shot (no TUI).

- **`stabbur serve --ui`** — full app: browse the library (grouped by format) + chat with any
  model (pick + switch).
- **`stabbur serve --ui --model <name>`** — *locked* single-model mode: no picker, stable
  OpenAI `/v1`, configurable CORS. The intended backend for the Chrome extension.

Stack: **Vite + React + Tailwind v4 + shadcn/ui**, built with **Bun** (`bun` is the frontend
package manager + runner — `make frontend` runs `bun install && bun run build`; there is no npm
lockfile) to `frontend/dist` and served by `serve --ui` (API routes take precedence, SPA is the
catch-all). **One SPA, four surfaces**
(build the chat UI once, wrap it): web (`serve --ui`), Chrome extension (MV3 side panel,
locked `/v1`), and Tauri + Electron desktop wrappers (maneki's pattern; stabbur's should also
launch/embed `stabbur serve`). Chat UI uses shadcn's official chat components paired with a
**hand-rolled OpenAI SSE fetch loop** — our backends emit raw OpenAI SSE, which the Vercel
AI SDK / AI Elements / assistant-ui don't expect (impedance mismatch), so we avoid them.

## Running models — llama.cpp first, mlx_lm for MLX

Serving is OpenAI-compatible so any client (and our SPA) can attach. Runtimes are
**external processes stabbur spawns**, not imported libs. Alternatively **`stabbur serve
--upstream <url>` fronts a remote OpenAI `/v1`** (e.g. a llama-server in router mode on
another box): stabbur's agent loop, tools, confirm gate, and UI run locally while the models
run there — `UpstreamManager` (in `stabbur.server`) mirrors `ServerManager`'s read surface,
and "loading" just selects a remote id. `stabbur chat --server <url>` is the CLI/TUI
counterpart; both prefer the remote's currently-loaded model so attaching never evicts it.

- **GGUF → llama.cpp `llama-server`** — primary, cross-platform, OpenAI `/v1`, tool calling
  (`--jinja` default), and a native router mode (`--models-dir`, hot-swap by name). A C++
  binary (`brew install llama.cpp`).
- **MLX → `mlx_lm.server` (text) / `mlx_vlm.server` (multimodal)** — Apple Silicon only.
  Vision-capable MLX checkpoints can't be loaded by text-only `mlx_lm` (it errors on the
  extra params and silently returns empty), so stabbur routes them to `mlx-vlm` by the detected
  `vision` capability (`stabbur.capabilities`). The MLX runtimes are an optional, platform-gated
  extra (`uv sync --extra mlx`) — no Linux wheels, so never hard deps; a missing one yields an
  install hint, not a hang.
- Ollama per-tensor models (e.g. `gemma4:12b-mlx`) need Ollama itself; single-GGUF Ollama
  models run via llama.cpp.

## Tools / MCP (required, even for stabbur itself)

stabbur supports **tool/function calling and acts as an MCP client**, generically (any MCP
server), not just DHIS2. llama-server does OpenAI-style tool calling (`--jinja`); stabbur runs
the **agent loop** (model emits `tool_call` → stabbur executes via the MCP client → feeds the
result back → continues), streamed to the chat UI. stabbur owns the client + loop so every
surface (web, extension, CLI) stays thin and tools work uniformly. A tool result's text goes
back as the `tool` message; any **image** it returns (e.g. a Playwright screenshot) is fed to a
**vision** model as a follow-up user image message so it actually sees it (text-only models get a
note instead — never the raw image), gated on the detected `vision` capability.

**Config is the ecosystem-standard `mcpServers` JSON** (`stabbur.mcpservers`), the same shape
Claude Desktop / Claude Code / Cursor use — so a server's README snippet pastes straight in.
Two levels, and they do **not** merge: machine-global `~/.config/stabbur/mcp.json` (what free-play
chat gets; `stabbur mcp add --global`) applies only when there is no `./.mcp.json`. A project's file
is the whole toolset — a project is self-contained, and merging meant a project listing three tools
answered with twenty-two, differently on another machine. CLI `--mcp` still layers on top.
`stabbur.toml` no longer carries tools.
Bundled first-party servers (`stabbur-mcp-*`, base deps) are entered by package name; `stabbur setup`
seeds a minimal global default (`datetime`).

## Gotchas worth knowing

Non-obvious landmines that aren't self-evident from the code:

- **Voice.** Runtime is in-process mlx-audio (Apple) + Kokoro-ONNX (cross-platform, the
  lightweight chat voice). A seeded model honors a seed only via `mx.random.seed()`
  (`generate_audio` ignores a `seed` kwarg) — and the seed→voice mapping is a function of
  the installed mlx version, so curated seeds do not survive an MLX upgrade. A **voice-design** model
  (`instruct`) samples a fresh speaker per run too, and the same seed pins it byte-for-byte — so
  the seed control follows the registry's `seedable` flag, never `voice_mode == seeded`. Same
  shape for `honors_speed`: mlx-audio takes `speed` for every model and the ones that don't
  implement it swallow it, so a slider tied to nothing looks broken (measured per model, not
  assumed). `instruct` goes only to a model whose `voice_mode` is `design`: the runtime forwards
  unknown params straight to the model's `generate()`, where one it does not accept is a
  TypeError, not a shrug. Models the
  registry flags `supported=False` are rejected at the synthesis choke point. Help text and
  docstrings stay model-agnostic: name concrete models only in the registry entries and in
  listings, never in `--help` or generic comments (another machine may hold none of them).
- **Capabilities.** Tool detection requires a tool-*calling* marker (`tool_call` /
  `function_call` / `available_tools`), not a bare "tools" — the latter false-flagged audio
  specialists.
- **Two model families.** Chat (text in/out; some read vision/audio, some call tools) and
  Voice (TTS speaks, STT transcribes; audio in/out, not chat). Voice runs on demand, never in
  the chat runtime — the composer's model picker offers Chat models only, and the Library lists
  Voice under its own heading with its own count.

## Dev workflow

- **Never leak anything from this machine or network.** No real hostnames, no MagicDNS or
  tailnet names, no private IPs, no model names off the drive, no paths under `$HOME`, no user
  names — not in code, comments, tests, fixtures, docs, screenshots, commit messages, PR bodies
  or release notes. All of those are public: the README *is* the PyPI project page, and a release
  note is announced. Use placeholders that are obviously placeholders — `gpu-box`, `lab-rig`,
  `example.com`, `some-remote-model`. When a real value is needed to reproduce something, it
  belongs in the conversation, never in a commit.

  This has gone wrong three times: a private model name baked into the docs screenshot, and a
  LAN host plus a real MagicDNS name spread across the README, roadmap, source comments, tests
  and a published release note. Grep before publishing anything outward-facing.
- **Branch and PR, never straight to `main`.** Every change goes on a `<type>/<short-description>`
  branch and lands through a pull request — including one-line fixes and docs. `main` is public and
  published from: a tag on it publishes to PyPI, and its README is the PyPI project page, so a
  mistake there is visible to everyone before anyone can review it. A PR is also where a reviewer
  can see the reasoning; a direct push has no such place.
- **Merge a PR once its checks are green.** Do not let green PRs queue up: each one that waits is
  a branch drifting from `main`, and stacked branches turn one conflict into several. Merge in
  dependency order when two touch the same lines, and rebase the loser rather than resolving a
  conflict blind.
- `make check` is the CI gate (read-only, also run in `.github/workflows/ci.yml`); `make lint`
  mutates locally. Run `make lint` and `make test` before committing.
- The gate covers the **SPA** too: `oxlint` (`frontend/.oxlintrc.json`, via `bun run lint`) for its
  JS/TS, and `scripts/check_ui_classes.py` for the class conventions a JS linter cannot see inside
  a `className`. Both need `bun`; the Makefile does a frozen install itself.
- **Actually run the thing you changed — and for a UI, look at it.** Headless assertions prove
  behavior, not that a screen is usable: a wizard whose model list was ordered smallest-first, whose
  tool step could not be multi-selected, and which never said it was about to download 10 GB passed
  every pilot test it had. None of that survives thirty seconds of looking at it.
  - A CLI change: run the command. A packaging change: run it from the **installed artifact**
    (`uv tool install --refresh`), not the checkout — the checkout cannot catch what packaging breaks.
  - A Textual TUI: render it and *look*. `App.save_screenshot("out.svg")` works headlessly under
    `run_test()`; convert to PNG and open it. Every screen the change touches, in the state a user
    first meets it.
  - The browser UI: `serve --ui` and drive it (Playwright is available), don't infer from the JSX.
  - Never write "verified" for something you reasoned about rather than ran, and say plainly which
    of the two a claim is.
- **Release notes are written, never generated.** Cutting a release means writing the notes
  into the ANNOTATED TAG MESSAGE (`git tag -a vX.Y.Z -F notes.md`); the publish workflow passes
  them straight through with `--notes-from-tag`. Never `--generate-notes` and never a bare
  "see the changelog": a list of commit subjects plus a compare link says what was touched and
  nothing about whether it affects the reader. Say what changed, why it matters, and — when the
  answer is "probably nothing for you" — say that too. Same reason there is no `CHANGELOG.md`.
- **Change the chat UI's appearance → run `make hero`, in the same commit.** `docs/assets/web-ui.png`
  is a *derived artifact* of the SPA, but nothing rebuilds it and no check fails when it drifts, so
  it silently advertises a UI that no longer exists (it has gone stale twice, once across two whole
  renames). It is also public: it is the README, the docs landing page, *and* the PyPI project page.
  The script serves the UI against a mock `/v1` so the model chip never leaks a real model name from
  the machine taking the shot — never point a capture at your own running server.
- **Ship the built SPA when packaging.** `make frontend-pack` stages `frontend/dist` into
  `src/stabbur/webui`, which the wheel carries and `Settings.frontend_dir` prefers. Without it
  `serve --ui` answers 404 for every install while every checkout works — the class of bug a
  checkout cannot catch, so verify the *artifact*, not the repo.
