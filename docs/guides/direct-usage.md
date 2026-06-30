# Using models directly

You don't have to go through `kodo` to run a library model — the files are
plain GGUF/MLX and work with the standard tools. Library layout:

```
<root>/gguf/<publisher>/<repo>/…   # *.gguf  → llama.cpp, Ollama, LM Studio
<root>/mlx/<publisher>/<repo>/…    # *.safetensors (+ config.json) → mlx_lm, LM Studio
```

All servers below expose an OpenAI-compatible API at `http://localhost:<port>/v1`.

## GGUF — llama.cpp

```bash
GGUF="$KODO_BACKUP_ROOT/gguf/lmstudio-community/gemma-4-12B-it-QAT-GGUF/gemma-4-12B-it-QAT-Q4_0.gguf"

llama-cli   -m "$GGUF" --single-turn -p "Explain MoE in one sentence." # one reply, then exits
llama-cli   -m "$GGUF" -cnv                                   # interactive chat
llama-server -m "$GGUF" --host 127.0.0.1 --port 8080 -c 8192 # OpenAI API + web UI
# multimodal: add  --mmproj ".../mmproj-*.gguf"
# tool calling: --jinja (default in recent builds)
```

## GGUF — Ollama

Ollama won't run a loose file in place; import once (it copies into its store):

```bash
printf 'FROM %s\n' "$GGUF" > Modelfile
ollama create my-model -f Modelfile
ollama run my-model            # also serves OpenAI-compatible on :11434/v1
```

## MLX (Apple Silicon)

```bash
MLX="$KODO_BACKUP_ROOT/mlx/lmstudio-community/Qwen3.5-4B-MLX-4bit"

mlx_lm.generate --model "$MLX" --prompt "Hi" --max-tokens 200   # one-shot
mlx_lm.chat     --model "$MLX"                                   # interactive
mlx_lm.server   --model "$MLX" --host 127.0.0.1 --port 8081      # OpenAI API
```

## LM Studio

LM Studio reads loose GGUF directly and loads MLX directories on Apple Silicon —
point its models directory at the library or copy the `<publisher>/<repo>` folder
in.

## Attaching a client

Point any OpenAI-API client (your own scripts, coding TUIs, etc.) at a running
server:

```bash
export OPENAI_BASE_URL="http://localhost:8080/v1"
export OPENAI_API_KEY="sk-noop"      # local servers ignore the key
```
