# CLI reference

The CLI is the **`kodo`** command. Everything centers on your **library** — the
models on your drive (`KODO_BACKUP_ROOT`). Run any command with `--help` for
full options.

## `kodo list` (alias `kodo ls`)

List the models in **your library** — what you've pulled, ready to run — grouped
by format with sizes. Reads `KODO_BACKUP_ROOT`.

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
```

- `--move` — relocate (copy, verify byte-for-byte, then delete the local source
  to free disk). Supported for LM Studio and Ollama.

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
