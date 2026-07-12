# heim

[![PyPI](https://img.shields.io/pypi/v/heim?color=006dad&label=PyPI)](https://pypi.org/project/heim/)
[![CI](https://github.com/winterop-com/heim/actions/workflows/ci.yml/badge.svg)](https://github.com/winterop-com/heim/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-winterop--com.github.io%2Fheim-2b7489)](https://winterop-com.github.io/heim/)
[![Python](https://img.shields.io/badge/python-3.13-3776ab)](https://www.python.org/)
[![Packaging](https://img.shields.io/badge/install-uv-6340ac)](https://docs.astral.sh/uv/)
[![License](https://img.shields.io/badge/license-proprietary-lightgrey)](LICENSE)

**Documentation: <https://winterop-com.github.io/heim/>**

A tool for building and keeping a **full local library of LLM models**. It
discovers models from **Hugging Face**, **Ollama**, and **LM Studio**, pulls
them into a single library (browse via a **Typer CLI** or a **browser chat UI**),
and runs them from there. The library lives under one configurable root you point
at an external drive (e.g. a 5TB drive).

**The name.** *heim* is German and Old Norse for **"home"** — the point of the tool is that your
models, your tools, and your data stay at home, on your own box, rather than in someone else's cloud.

> **Proprietary, source-available.** heim is not open-source. Copyright (c) 2026 Morten
> Hansen, all rights reserved (see [`LICENSE`](LICENSE)). The source is published for
> reference and evaluation; running it requires a written license — contact
> <morten@winterop.com>. Install with `uv` — `uv tool install heim` (published on PyPI) or from a
> source checkout.

![heim web UI](docs/assets/web-ui.png)

## Layout

```
src/heim/
├── config.py          # Pydantic settings (HEIM_* env vars)
├── models.py          # Catalog / entry / result models
├── catalog.py         # Aggregates listing + pull across sources
├── library.py         # Scans the on-drive library (gguf/ mlx/ voice/ ...)
├── capabilities.py    # Detects per-model tools/vision/audio + context
├── runtime.py         # Serves a model (llama.cpp / mlx_lm / mlx-vlm)
├── voice/             # Voice models: registry, import, TTS/STT runtime
├── cli/              # Typer CLI package, one module per command group (entry: `heim` → heim.cli:main)
├── app.py             # FastAPI app factory
├── routers/           # health + catalog (browse/pull) + serving (load/chat/audio) endpoints
└── sources/           # huggingface / ollama / lmstudio adapters
```

## Setup

```bash
uv sync                       # heim itself (needs Python 3.13 + uv)
brew install llama.cpp        # baseline runtime: GGUF chat + OuteTTS speech (build from source on Linux)
make install-mlx              # optional: MLX runtimes (Apple Silicon)
uv sync --extra voice         # optional: mlx-audio runtimes (Dia/Whisper, Apple Silicon)
make frontend                 # optional: build the web UI (needs Bun)
export HEIM_LIBRARY_ROOT=/path/to/your/library   # required: where your library lives
heim doctor                   # verify what's installed
```

**Point heim at a library.** heim won't guess a location — set `HEIM_LIBRARY_ROOT`
(a per-machine shell/`.env` value; an external drive is the intended home). Without
it, library commands fail with a clear message instead of silently using `./data`.

**Install globally** (run `heim` from any directory):

```bash
uv tool install --editable ".[mlx,voice,web,benchmark]"   # from a checkout: heim on your PATH, code edits live
uv tool install "git+https://github.com/winterop-com/heim" # or straight from git (requires repo access; core CLI)
# then put HEIM_LIBRARY_ROOT in your shell profile (~/.zshrc) so it applies everywhere
```

Only `uv sync` + llama.cpp are needed to run GGUF models; the rest are optional.
The `benchmark` extra adds the `heim benchmark` eval command; drop it if you don't need it.
See [getting started](docs/getting-started.md) for details.

heim is installed **from this workspace** (`uv sync` / `uv tool install -e .`), not as a
standalone PyPI wheel: the bundled first-party MCP servers (`heim-mcp-*`) are unpublished
workspace members that resolve as editable siblings, so a loose `pip install heim` off PyPI
is not a supported install path.

## CLI

```bash
heim library ls                     # your library (the models on your drive)
heim library sources                # models in app caches (HF/Ollama/LM Studio) you could pull
heim library pull lmstudio lmstudio-community/gemma-4-12B-it-QAT-GGUF
heim library pull ollama gemma4:31b --move   # copy to the library, then delete the local copy
heim doctor                         # pre-flight: runtimes, library, project
heim serve --ui                     # browse + chat in the browser (Chat · Voice · Library)
heim chat gemma-4-12B-it-QAT-GGUF -p "hi"             # one-shot, scriptable
heim chat gemma-4-12B-it-QAT-GGUF -p "?" -i pic.jpg   # image input (vision model)
heim voice ls                       # voice models (TTS/STT) in the library
heim voice import --all             # import known voice models to the library
heim voice speak -v af_heart "hello there"           # text-to-speech (Kokoro)
heim voice speak --model dia --seed 10 "hi there"    # Dia (pin a seed for a stable voice)
heim project init                   # scaffold a project assistant (model + tools + prompt)
```

**Two model families:** **Chat** (language models you talk to — text in/out; some
also read images/audio or call tools) and **Voice** (TTS speaks, STT transcribes).
heim detects each chat model's capabilities and runs the right runtime — GGUF via
llama.cpp (`llama-server`, `--mmproj` for vision/audio), MLX via `mlx_lm`/`mlx-vlm`.
The web UI's **Library** lists both families; the **Voice** studio does TTS/STT
(Kokoro, Dia with voice cloning, Whisper); in chat you can attach images/audio,
dictate with the mic (Whisper), and **read replies aloud** (Kokoro by default). See
the [voice guide](docs/guides/voice.md).

Full docs: **<https://winterop-com.github.io/heim/>** (or `make docs` to serve locally) —
getting started, the library, pulling, running & chatting, the web UI, the Chrome side panel,
the DHIS2 assistant, and the architecture.

## API

`heim serve` exposes an OpenAI-compatible surface plus browse/voice endpoints:
`/api/status`, `/api/library`, `/api/voice`, `/api/chat` (tool-aware SSE),
`/v1/*` (proxied to the loaded model), and `/v1/audio/speech` +
`/v1/audio/transcriptions`. See the [web UI guide](docs/guides/web-ui.md) for the
full endpoint table and the single-origin proxy design.

## Chrome side panel & the DHIS2 assistant

heim ships an **MV3 Chrome side panel** (`extension/`, built with WXT) — a thin client for
a local or remote `heim serve` that puts your own model + tools next to any page. It builds
in two flavors from one codebase: the generic **heim** panel and **heim for DHIS2**
(`HEIM_FLAVOR=dhis2`).

Pointed at a DHIS2 project (`heim project new --template dhis2`), it becomes the north-star
assistant: chat grounded in the page you are viewing, a target banner (verify + tab
match/mismatch), and **"Use my login"** — mint a read-only, GET-scoped Personal Access Token
in the DHIS2 tab's own security context and hand it to heim once, so the tools act as *you*
(with a session-cookie fallback). Everything runs against your own local model; nothing
leaves the box.

```bash
cd extension && bun install && bun run build          # -> extension/.output/chrome-mv3(-dhis2)
# chrome://extensions -> Load unpacked -> the built dir; then `heim serve` and open the panel
```

See the [Chrome side panel guide](https://winterop-com.github.io/heim/guides/extension/) and
the [verified prompt catalog](https://winterop-com.github.io/heim/guides/extension-prompts/).

## Configuration

Two separate concepts:

- **The library location** — where your models live. Set **`HEIM_LIBRARY_ROOT`** (a
  per-machine value; shell profile or `.env`). heim **requires** it — without one,
  library/chat/serve commands fail with a clear message rather than silently using a
  local folder. An external drive is the intended home:

  ```bash
  export HEIM_LIBRARY_ROOT=/path/to/your/library     # e.g. a mounted external drive
  ```

- **A project** (`heim.toml`, via `heim project init` / `heim project new <dir>`) — a
  purpose-built **assistant**: `[project].model` + `system_prompt`, with tools in a sibling
  `.mcp.json` (standard `mcpServers`). In a project, `heim serve` / `heim chat` bind to that
  model (like `--model`, with its tools + prompt); **outside** a project it's free-play
  (pick/switch any model). A project uses the machine library by default.

Precedence (high → low): CLI flags, `HEIM_*` env vars, `heim.toml`, `.env`,
`~/.config/heim/config.toml` (machine defaults).

| Key / env var                                 | Purpose                             |
| --------------------------------------------- | ----------------------------------- |
| `HEIM_LIBRARY_ROOT` (or `library_root`)       | the library location (**required**) |
| `HEIM_OLLAMA_MODELS_DIR`, `HEIM_LMSTUDIO_MODELS_DIR` | source caches to pull from    |
| `HEIM_HF_TOKEN`                               | HF token (uses your HF login if unset) |
| `HEIM_DEFAULT_MAX_TOKENS` (or `default_max_tokens`) | per-turn `/api/chat` generation cap (default 4096; `0` = unbounded) |

## Develop

```bash
make lint    # ruff format + check, mypy, pyright (mutates)
make check   # same, read-only (CI gate) + tests
make test    # pytest
make build   # uv build (wheel + sdist)
```
