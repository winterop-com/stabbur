# CLI reference

The CLI is the **`kodo`** command. Everything centers on your **library** — the
models on your drive, at the `library_root` set in `kodo.toml`. Run any command
with `--help` for full options.

## `kodo init`

Scaffold **`kodo.toml`** here — kodo's primary config (no `.env` needed) — and
ensure its model is in the library. The generated file captures the library
location (`library_root`) plus the assistant (`[project]` model + `[[mcp]]`
tools). Idempotent — only pulls the model if it's missing. With no `--model` it
offers a small curated set and pulls the choice into the always-local library.

```bash
kodo init                                  # pick a curated starter model
kodo init --model unsloth/Qwen3.5-4B-GGUF  # bind a specific model
kodo init --force                          # overwrite an existing kodo.toml
```

## `kodo search <query>`

Search the Hugging Face Hub for new models to pull (most-downloaded first).

```bash
kodo search qwen3            # text search
kodo search qwen3 --gguf     # only GGUF (llama.cpp-ready) repos
kodo search qwen3 -n 30      # more results
```

## `kodo list` (alias `kodo ls`)

List the models in **your library** — what you've pulled, ready to run — grouped
by format with sizes. Reads `library_root` (from `kodo.toml`).

```bash
kodo list
kodo ls        # same thing
```

## `kodo sources`

Browse models sitting in your **app caches** (Hugging Face cache, Ollama, LM
Studio) that you could pull into the library. The IN LIBRARY column marks what
you already have. Non-chat (embedding/vision) and partial entries are hidden
unless `--all`.

```bash
kodo sources
kodo sources -s ollama       # --source: limit to one source
kodo sources --all           # include embedding/vision/partial entries
```

## `kodo pull <source> <name>`

Copy a model from a source cache into the library.

```bash
kodo pull lmstudio <name>
kodo pull ollama gemma4:31b --move    # delete the local source after a verified copy
kodo pull ollama --all                # import every model from the local store
kodo pull lmstudio --all --move       # import all, freeing local disk as it goes
```

- `--move` — relocate (copy, verify byte-for-byte, then delete the local source
  to free disk). Supported for LM Studio and Ollama.
- `--all` — import every model from that source's local store instead of a single
  name (the two are mutually exclusive). Idempotent (skips models already in the
  library, so it doubles as a sync) and resilient (a failing model is logged and
  the batch continues; exit code is non-zero if any failed).

## `kodo run <name>`

Serve a library model (OpenAI `/v1`; GGUF also gets llama-server's web chat UI).

```bash
kodo run <name>
kodo run <name> --host 0.0.0.0 --port 9000
kodo run <name> --format gguf         # disambiguate across formats
```

## `kodo chat <name>`

Chat with a library model — interactive by default, one-shot with `-p`.

```bash
kodo chat <name>                      # interactive (llama-cli / mlx_lm.chat)
kodo chat <name> -p "prompt"          # one-shot, prints just the answer (pipeable)
kodo chat <name> -p "prompt" -n 256   # --max-tokens
```

Non-chat models (embeddings, vision encoders) are refused with a clear message —
kodo runs generative LLMs only.

## `kodo serve`

Run the web server (browse API + `/v1` proxy; browser UI with `--ui`).

```bash
kodo serve --ui                       # browse + chat, switch models
kodo serve --ui --model <name>        # locked single-model mode (extension backend)
kodo serve --reload                   # dev auto-reload
```

Equivalent Makefile targets: `make run` and `make run MODEL=<name>`.
