# The library

The library is the on-drive home for your models, under `KODO_BACKUP_ROOT`.
List it with:

```bash
kodo ls
```

## Layout

Models are organized by **format**, with the Ollama store kept in its native
(restorable) layout:

```
<root>/
├── gguf/<publisher>/<repo>/…          # *.gguf
├── mlx/<publisher>/<repo>/…           # *.safetensors + config.json
├── huggingface/<repo_id>/…            # HF snapshots (classified on scan)
└── ollama/manifests/… + ollama/blobs/ # Ollama's content-addressed store
```

The scanner finds runnable models **anywhere** under the root (any directory with
`*.gguf`/`*.safetensors`, plus the Ollama native store). When a GGUF repo ships
several quants, a balanced one (`Q4_K_M` first) is picked automatically. macOS
`._` AppleDouble files are ignored.

!!! note "Ollama new-format models"
    Some Ollama models use a per-tensor format (hundreds of `.tensor` blobs)
    rather than a single GGUF — those can only run via Ollama itself, not
    llama.cpp, so they won't appear as runnable.

## Model cards & metadata

Each pulled model gets a `.kodo/` sidecar with `metadata.json` and a
`model-card.md` (the upstream README for HF/LM Studio; a generated Modelfile-style
card from the manifest layers for Ollama) — so every model carries its run
instructions.

## Storage on an external drive

The library is designed to live on a large external/cloud drive; moving it is a
one-line change to `KODO_BACKUP_ROOT`.

- **exFAT** is a good choice for a drive shared between macOS and Linux (the only
  filesystem both read/write natively). No journaling — **eject cleanly**; no
  symlinks — dedup is store-once-and-copy, not by link.
- Mount paths differ per machine (e.g. `/Volumes/LLM` on macOS vs
  `/media/<user>/LLM` on Linux) — set the env var per machine.
- Re-downloadable weights make the lack of journaling low-stakes.
