# The library

A **library** is a self-contained, portable store for your models — the model
files **plus their own metadata** (tags, cards) under `<root>/.kodo/`. Because the
metadata lives *inside* the library, the whole thing travels: move the drive to
another machine and your tags come with it.

The **default library** is `KODO_LIBRARY_ROOT` (set per machine, e.g. an external
drive). A [project](../guides/projects.md) can compose more libraries in front of
it. List what's in scope with:

```bash
kodo library ls
```

## Layout

Models are organized by **format**, with the Ollama store kept in its native
(restorable) layout:

```
<root>/
├── gguf/<publisher>/<repo>/…          # *.gguf          (HF + LM Studio pulls)
├── mlx/<publisher>/<repo>/…           # *.safetensors + config.json
├── safetensors/<publisher>/<repo>/…   # full-precision weights
├── huggingface/<repo_id>/…            # fallback for repos with no recognizable weights
└── ollama/manifests/… + ollama/blobs/ # Ollama's content-addressed store
```

Both Hugging Face and LM Studio pulls land in the **format** bucket (`gguf/`, `mlx/`,
`safetensors/`), so the same GGUF from either source is one copy on disk.

!!! tip "Migrating an older library"
    Libraries built before this used `huggingface/<repo>`. Reorganize them into the format
    buckets with `kodo library migrate` — a dry-run prints the plan; `--apply` performs it (a
    same-drive rename per model, and it removes any copy already duplicated in a bucket).

The scanner finds runnable models **anywhere** under the root (any directory with
`*.gguf`/`*.safetensors`, plus the Ollama native store). When a GGUF repo ships
several quants, a balanced one (`Q4_K_M` first) is picked automatically. macOS
`._` AppleDouble files are ignored.

!!! note "Ollama new-format models"
    Some Ollama models use a per-tensor format (hundreds of `.tensor` blobs)
    rather than a single GGUF — those can only run via Ollama itself, not
    llama.cpp, so they won't appear as runnable.

## Installing a model into a runtime

The library keeps **one canonical copy** per `(model, format)`. Some runtimes read
a loose GGUF/MLX in place (LM Studio, llama.cpp), but **Ollama** keeps a
content-addressed blob store and needs the GGUF *imported* first. `kodo library
install` feeds it from the canonical copy, so the drive stays the single source of
truth and the Ollama copy is regenerable:

```bash
kodo library install Qwen3.5-4B-GGUF            # → ollama create qwen3.5-4b
kodo library install Qwen3.5-4B-GGUF --name qwen-fast --system "Be terse."
ollama run qwen3.5-4b
```

It generates a Modelfile pointing at the canonical GGUF (`FROM <path>`, plus the
vision projector and an optional `SYSTEM` prompt) and runs `ollama create`. A local
Ollama daemon must be running (`ollama serve` or the app). Only GGUF installs into
Ollama — MLX/safetensors aren't supported there.

## Model cards & metadata

Each pulled model gets a `.kodo/` sidecar with `metadata.json` and a
`model-card.md` (the upstream README for HF/LM Studio; a generated Modelfile-style
card from the manifest layers for Ollama) — so every model carries its run
instructions. **Tags** live in one `<root>/.kodo/tags.json` per library.

## Composing libraries in a project

A project (`kodo.toml`) can use more than one library, in priority order — its own
**project-local** library plus the shared/default one — so you can keep a few hot
models next to a project while still using the big archive:

```toml
libraries = ["models", "@shared"]   # project-local first, then the machine default
```

`@shared` is the token for the machine's default library (`KODO_LIBRARY_ROOT`), so
the file stays portable. `kodo project init` scaffolds a `models/` directory and
this list; reads span all listed libraries (first match wins), while `kodo library
pull` targets the first (project-local) one by default (`--shared` for the shared
one). See [Projects](projects.md).

## Storage on an external drive

A library is portable — point `KODO_LIBRARY_ROOT` at wherever it lives on each
machine; the models and their tags come along.

- **exFAT** is a good choice for a drive shared between macOS and Linux (the only
  filesystem both read/write natively). No journaling — **eject cleanly**; no
  symlinks — dedup is store-once-and-copy, not by link.
- Mount paths differ per machine (e.g. `/Volumes/<drive>` on macOS vs
  `/media/<user>/<drive>` on Linux) — set `library_root` in `kodo.toml`, or override
  it per machine with `KODO_LIBRARY_ROOT`.
- Re-downloadable weights make the lack of journaling low-stakes.

!!! tip "Runtime assets travel too"
    Some runtimes fetch assets by Hugging Face repo id rather than from the library — e.g.
    mlx-audio's Dia loads its DAC codec that way. So they don't get left behind in
    `~/.cache/huggingface`, kodo points `HF_HOME` at `<library_root>/.cache/huggingface` (unless
    you've set `HF_HOME`/`HF_HUB_CACHE` yourself). Run `kodo voice setup` once to seed Dia's codec
    onto the drive; Dia then works offline and travels with it.
