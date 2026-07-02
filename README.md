# kodo

A tool for building and keeping a **full local library of LLM models**. It
discovers models from **Hugging Face**, **Ollama**, and **LM Studio**, pulls
them into a single library (browse via a **Typer CLI** or a **browser chat UI**),
and runs them from there. The library lives under one configurable root you point
at an external drive (e.g. a 5TB drive).

![kodo web UI](docs/assets/web-ui.png)

## Layout

```
src/kodo/
├── config.py          # Pydantic settings (KODO_* env vars)
├── models.py          # Catalog / entry / result models
├── catalog.py         # Aggregates listing + pull across sources
├── library.py         # Scans the on-drive library (gguf/ mlx/ ...)
├── runtime.py         # Serves a model (llama.cpp / mlx_lm)
├── cli.py             # Typer CLI (entry points: `kodo`, `kodo`)
├── app.py             # FastAPI app factory
├── routers/           # health + models (browse/pull) endpoints
└── sources/           # huggingface / ollama / lmstudio adapters
```

## Setup

```bash
uv sync                       # kodo itself (needs Python 3.13 + uv)
brew install llama.cpp        # baseline runtime: GGUF chat + OuteTTS speech (build from source on Linux)
make install-mlx              # optional: MLX runtimes (Apple Silicon)
make install-tts              # optional: 54-voice Kokoro TTS (macOS + Linux; espeak bundled)
make frontend                 # optional: build the web UI (needs Node/npm)
kodo doctor                   # verify what's installed
```

Only `uv sync` + llama.cpp are needed to run GGUF models; the rest are optional.
See [getting started](docs/getting-started.md) for details.

## CLI

```bash
kodo library ls                     # your library (the models on your drive)
kodo library sources                  # models in app caches (HF/Ollama/LM Studio) you could pull
kodo library pull lmstudio lmstudio-community/gemma-4-12B-it-QAT-GGUF
kodo library pull ollama gemma4:31b --move   # copy to the library, then delete the local copy
kodo doctor                   # pre-flight: runtimes, library, project
kodo serve --ui                     # browse + chat in the browser
kodo chat gemma-4-12B-it-QAT-GGUF -p "hi"          # one-shot, scriptable
kodo chat gemma-4-12B-it-QAT-GGUF -p "?" -i pic.jpg   # image input (vision model)
kodo chat ultravox-v0_5-llama-3_2-1b-GGUF -p "transcribe" -a clip.wav   # audio input
kodo audio voices                   # list Kokoro voices (needs `make install-tts`)
kodo audio speak hello there                      # text-to-speech (default voice)
kodo audio speak -v af_heart "hello there"        # a specific Kokoro voice
kodo serve --ui               # browser UI over your library
```

**Multimodal & voice:** kodo detects each model's capabilities (tool calling,
vision, audio) and runs the right runtime — GGUF via llama.cpp (`llama-server`,
plus `--mmproj` for vision/audio), MLX via `mlx_lm`/`mlx-vlm`. The web UI and CLI
let you attach images/audio (or record from the mic) to multimodal models, and
**read replies aloud**: pick from **54 built-in Kokoro voices** (9 languages, via
the optional `make install-tts` extra) or `llama-tts`/OuteTTS. Replies are reduced
to prose first, so code and Markdown syntax aren't spoken.

Full docs (mkdocs + material): run `make docs`. See `docs/` — getting started,
the library, pulling, running & chatting, the web UI, and using models directly.

## API

```bash
make dev                              # uvicorn with --reload
# GET  /health
# GET  /models?source=ollama
# POST /models/{source}/pull?name=...
```

## Configuration

Config lives in **`kodo.toml`** (run `kodo project init`, or copy `kodo.toml.example`).
Top-level keys set the library/runtime; `[project]` / `[[mcp]]` define the
assistant. To put the library on the external drive:

```toml
# kodo.toml
library_root = "/Volumes/LLM/Library"
```

Any value can be overridden per machine with a `KODO_*` env var (e.g.
`KODO_LIBRARY_ROOT=/mnt/llm/Library` on Linux). Precedence, high to low:
CLI flags, `KODO_*` env vars, `kodo.toml`, `.env` (an optional fallback — you
don't need it).

| Key (`kodo.toml`) / env var                 | Default                  |
| ------------------------------------------- | ------------------------ |
| `library_root` / `KODO_LIBRARY_ROOT`          | `data` (the default library) |
| `ollama_models_dir` / `KODO_OLLAMA_MODELS_DIR` | `~/.ollama/models`    |
| `lmstudio_models_dir` / `KODO_LMSTUDIO_MODELS_DIR` | `~/.lmstudio/models` |
| `hf_token` / `KODO_HF_TOKEN`                | (uses HF login if unset) |

## Develop

```bash
make lint    # ruff format + check, mypy, pyright (mutates)
make check   # same, read-only (CI gate) + tests
make test    # pytest
make build   # uv build (wheel + sdist)
```
