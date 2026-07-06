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

## Model cards

Each model's card (its README) is what the UI/CLI info panel shows. Hugging Face pulls ship one;
some LM Studio downloads (and older pulls) don't. Backfill the missing ones:

```bash
kodo library cards            # fetch a missing README from HF into each model's .kodo/ sidecar
kodo library cards --refresh  # re-fetch even models that already have a card
```

It infers the HF repo from the model's `<publisher>/<repo>` name, is idempotent (skips models that
already have a card), and never fails on a model that isn't on the Hub. Ollama models keep their
generated card (built from the manifest).

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

**LM Studio** reads loose GGUF/MLX directly, and the library's `gguf/<publisher>/<repo>/`
and `mlx/<publisher>/<repo>/` buckets already match LM Studio's layout — so it needs no copy,
just a pointer:

```bash
kodo library install Qwen3.5-4B-GGUF --to lmstudio      # symlink into LM Studio's models dir
kodo library install gemma-4-26B-A4B-MLX --to lmstudio  # MLX works too (--format to disambiguate)
```

This symlinks `<lmstudio_models_dir>/<publisher>/<repo>` to the library copy (the link lives on
the machine disk, so exFAT's no-symlink limit doesn't apply — **zero bytes copied**). It's
idempotent and won't clobber a model LM Studio downloaded itself; rescan/restart LM Studio to
see it. `KODO_LMSTUDIO_MODELS_DIR` overrides the target. To surface **every** model at once
instead, point LM Studio at the whole `gguf/` (and `mlx/`) bucket as a models directory.

**mlx_lm** needs no install step at all: `mlx_lm.server` / `mlx_lm.generate` run a loose MLX
model in place, and the library's `mlx/<publisher>/<repo>/` is exactly that. Point it at the
model directory — e.g. `mlx_lm.server --model "$KODO_LIBRARY_ROOT/mlx/mlx-community/Qwen3.6-27B-4bit"`
(kodo itself serves MLX this way). So there's no `--to mlx_lm`: it already reads the canonical copy.

### Seeing and undoing installs

The install is reversible, and you can see what's fed where — the library always keeps the
canonical copy:

```bash
kodo library installed                            # which runtimes each model is installed into
kodo library uninstall Qwen3.5-4B-GGUF --from lmstudio   # remove kodo's LM Studio symlink
kodo library uninstall Qwen3.5-4B-GGUF --from ollama     # ollama rm the imported copy
```

`installed` cross-references the drive against LM Studio (a kodo symlink pointing into the
library) and Ollama (a model whose deterministic install name is present). `uninstall` removes
only what kodo put there — it never deletes a real LM Studio download, and never touches the
library copy.

## Which formats to keep

Format is a **per-model choice**, not "keep every format of everything":

- **GGUF** — the portable, cross-runtime backbone (Ollama, LM Studio, llama.cpp; Mac + Linux).
  Keep for anything you want to run widely. The most shareable tier.
- **MLX** — Apple-Silicon native and fastest on the Mac. Keep for models you run locally on an
  M-series machine. (MLX repos are just HF repos, so no separate downloader.)
- **safetensors** — original full-precision weights, 2–4× the size of a quant. Keep **only** for
  models you'll re-quantize or fine-tune — not blanket. Pull on demand, drop when done.

Default policy: keep **GGUF + MLX** ready for a model you actually use; fetch safetensors only
when you need to convert or train. `kodo library rm <model> --format safetensors` reclaims space
once a conversion is done.

## Checking integrity

`kodo library verify` checks each model on disk is intact — the declared weights (and vision
projector) exist and are non-empty, and the recorded model card is present:

```bash
kodo library verify            # all models
kodo library verify Ornith     # one
kodo library verify --deep     # also re-hash Ollama blobs against their sha256
```

Ollama's store is content-addressed, so `--deep` gives true content integrity there. HF/LM Studio
pulls carry no per-file checksums, so their check is structural (present + non-empty).

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
