# local-llm

Build a **full local library of LLM models**, then **run, chat, and serve** them
— entirely on your own hardware. Discover models from Hugging Face, Ollama, and
LM Studio, pull them into one library on a drive of your choosing, and serve any
of them through an OpenAI-compatible API and a browser chat UI.

```
   sources                    library (your drive)            run / chat / serve
┌───────────┐  llm pull   ┌──────────────────────┐  llm run   ┌──────────────────┐
│ HF cache  │ ──────────▶ │ gguf/  mlx/  …        │ ─────────▶ │ llama-server /   │
│ Ollama    │             │ + model cards/meta   │  llm chat  │ mlx_lm.server    │
│ LM Studio │             │ (LOCAL_LLM_BACKUP_   │  serve --ui│ OpenAI /v1 + UI  │
└───────────┘             │  ROOT)               │            └──────────────────┘
                          └──────────────────────┘
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
llm list                       # what's in your local source stores
llm pull lmstudio <name>       # copy a model into the library (--move to relocate)
llm library                    # what's in the library
llm run <name>                 # serve it: OpenAI /v1 + (for GGUF) a web chat UI
llm chat <name> -p "hello"     # one-shot, scriptable answer
make run MODEL=<name>          # backend + browser UI, locked to one model
```

Start at [Getting started](getting-started.md), or jump to the
[CLI reference](cli.md).
