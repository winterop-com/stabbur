# Running & chatting

All runtimes expose an **OpenAI-compatible** API at `http://<host>:<port>/v1`, so
any OpenAI client attaches the same way.

| Format | Runtime | Notes |
| ------ | ------- | ----- |
| GGUF   | llama.cpp `llama-server` / `llama-cli` | cross-platform; built-in web chat UI |
| MLX    | `mlx_lm.server` / `mlx_lm.chat`        | Apple Silicon only |

## Serve a model

```bash
llm run <name>                       # default 127.0.0.1:8080
llm run <name> --host 0.0.0.0 --port 9000
```

`run` resolves the model in the library, starts the right runtime, and prints the
endpoints. For GGUF it also prints the **web chat UI** link (llama-server ships
one); for MLX use `llm chat`.

```
Serving gguf lmstudio-community/gemma-4-12B-it-QAT-GGUF
  Chat UI:     http://127.0.0.1:8080
  OpenAI API:  http://127.0.0.1:8080/v1
```

## Chat

```bash
llm chat <name>                      # interactive terminal chat
llm chat <name> -p "Summarize MoE"   # one-shot: prints only the answer (pipeable)
llm chat <name> -p "..." -n 256      # cap generated tokens
```

The `-p/--prompt` one-shot mirrors `claude -p` — clean stdout for scripting:

```bash
llm chat gemma-4-12B-it-QAT-GGUF -p "Say hi" 2>/dev/null | tee out.txt
```

## Picking a model name

Use the full `<publisher>/<repo>` name or just the final part; add `--format` to
disambiguate if the same model exists in multiple formats. If a name isn't in the
library but is in a source store, the error tells you the `llm pull` to run.
