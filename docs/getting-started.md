# Getting started

## Install

```bash
uv sync
```

This installs `local-llm` and puts two equivalent commands on the path inside the
venv: **`llm`** (primary) and **`local-llm`** (collision-proof alias, since the
`pip install llm` tool also claims `llm`). Run via `uv run llm …` or activate the
venv.

For running models you also need the runtimes:

```bash
brew install llama.cpp        # GGUF: llama-server + llama-cli (macOS; build from source on Linux)
uv tool install mlx-lm        # MLX: mlx_lm.server + mlx_lm.chat (Apple Silicon only)
```

## Point at your library

The library lives under `LOCAL_LLM_BACKUP_ROOT` (default `./data`). Put it on an
external/cloud drive and set it once — in a gitignored `.env` or the environment:

```bash
# .env
LOCAL_LLM_BACKUP_ROOT=/Volumes/LLM/Library
```

All settings use this `LOCAL_LLM_` prefix (they're [pydantic-settings][ps]).
See [The library](guides/library.md) for storage/filesystem notes.

[ps]: https://docs.pydantic.dev/latest/concepts/pydantic_settings/

## First run

```bash
llm list                                   # models in your local source stores
llm pull lmstudio lmstudio-community/...    # copy one into the library
llm library                                 # confirm it's in the library
llm run lmstudio-community/...              # serve it (open the printed Chat UI link)
```

Then explore: [Pulling models](guides/pulling.md) ·
[Running & chatting](guides/running.md) · [Web UI](guides/web-ui.md).
