# Getting started

## Install

### 1. kodo itself

Requires **Python 3.13** and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

This installs the **`kodo`** command (with a hidden `ls` alias for `list`) plus the
bundled first-party MCP tool servers (`datetime`, `files`, `memory`, …), so tools work
out of the box. Run it via `uv run kodo …`, or activate the venv and call `kodo` directly.
Use `make install` (= `uv sync --extra benchmark`) if you also want the `kodo benchmark`
eval command.

kodo is installed **from this workspace**, not as a standalone PyPI wheel — the bundled
`kodo-mcp-*` servers are unpublished workspace members that resolve as editable siblings, so
a loose `pip install kodo` off PyPI is not a supported path.

### 2. Runtimes (to actually run models)

kodo spawns model runtimes as **external processes** — it doesn't bundle them.
Install the ones you need:

| Runtime | Install | Needed for |
| --- | --- | --- |
| **llama.cpp** | `brew install llama.cpp` (macOS); build from source on Linux | The baseline: GGUF chat (`llama-server`) and OuteTTS speech (`llama-tts`) |
| **MLX** — optional, Apple Silicon | `make install-mlx` (= `uv sync --extra mlx`) | Running MLX models (`mlx_lm` / `mlx-vlm`) — fastest on Macs |

llama.cpp is the one to install first. The MLX extra is optional and gated — add
it only if you want MLX models. **Kokoro TTS** (54 multi-voice speech, espeak-ng
bundled) ships built in — no extra to install; on first use it downloads its model
(~310 MB) into the library (`<root>/tts/kokoro`).

!!! note "MLX + `transformers` 5.13"
    `make install-mlx` already caps `transformers<5.13` (5.13 broke `mlx-lm`'s tokenizer
    registration — MLX models crash at load otherwise). If you instead run kodo as a global
    `uv tool` and provide the MLX runtimes as standalone tools, apply the same cap:

    ```bash
    uv tool install mlx-lm  --with 'transformers>=5.5,<5.13'
    uv tool install mlx-vlm --with 'transformers>=5.5,<5.13'
    ```

    Drop the cap once `mlx-lm` ships a `transformers` 5.13-compatible release.

### 3. Web UI (optional)

The browser UI is built from source (**[Bun](https://bun.sh)** required); it isn't committed:

```bash
make frontend        # bun install + build -> frontend/dist
```

Then run `kodo serve --ui`. Skip this if you only use the CLI.

### Set up this machine

```bash
kodo setup           # first-run: sets the library location + a default model, builds the UI
kodo doctor          # or just check runtimes, library, and the current project
```

`kodo setup` is the write-mode companion to `kodo doctor`: it persists per-machine
defaults (see below), builds the browser UI if [Bun](https://bun.sh) is present, and
prints an OS-specific hint for anything it can't install (the llama.cpp binary). It's
safe to re-run. Prefer to do it by hand? Everything it writes is a `kodo config` call.
With no drive mounted, its fallback library location is the XDG data dir
(`~/.local/share/kodo/library`); point it at your external drive when you have one.

Optional model *sources* (not required to run kodo): **Ollama** and **LM Studio**
— kodo reads their local caches if present, so you can pull models from them.

## Point at your library

The library location is the `library_root` setting. The simplest way to set it
per machine — no shell edits — is the **machine config**:

```bash
kodo config set library-root /path/to/your/library   # -> ~/.config/kodo/config.toml
kodo config set model lmstudio-community/gemma-4-12B-it-QAT-GGUF   # default model outside a project
```

A **project** can instead pin its own library + model in **`kodo.toml`** (run
`kodo project init` to scaffold one; see [kodo.toml.example](https://github.com/winterop-com/kodo/blob/main/kodo.toml.example)):

```toml
# kodo.toml
library_root = "/path/to/your/library"

[project]
model = "lmstudio-community/gemma-4-12B-it-QAT-GGUF"
```

Every value can be overridden per machine with a `KODO_*` environment variable
(e.g. a different mount path on Linux: `KODO_LIBRARY_ROOT=/media/<user>/<drive>`).
Precedence, high to low: CLI flags, `KODO_*` env vars, project `kodo.toml`, `.env`,
machine config (`~/.config/kodo/config.toml`). Settings are [pydantic-settings][ps].
See [The library](guides/library.md) for storage notes.

[ps]: https://docs.pydantic.dev/latest/concepts/pydantic_settings/

## First run

```bash
kodo library ls                                   # your library (empty at first)
kodo library sources                                # models in app caches you could pull
kodo library pull lmstudio lmstudio-community/...    # pull one into the library
kodo library ls                                   # now it's in your library
kodo serve --ui                             # browse + chat in the browser
```

Then explore: [Pulling models](guides/pulling.md) ·
[Running & chatting](guides/running.md) · [Web UI](guides/web-ui.md).
