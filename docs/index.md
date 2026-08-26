<img src="assets/logo.png" alt="stabbur" width="150" align="right">

# stabbur

Build a **full local library of LLM models**, then **run, chat, and serve** them
— entirely on your own hardware. Discover models from Hugging Face, Ollama, and
LM Studio, pull them into one library on a drive of your choosing, and serve any
of them through an OpenAI-compatible API and a browser chat UI.

![stabbur web UI](assets/web-ui.png)

```mermaid
flowchart LR
    hf["HF cache"] -->|sb library pull| lib
    ol["Ollama"] -->|sb library pull| lib
    ls["LM Studio"] -->|sb library pull| lib
    lib["Library on your drive<br/>gguf/ · mlx/ · cards + metadata"] -->|chat / serve --ui| rt["llama-server / mlx_lm.server<br/>OpenAI /v1 "]
```

!!! info "Proprietary, source-available"
    stabbur is **not** open-source. Copyright (c) 2026 Morten Hansen, all rights reserved
    (see [`LICENSE`](https://github.com/winterop-com/stabbur/blob/main/LICENSE)). The source is
    published for reference and evaluation; running it requires a written license — contact
    **<morten@winterop.com>**. Published on PyPI so it can be run with `uvx`; the licence still
    governs use. See [Getting started](getting-started.md).

## Why

- **One library, everywhere.** A single, format-organized library on an external
  or cloud drive — move it by changing one setting.
- **Run anything, uniformly.** GGUF via llama.cpp, MLX via `mlx_lm` — both behind
  one OpenAI-compatible endpoint, so any client attaches the same way.
- **Local and private.** Nothing leaves the box. Built to pair with tool/MCP use
  by small local models.

## Quick taste

```bash
uv sync
sb library ls                       # your library (the models on your drive)
sb library sources                    # models in app caches you could pull
sb library pull lmstudio <name>       # pull one into the library (--move to relocate)
sb serve --ui                 # browse + chat in the browser
sb chat <name> -p "hello"     # one-shot, scriptable answer
make run MODEL=<name>           # backend + browser UI, locked to one model
```

Start at [Getting started](getting-started.md), or jump to the
[CLI reference](cli.md).

## The name

**stabbur** is short, easy to type as a command, and deliberately *neutral* — it
collides with nothing (no PyPI package, no product, no brand), which is the
safest kind of name. If you want a meaning, read it as 鼓動 (*kodō*) — Japanese
for **heartbeat / pulse**: the steady pulse of your own models, running on your
own hardware.
