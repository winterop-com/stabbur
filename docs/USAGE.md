# Using the library directly

The library stores models by **format** under `$LOCAL_LLM_BACKUP_ROOT`
(this Mac: `/Volumes/LLM/Library`):

```
Library/
├── gguf/<publisher>/<repo>/…   # *.gguf  → llama.cpp, Ollama, LM Studio
└── mlx/<publisher>/<repo>/…    # *.safetensors (+ config.json) → mlx_lm, LM Studio
```

Every model has a `.local-llm/` sidecar (`metadata.json`, and a `model-card.md`
for run instructions where available).

All servers below expose an **OpenAI-compatible** API at
`http://localhost:<port>/v1`, so any OpenAI-API client (claude, opencode, pi,
hermes, `openai` SDK, etc.) can attach — see [Client TUIs](#client-tuis).

---

## GGUF

### llama.cpp (primary, cross-platform)

Install: `brew install llama.cpp` (macOS) or build from source on Linux.

Pick the single `.gguf` weight file (ignore any `mmproj-*.gguf`, which is a
vision projector loaded separately with `--mmproj`).

```bash
GGUF="/Volumes/LLM/Library/gguf/lmstudio-community/gemma-4-12B-it-QAT-GGUF/gemma-4-12B-it-QAT-Q4_0.gguf"

# One-shot
llama-cli -m "$GGUF" -p "Explain MoE in one sentence."

# OpenAI-compatible server on :8080
llama-server -m "$GGUF" --host 127.0.0.1 --port 8080 -c 8192
# multimodal: add  --mmproj ".../mmproj-gemma-4-12B-it-QAT-BF16.gguf"
```

### Ollama

Ollama will not run a loose file in place; import it once into its store via a
Modelfile (it copies the blob in — the library keeps the canonical copy):

```bash
cat > Modelfile <<EOF
FROM $GGUF
# optional: TEMPLATE / SYSTEM / PARAMETER lines (see the model-card.md sidecar)
EOF

ollama create gemma-4-12b-qat -f Modelfile
ollama run gemma-4-12b-qat
```

Ollama also serves OpenAI-compatible at `http://localhost:11434/v1`.

### LM Studio

LM Studio reads loose GGUF from its models directory. Either keep using its own
`~/.lmstudio/models`, or point/copy from the library following the
`<publisher>/<repo>/<file>.gguf` layout (the same layout the library uses), then
"My Models" → load. Its server runs at `http://localhost:1234/v1`.

---

## MLX (Apple Silicon only)

MLX dirs hold `config.json` + `*.safetensors` (+ tokenizer files).

### mlx_lm

```bash
uv tool install mlx-lm        # or: pip install mlx-lm
MLX="/Volumes/LLM/Library/mlx/lmstudio-community/Qwen3.5-4B-MLX-4bit"

# One-shot
mlx_lm.generate --model "$MLX" --prompt "Hi" --max-tokens 200

# OpenAI-compatible server on :8081
mlx_lm.server --model "$MLX" --host 127.0.0.1 --port 8081
```

### LM Studio

On Apple Silicon, LM Studio loads MLX directories directly (same as GGUF, via its
models folder).

---

## Client TUIs

Point any OpenAI-API client at a running server. The base URL is the server's
`/v1`; the model name is whatever that server reports (`/v1/models`).

```bash
# Example: a llama-server running on :8080
export OPENAI_BASE_URL="http://localhost:8080/v1"
export OPENAI_API_KEY="sk-noop"          # local servers ignore the key

# opencode / claude / pi / hermes — consult each tool's flags for base-url/model,
# e.g. many accept OPENAI_BASE_URL + OPENAI_API_KEY directly, or a --model flag.
opencode
```

Planned: the `llm` browser (Textual TUI) will do this for you — select a model,
start the right server (`llama-server` for GGUF, `mlx_lm.server` for MLX), and
launch your chosen client against the local endpoint.
