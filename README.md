# local-llm

A tool for building and keeping a **full local library of LLM models**. It
discovers models from **Hugging Face**, **Ollama**, and **LM Studio**, pulls
them into a single library (browse via CLI, a Textual TUI later, or a FastAPI
service), and runs them from there. The library lives under one configurable
root you point at an external drive (e.g. a 5TB drive).

## Layout

```
src/local_llm/
├── config.py          # Pydantic settings (LOCAL_LLM_* env vars)
├── models.py          # Catalog / entry / result models
├── catalog.py         # Aggregates listing + pull across sources
├── library.py         # Scans the on-drive library (gguf/ mlx/ ...)
├── runtime.py         # Serves a model (llama.cpp / mlx_lm)
├── cli.py             # Typer CLI (entry points: `llm`, `local-llm`)
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
llm list                     # what's in the local source stores (HF/Ollama/LM Studio)
llm list -s ollama           # filter by source
llm pull lmstudio lmstudio-community/gemma-4-12B-it-QAT-GGUF
llm pull ollama gemma4:31b --move   # copy to the library, then delete the local copy
llm library                  # what's in the on-drive library
llm run gemma-4-12B-it-QAT-GGUF     # serve it (OpenAI API); GGUF→llama.cpp, MLX→mlx_lm
llm serve                    # start the FastAPI browse API
```

See [docs/USAGE.md](docs/USAGE.md) for running models directly with llama.cpp,
Ollama, LM Studio, and MLX, and attaching client TUIs.

## API

```bash
make dev                              # uvicorn with --reload
# GET  /health
# GET  /models?source=ollama
# POST /models/{source}/pull?name=...
```

## Configuration

All paths are env-configurable (prefix `LOCAL_LLM_`). To move the library to the
external drive, change one value:

```bash
export LOCAL_LLM_BACKUP_ROOT=/Volumes/LLM/Library
```

| Variable                     | Default                  |
| ---------------------------- | ------------------------ |
| `LOCAL_LLM_BACKUP_ROOT`      | `data`                   |
| `LOCAL_LLM_OLLAMA_MODELS_DIR`| `~/.ollama/models`       |
| `LOCAL_LLM_LMSTUDIO_MODELS_DIR` | `~/.lmstudio/models`  |
| `LOCAL_LLM_HF_TOKEN`         | (uses HF login if unset) |

## Develop

```bash
make lint    # ruff format + check, mypy, pyright (mutates)
make check   # same, read-only (CI gate) + tests
make test    # pytest
make build   # uv build (wheel + sdist)
```
