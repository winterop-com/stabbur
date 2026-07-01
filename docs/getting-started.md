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

kodo's config lives in **`kodo.toml`** in your working directory. Run
`kodo init` to scaffold one (see [kodo.toml.example](https://github.com/winterop-com/kodo/blob/main/kodo.toml.example)),
or create it by hand. The library location is the `backup_root` key — put it on
an external/cloud drive:

```toml
# kodo.toml
backup_root = "/Volumes/LLM/Library"

[project]
model = "lmstudio-community/gemma-4-12B-it-QAT-GGUF"
```

Every value can be overridden per machine with a `KODO_*` environment variable
(e.g. a different mount path on Linux: `KODO_BACKUP_ROOT=/mnt/llm/Library`).
A `.env` still works as an optional low-priority fallback, but `kodo.toml` is
the primary config — you don't need `.env`. Precedence, high to low:
CLI flags, `KODO_*` env vars, `kodo.toml`, `.env`. Settings are
[pydantic-settings][ps]. See [The library](guides/library.md) for storage notes.

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
