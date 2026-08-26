# Pulling models

`sb library pull` copies a model from a local **source store** (the Hugging Face cache,
Ollama, or LM Studio) into the library.

```bash
sb library pull <source> <name>          # source: huggingface | ollama | lmstudio
sb library pull lmstudio lmstudio-community/gemma-4-12B-it-QAT-GGUF
sb library pull huggingface unsloth/SmolLM2-135M-Instruct-GGUF
sb library pull ollama gemma4:31b
```

## See what's available vs already pulled

```bash
sb library sources
```

`sb library sources` shows models in your app caches with an **IN LIBRARY** column
(✓ = already pulled) and a summary like `12 models · 3 already in library · 9 to
pull`. It's the "what can I pull" view; [`sb library ls`](library.md) is the "what
do I have" (your library) view.

## Move instead of copy

`--move` deletes the local source **after a verified, byte-for-byte copy** — use
it to relocate models onto the drive and free local disk:

```bash
sb library pull lmstudio <name> --move
sb library pull ollama gemma4:31b --move      # preserves blobs shared with other models
```

!!! note "Source support"
    `--move` is implemented for **LM Studio** and **Ollama** today. For Ollama it
    removes the manifest and only the blobs no other model still references.
    Hugging Face pulls download from the hub, so `--move` there is not applicable.

## Per-format destinations

LM Studio and HF land in the format-centric layout (`gguf/`, `mlx/`, …); Ollama
keeps its native store. Either way the model shows up in `sb library ls` and is
runnable. See [The library](library.md) for the full layout.
