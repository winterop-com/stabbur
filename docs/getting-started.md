# Getting started

!!! warning "Access & license"
    stabbur is **proprietary, source-available software** — Copyright (c) 2026 Morten Hansen,
    all rights reserved (see [`LICENSE`](https://github.com/winterop-com/stabbur/blob/main/LICENSE)).
    It is **not** open-source and viewing the source does not grant a right to use it. Running
    stabbur requires a written license from the owner — contact **<morten@winterop.com>**. This
    guide is written for someone who already has access to the repository and permission to run it.

## Install

The quickest way in is `uvx stabbur` — no install, no checkout. Everything below uses
[uv](https://docs.astral.sh/uv/) and needs **Python 3.13**.

`stabbur` and `sb` are the same command; these docs use the short form.

### 1. stabbur itself

The tidiest install is a global **`uv tool`** from a local checkout — it puts `stabbur` on your
`PATH` for every directory, editable so a `git pull` takes effect immediately:

```bash
git clone https://github.com/winterop-com/stabbur && cd stabbur
uv tool install --editable ".[mlx,voice,tts,benchmark]"   # stabbur on your PATH; code edits live
```

Pick only the extras you need — `mlx` and `voice` are Apple-Silicon runtimes, `tts` adds
Kokoro dependencies, `benchmark` adds the `sb benchmark` eval command. On Linux, drop
`mlx`/`voice` (no wheels). You can also install straight from git without a manual clone
(requires repo access; installs the core CLI, non-editable):

```bash
uv tool install "git+https://github.com/winterop-com/stabbur"
```

Prefer to work **in the checkout** instead of a global tool? Use `uv sync` and run everything
through `uv run`:

```bash
uv sync                       # build the project's .venv (stabbur + bundled MCP servers)
uv run stabbur doctor            # run any command with `uv run stabbur …`
```

Either way you get the **`stabbur`** command (with a hidden `ls` alias for `list`) plus the
bundled first-party MCP tool servers (`datetime`, `files`, `memory`, …), so tools work out of
the box. Use `make install` (= `uv sync --extra benchmark`) for a dev sync that includes the
`sb benchmark` eval command.

stabbur is installed **from this workspace**, not as a standalone PyPI wheel — the bundled
`stabbur-mcp-*` servers are unpublished workspace members that resolve as editable siblings, so
a loose `pip install stabbur` off PyPI is not a supported path.

### 2. Runtimes (to actually run models)

stabbur spawns model runtimes as **external processes** — it doesn't bundle them.
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
    registration — MLX models crash at load otherwise). If you instead run stabbur as a global
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

Then run `sb serve --ui`. Skip this if you only use the CLI.

### Set up this machine

```bash
sb setup           # first-run: sets the library location + a default model, builds the UI
sb doctor          # or just check runtimes, library, and the current project
```

`sb setup` is the write-mode companion to `sb doctor`: it persists per-machine
defaults (see below), builds the browser UI if [Bun](https://bun.sh) is present, and
prints an OS-specific hint for anything it can't install (the llama.cpp binary). It's
safe to re-run. Prefer to do it by hand? Everything it writes is a `sb config` call.
With no drive mounted, its fallback library location is the XDG data dir
(`~/.local/share/stabbur/library`); point it at your external drive when you have one.

Optional model *sources* (not required to run stabbur): **Ollama** and **LM Studio**
— stabbur reads their local caches if present, so you can pull models from them.

## Point at your library

The library location is the `library_root` setting. The simplest way to set it
per machine — no shell edits — is the **machine config**:

```bash
sb config set library-root /path/to/your/library   # -> ~/.config/stabbur/config.toml
sb config set model lmstudio-community/gemma-4-12B-it-QAT-GGUF   # default model outside a project
```

A **project** can instead pin its own library + model in **`stabbur.toml`** (run
`sb project init` to scaffold one; see [stabbur.toml.example](https://github.com/winterop-com/stabbur/blob/main/stabbur.toml.example)):

```toml
# stabbur.toml
library_root = "/path/to/your/library"

[project]
model = "lmstudio-community/gemma-4-12B-it-QAT-GGUF"
```

Every value can be overridden per machine with a `STABBUR_*` environment variable
(e.g. a different mount path on Linux: `STABBUR_LIBRARY_ROOT=/media/<user>/<drive>`).
Precedence, high to low: CLI flags, `STABBUR_*` env vars, project `stabbur.toml`, `.env`,
machine config (`~/.config/stabbur/config.toml`). Settings are [pydantic-settings][ps].
See [The library](guides/library.md) for storage notes.

[ps]: https://docs.pydantic.dev/latest/concepts/pydantic_settings/

## First run

```bash
sb library ls                                   # your library (empty at first)
sb library sources                                # models in app caches you could pull
sb library pull lmstudio lmstudio-community/...    # pull one into the library
sb library ls                                   # now it's in your library
sb serve --ui                             # browse + chat in the browser
```

Then explore: [Pulling models](guides/pulling.md) ·
[Running & chatting](guides/running.md) · [Web UI](guides/web-ui.md).
