# kodo

Build a **full local library of LLM models**, then **run, chat, and serve** them
— entirely on your own hardware. Discover models from Hugging Face, Ollama, and
LM Studio, pull them into one library on a drive of your choosing, and serve any
of them through an OpenAI-compatible API and a browser chat UI.

```mermaid
flowchart LR
    hf["HF cache"] -->|kodo pull| lib
    ol["Ollama"] -->|kodo pull| lib
    ls["LM Studio"] -->|kodo pull| lib
    lib["Library on your drive<br/>gguf/ · mlx/ · cards + metadata"] -->|kodo run / chat / serve --ui| rt["llama-server / mlx_lm.server<br/>OpenAI /v1 + web chat UI"]
```

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
kodo list                       # what's in your local source stores
kodo pull lmstudio <name>       # copy a model into the library (--move to relocate)
kodo library                    # what's in the library
kodo run <name>                 # serve it: OpenAI /v1 + (for GGUF) a web chat UI
kodo chat <name> -p "hello"     # one-shot, scriptable answer
make run MODEL=<name>          # backend + browser UI, locked to one model
```

Start at [Getting started](getting-started.md), or jump to the
[CLI reference](cli.md).

## The name

**kodo** is short, easy to type as a command, and deliberately *neutral* — it
collides with nothing (no PyPI package, no product, no brand), which is the
safest kind of name. If you want a meaning, read it as 鼓動 (*kodō*) — Japanese
for **heartbeat / pulse**: the steady pulse of your own models, running on your
own hardware.
