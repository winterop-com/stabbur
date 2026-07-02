# CLI reference

The CLI is the **`kodo`** command. Everything centers on your **library** — the
models on your drive, at the `library_root` set in `kodo.toml`. Run any command
with `--help` for full options.

**Global flag:** `kodo --debug <command>` turns on verbose diagnostics — it prints
the exact model-runtime command and streams the runtime's logs live (instead of
discarding them), which is the first thing to reach for when `kodo chat`/`run`
reports a model that "exited before becoming ready". (Also settable with
`KODO_DEBUG=1`.)

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
# Hugging Face:
kodo pull huggingface lmstudio-community/gemma-4-12B-it-QAT-GGUF --include '*Q4_K_M*'
kodo pull huggingface OuteAI/OuteTTS-0.2-500M-GGUF --include '*Q4_K_M*' \
         --vocoder ggml-org/WavTokenizer          # a TTS model + its vocoder
```

- `--move` — relocate (copy, verify byte-for-byte, then delete the local source
  to free disk). Supported for LM Studio and Ollama.
- `--all` — import every model from that source's local store instead of a single
  name (the two are mutually exclusive). Idempotent (skips models already in the
  library, so it doubles as a sync) and resilient (a failing model is logged and
  the batch continues; exit code is non-zero if any failed).
- `--include <glob>` — Hugging Face only; fetch only matching files (repeatable),
  e.g. one GGUF quant from a multi-quant repo. Model cards and configs come along.
- `--vocoder <repo>` — Hugging Face only; co-locate a vocoder (e.g. WavTokenizer)
  with the model so it's recognized as a **text-to-speech** model (see `kodo speak`).

## `kodo run <name>`

Expose a library model's raw runtime server (foreground; OpenAI `/v1`).

```bash
kodo run <name>
kodo run <name> --host 0.0.0.0 --port 9000
kodo run <name> --format gguf         # disambiguate across formats
```

## `kodo chat <name>`

Chat with a library model — interactive by default, one-shot with `-p`.

```bash
kodo chat <name>                      # interactive streaming REPL
kodo chat <name> -p "prompt"          # one-shot, prints just the answer (pipeable)
kodo chat <name> -p "prompt" -n 256   # --max-tokens
kodo chat <name> --render             # render each reply as Markdown (code highlighting)
kodo chat <name> --system "..."       # session system prompt (overrides kodo.toml)
kodo chat <name> --mcp <cmd>          # attach an MCP tool server (repeatable)
```

By default replies **stream** token-by-token as plain text (fast, pipe-safe).
`--render` instead buffers each reply and prints it as **formatted Markdown** —
headers, lists, and syntax-highlighted fenced code — when it's done (so you lose
the live token stream). It's ignored under `-p` so scripted output stays plain.
Up-arrow recalls previous prompts.

**Multimodal input** — for vision/audio models, attach files:

```bash
kodo chat <name> -p "what is this?" --image photo.jpg    # vision model
kodo chat <name> -p "transcribe" --audio clip.wav         # audio model
```

`--image`/`-i` and `--audio`/`-a` are repeatable. In the REPL you can also just
**drag a file into the terminal** — the inserted path is detected and attached.
Sent as OpenAI multimodal content; kodo warns if the model lacks that modality.

Non-chat models (embeddings, vision encoders) are refused with a clear message —
kodo runs generative LLMs only.

## `kodo voices`

List the built-in **Kokoro** voices (54 across 9 languages) with their id,
language, and gender. Requires the optional TTS extra (`make install-tts`).

```bash
kodo voices                            # id · name · language · gender
```

## `kodo speak <text...>`

Text-to-speech. `--voice`/`-v` picks a **Kokoro** voice (multi-voice engine; run
`kodo voices` to list them, model downloaded on first use). Otherwise it uses
`llama-tts`/OuteTTS — the default, or `--model` for a library TTS model (see
`kodo pull --vocoder`). Markdown/code in the text is reduced to prose first.

```bash
kodo speak hello there                 # default voice, play aloud (macOS)
kodo speak -v af_heart "hello there"   # a specific Kokoro voice
kodo speak "some text" -o out.wav      # write a WAV instead of playing
kodo speak hi --model OuteTTS-0.2-500M-GGUF   # a specific library OuteTTS model
```

## `kodo doctor`

Pre-flight system health: are the runtime binaries kodo spawns installed
(`llama-server`, and `mlx_lm.server`/`mlx_vlm.server` on Apple Silicon), is the
library reachable and non-empty, and does the project point at a present model.
Exits non-zero if any check fails.

```bash
kodo doctor
```

## `kodo serve`

Run the web server (browse API + `/v1` proxy; browser UI with `--ui`).

```bash
kodo serve --ui                       # browse + chat, switch models (auto-picks a free port)
kodo serve --ui --port 8000           # pin the port for a stable URL
kodo serve --ui --model <name>        # locked single-model mode (extension backend)
kodo serve --reload                   # dev auto-reload
```

Equivalent Makefile targets: `make run` and `make run MODEL=<name>`.
