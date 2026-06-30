# Getting started

## Install

```bash
uv sync
```

This installs the **`kodo`** command (with a hidden `ls` alias for `list`). Run
it via `uv run kodo …`, or activate the venv and call `kodo` directly.

For running models you also need the runtimes:

```bash
brew install llama.cpp        # GGUF: llama-server + llama-cli (macOS; build from source on Linux)
uv tool install mlx-lm        # MLX: mlx_lm.server + mlx_lm.chat (Apple Silicon only)
```

## Point at your library

The library lives under `KODO_BACKUP_ROOT` (default `./data`). Put it on an
external/cloud drive and set it once — in a gitignored `.env` or the environment:

```bash
# .env
KODO_BACKUP_ROOT=/Volumes/LLM/Library
```

All settings use this `KODO_` prefix (they're [pydantic-settings][ps]).
See [The library](guides/library.md) for storage/filesystem notes.

[ps]: https://docs.pydantic.dev/latest/concepts/pydantic_settings/

## First run

```bash
kodo list                                   # your library (empty at first)
kodo sources                                # models in app caches you could pull
kodo pull lmstudio lmstudio-community/...    # pull one into the library
kodo list                                   # now it's in your library
kodo run lmstudio-community/...              # serve it (open the printed Chat UI link)
```

Then explore: [Pulling models](guides/pulling.md) ·
[Running & chatting](guides/running.md) · [Web UI](guides/web-ui.md).
