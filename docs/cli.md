# CLI reference

The CLI is exposed as **`llm`** (primary) and **`local-llm`** (alias). Run any
command with `--help` for full options.

## `llm list`

List models in the local **source stores** (HF cache, Ollama, LM Studio), grouped
by source, with an **IN LIBRARY** column showing what's already pulled.

```bash
llm list
llm list -s ollama          # --source: limit to one source
```

## `llm library`

List models in the on-drive **library** (`LOCAL_LLM_BACKUP_ROOT`), grouped by
format with sizes.

```bash
llm library
```

## `llm pull <source> <name>`

Copy a model from a source store into the library.

```bash
llm pull lmstudio <name>
llm pull ollama gemma4:31b --move    # delete the local source after a verified copy
```

- `--move` — relocate (copy, verify byte-for-byte, then delete the source).
  Supported for LM Studio and Ollama.

## `llm run <name>`

Serve a library model (OpenAI `/v1`; GGUF also gets llama-server's web chat UI).

```bash
llm run <name>
llm run <name> --host 0.0.0.0 --port 9000
llm run <name> --format gguf         # disambiguate across formats
```

## `llm chat <name>`

Chat with a model — interactive by default, one-shot with `-p`.

```bash
llm chat <name>                      # interactive (llama-cli / mlx_lm.chat)
llm chat <name> -p "prompt"          # one-shot, prints just the answer (pipeable)
llm chat <name> -p "prompt" -n 256   # --max-tokens
```

## `llm serve`

Run the web server (browse API + `/v1` proxy; browser UI with `--ui`).

```bash
llm serve --ui                       # browse + chat, switch models
llm serve --ui --model <name>        # locked single-model mode (extension backend)
llm serve --reload                   # dev auto-reload
```

Equivalent Makefile targets: `make run` and `make run MODEL=<name>`.
