# kodo

A tool for building and keeping a **full local library of LLM models**. It
discovers models from **Hugging Face**, **Ollama**, and **LM Studio**, pulls
them into a single library (browse via CLI, a Textual TUI later, or a FastAPI
service), and runs them from there. The library lives under one configurable
root you point at an external drive (e.g. a 5TB drive).

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
uv sync
```

## CLI

```bash
kodo list                     # your library (the models on your drive)
kodo sources                  # models in app caches (HF/Ollama/LM Studio) you could pull
kodo pull lmstudio lmstudio-community/gemma-4-12B-it-QAT-GGUF
kodo pull ollama gemma4:31b --move   # copy to the library, then delete the local copy
kodo run gemma-4-12B-it-QAT-GGUF     # serve it (OpenAI API); GGUF→llama.cpp, MLX→mlx_lm
kodo chat gemma-4-12B-it-QAT-GGUF -p "hi"   # one-shot, scriptable
kodo serve --ui               # browser UI over your library
```

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

Config lives in **`kodo.toml`** (run `kodo init`, or copy `kodo.toml.example`).
Top-level keys set the library/runtime; `[project]` / `[[mcp]]` define the
assistant. To put the library on the external drive:

```toml
# kodo.toml
backup_root = "/Volumes/LLM/Library"
```

Any value can be overridden per machine with a `KODO_*` env var (e.g.
`KODO_BACKUP_ROOT=/mnt/llm/Library` on Linux). Precedence, high to low:
CLI flags, `KODO_*` env vars, `kodo.toml`, `.env` (an optional fallback — you
don't need it).

| Key (`kodo.toml`) / env var                 | Default                  |
| ------------------------------------------- | ------------------------ |
| `backup_root` / `KODO_BACKUP_ROOT`          | `data`                   |
| `local_root` / `KODO_LOCAL_ROOT`            | `~/.kodo/library`        |
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
