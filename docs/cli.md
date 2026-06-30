# CLI reference

The CLI is the **`kodo`** command (with a hidden `ls` alias for `list`). Run any
command with `--help` for full options.

## `kodo list`

List models in the local **source stores** (HF cache, Ollama, LM Studio), grouped
by source, with an **IN LIBRARY** column showing what's already pulled.

```bash
kodo list
kodo list -s ollama          # --source: limit to one source
```

## `kodo library`

List models in the on-drive **library** (`KODO_BACKUP_ROOT`), grouped by
format with sizes.

```bash
kodo library
```

## `kodo pull <source> <name>`

Copy a model from a source store into the library.

```bash
kodo pull lmstudio <name>
kodo pull ollama gemma4:31b --move    # delete the local source after a verified copy
```

- `--move` — relocate (copy, verify byte-for-byte, then delete the source).
  Supported for LM Studio and Ollama.

## `kodo run <name>`

Serve a library model (OpenAI `/v1`; GGUF also gets llama-server's web chat UI).

```bash
kodo run <name>
kodo run <name> --host 0.0.0.0 --port 9000
kodo run <name> --format gguf         # disambiguate across formats
```

## `kodo chat <name>`

Chat with a model — interactive by default, one-shot with `-p`.

```bash
kodo chat <name>                      # interactive (llama-cli / mlx_lm.chat)
kodo chat <name> -p "prompt"          # one-shot, prints just the answer (pipeable)
kodo chat <name> -p "prompt" -n 256   # --max-tokens
```

## `kodo serve`

Run the web server (browse API + `/v1` proxy; browser UI with `--ui`).

```bash
kodo serve --ui                       # browse + chat, switch models
kodo serve --ui --model <name>        # locked single-model mode (extension backend)
kodo serve --reload                   # dev auto-reload
```

Equivalent Makefile targets: `make run` and `make run MODEL=<name>`.
