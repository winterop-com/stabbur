# CLI reference

The CLI is the **`kodo`** command. Everything centers on your **library** — the
models in your libraries (a project's, or the default `KODO_LIBRARY_ROOT`). Run any command
with `--help` for full options.

**Global flag:** `kodo --debug <command>` turns on verbose diagnostics — it prints
the exact model-runtime command and streams the runtime's logs live (instead of
discarding them), which is the first thing to reach for when `kodo chat`
reports a model that "exited before becoming ready". (Also settable with
`KODO_DEBUG=1`.)

## `kodo project init`

Scaffold **`kodo.toml`** here — kodo's primary config (no `.env` needed) — and
ensure its model is in the library. The generated file is portable — it lists the
`libraries` this project uses (a project-local `models/` plus `@shared`, the
machine default) plus the assistant (`[project]` model + `[[mcp]]` tools).
Idempotent — only pulls the model if it's missing. With no `--model` it
offers a small curated set and pulls the choice into the project-local library.

```bash
kodo project init                                  # pick a curated starter model
kodo project init --model unsloth/Qwen3.5-4B-GGUF  # bind a specific model
kodo project init --force                          # overwrite an existing kodo.toml
```

A project is a **reproducible assistant**: in a project directory both `kodo chat`
and `kodo serve --ui` default to its model, system prompt, and MCP tool servers —
so `kodo serve --ui` boots straight into that model, no manual picking.

## `kodo project show`

Show the active project (`kodo.toml`) in full: the bound model's detail card
(format, size, capabilities, context, tags, path), the system prompt, and the
**actual tools** — it connects to the project's MCP servers and lists the tools
they expose (with descriptions), not just the server names. `--card` also renders
the bound model's model card (README).

```bash
kodo project show
kodo project show --card    # also print the model card (README)
```

## `kodo library search <query>`

Search the Hugging Face Hub for new models to pull (most-downloaded first).

```bash
kodo library search qwen3            # text search
kodo library search qwen3 --gguf     # only GGUF (llama.cpp-ready) repos
kodo library search qwen3 -n 30      # more results
```

## `kodo library ls`

List the models in **your library** — what you've pulled, ready to run — grouped
by format with sizes across the libraries in scope.

```bash
kodo library ls
kodo library ls -d     # detailed cards (caps, context, location, path, tags)
```

## `kodo library rm <name>`

Remove a model from the library — **deletes its files from disk**. Resolves like
`kodo chat` (use `--format` to disambiguate a model kept in more than one format);
all copies of the model are removed (e.g. one on the local disk and one on the
drive). Ollama models keep any blobs still shared with other installed models.
Prompts for confirmation unless `--yes`.

```bash
kodo library rm Voxtral-Mini-3B-2507-GGUF          # confirm, then delete
kodo library rm gemma-4-E4B-it-MLX-4bit --yes      # skip the prompt
kodo library rm Qwen3.6-27B --format mlx           # disambiguate when kept in two formats
```

## `kodo library sources`

Browse models sitting in your **app caches** (Hugging Face cache, Ollama, LM
Studio) that you could pull into the library. The IN LIBRARY column marks what
you already have. Non-chat (embedding/vision) and partial entries are hidden
unless `--all`.

```bash
kodo library sources
kodo library sources -s ollama       # --source: limit to one source
kodo library sources --all           # include embedding/vision/partial entries
```

## `kodo library pull <source> <name>`

Copy a model from a source cache into the library.

```bash
kodo library pull lmstudio <name>
kodo library pull ollama gemma4:31b --move    # delete the local source after a verified copy
kodo library pull ollama --all                # import every model from the local store
kodo library pull lmstudio --all --move       # import all, freeing local disk as it goes
# Hugging Face:
kodo library pull huggingface lmstudio-community/gemma-4-12B-it-QAT-GGUF --include '*Q4_K_M*'
kodo library pull huggingface OuteAI/OuteTTS-0.2-500M-GGUF --include '*Q4_K_M*' \
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
  with the model so it's recognized as a **text-to-speech** model (see `kodo audio speak`).

## `kodo chat <name>`

Chat with a library model — a full-screen Textual TUI by default, one-shot with `-p`.

```bash
kodo chat <name>                      # interactive full-screen TUI
kodo chat <name> -p "prompt"          # one-shot, prints just the answer (pipeable)
kodo chat <name> -p "prompt" -n 256   # --max-tokens
kodo chat <name> --system "..."       # session system prompt (overrides kodo.toml)
kodo chat <name> --mcp <cmd>          # attach an MCP tool server (repeatable)
```

Interactive chat opens a scrolling TUI: markdown replies, collapsible reasoning,
live tool activity, and a context footer. Enter sends; Shift+Return / Ctrl-J / a
trailing backslash insert a newline; type a new message while one streams to
**queue** it; Esc stops. `-p` stays a plain scripted one-shot (streamed stdout).

**Multimodal input** — for vision/audio models, attach files:

```bash
kodo chat <name> -p "what is this?" --image photo.jpg    # vision model
kodo chat <name> -p "transcribe" --audio clip.wav         # audio model
```

`--image`/`-i` and `--audio`/`-a` are repeatable. In the REPL you can also just
**drag a file into the terminal** — the inserted path is detected and attached:
image/audio go as OpenAI multimodal content (kodo warns if the model lacks that
modality), while **text/code files** (`.md`, `.py`, `.json`, …) are inlined into
the prompt as fenced blocks, so you can drop a file into *any* model as context.

Non-chat models (embeddings, vision encoders) are refused with a clear message —
kodo runs generative LLMs only.

## `kodo audio voices`

List the built-in **Kokoro** voices (54 across 9 languages) with their id,
language, and gender. Requires the optional TTS extra (`make install-tts`).

```bash
kodo audio voices                            # id · name · language · gender
```

## `kodo audio speak <text...>`

Text-to-speech. `--voice`/`-v` picks a **Kokoro** voice (multi-voice engine; run
`kodo audio voices` to list them, model downloaded on first use). Otherwise it uses
`llama-tts`/OuteTTS — the default, or `--model` for a library TTS model (see
`kodo library pull --vocoder`). Markdown/code in the text is reduced to prose first.

```bash
kodo audio speak hello there                 # default voice, play aloud (macOS)
kodo audio speak -v af_heart "hello there"   # a specific Kokoro voice
kodo audio speak "some text" -o out.wav      # write a WAV instead of playing
kodo audio speak hi --model OuteTTS-0.2-500M-GGUF   # a specific library OuteTTS model
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
