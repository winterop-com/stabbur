# The library

A **library** is a self-contained, portable store for your models — the model
files **plus their own metadata** (tags, cards) under `<root>/.stabbur/`. Because the
metadata lives *inside* the library, the whole thing travels: move the drive to
another machine and your tags come with it.

The **default library** is `STABBUR_LIBRARY_ROOT` (set per machine, e.g. an external
drive). A [project](../guides/projects.md) can compose more libraries in front of
it. List what's in scope with:

```bash
sb library ls
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
    buckets with `sb library migrate` — a dry-run prints the plan; `--apply` performs it (a
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
sb library cards            # fetch a missing README from HF into each model's .stabbur/ sidecar
sb library cards --refresh  # re-fetch even models that already have a card
```

It infers the HF repo from the model's `<publisher>/<repo>` name, is idempotent (skips models that
already have a card), and never fails on a model that isn't on the Hub. Ollama models keep their
generated card (built from the manifest).

## Installing a model into a runtime

The library keeps **one canonical copy** per `(model, format)`. Some runtimes read
a loose GGUF/MLX in place (LM Studio, llama.cpp), but **Ollama** keeps a
content-addressed blob store and needs the GGUF *imported* first. `sb library
install` feeds it from the canonical copy, so the drive stays the single source of
truth and the Ollama copy is regenerable:

```bash
sb library install Qwen3.5-4B-GGUF            # → ollama create qwen3.5-4b
sb library install Qwen3.5-4B-GGUF --name qwen-fast --system "Be terse."
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
sb library install Qwen3.5-4B-GGUF --to lmstudio      # symlink into LM Studio's models dir
sb library install gemma-4-26B-A4B-MLX --to lmstudio  # MLX works too (--format to disambiguate)
```

This symlinks `<lmstudio_models_dir>/<publisher>/<repo>` to the library copy (the link lives on
the machine disk, so exFAT's no-symlink limit doesn't apply — **zero bytes copied**). It's
idempotent and won't clobber a model LM Studio downloaded itself; rescan/restart LM Studio to
see it. `STABBUR_LMSTUDIO_MODELS_DIR` overrides the target. To surface **every** model at once
instead, point LM Studio at the whole `gguf/` (and `mlx/`) bucket as a models directory.

**mlx_lm** needs no install step at all: `mlx_lm.server` / `mlx_lm.generate` run a loose MLX
model in place, and the library's `mlx/<publisher>/<repo>/` is exactly that. Point it at the
model directory — e.g. `mlx_lm.server --model "$STABBUR_LIBRARY_ROOT/mlx/mlx-community/Qwen3.6-27B-4bit"`
(stabbur itself serves MLX this way). So there's no `--to mlx_lm`: it already reads the canonical copy.

### Seeing and undoing installs

The install is reversible, and you can see what's fed where — the library always keeps the
canonical copy:

```bash
sb library installed                            # which runtimes each model is installed into
sb library uninstall Qwen3.5-4B-GGUF --from lmstudio   # remove stabbur's LM Studio symlink
sb library uninstall Qwen3.5-4B-GGUF --from ollama     # ollama rm the imported copy
```

`installed` cross-references the drive against LM Studio (a stabbur symlink pointing into the
library) and Ollama (a model held under the deterministic install name, or under a name the
model's sidecar recorded — that's how an `install --name qwen-fast` stays findable, since Ollama
copies the GGUF into its own store and keeps no pointer back to the library). `uninstall` picks
the same way, so it removes the right Ollama model without you having to remember the name.
It removes only what stabbur put there — never a real LM Studio download, and never the library
copy.

## Which formats to keep

Format is a **per-model choice**, not "keep every format of everything":

- **GGUF** — the portable, cross-runtime backbone (Ollama, LM Studio, llama.cpp; Mac + Linux).
  Keep for anything you want to run widely. The most shareable tier.
- **MLX** — Apple-Silicon native and fastest on the Mac. Keep for models you run locally on an
  M-series machine. (MLX repos are just HF repos, so no separate downloader.)
- **safetensors** — original full-precision weights, 2–4× the size of a quant. Keep **only** for
  models you'll re-quantize or fine-tune — not blanket. Pull on demand, drop when done.

Default policy: keep **GGUF + MLX** ready for a model you actually use; fetch safetensors only
when you need to convert or train. `sb library rm <model> --format safetensors` reclaims space
once a conversion is done.

`sb library formats` makes this actionable: one row per model with a column per format present
and their sizes, flagging any **redundant** safetensors copy (a GGUF/MLX build already exists) and
any model that's **only** safetensors (no ready-to-run quant), with the total space reclaimable by
dropping the redundant copies.

## Checking integrity

`sb library verify` checks each model on disk is intact — the declared weights (and vision
projector) exist and are non-empty, and the recorded model card is present:

```bash
sb library verify            # all models
sb library verify Ornith     # one
sb library verify --deep     # also re-hash Ollama blobs against their sha256
```

Ollama's store is content-addressed, so `--deep` gives true content integrity there. HF/LM Studio
pulls carry no per-file checksums, so their check is structural (present + non-empty).

A model marked `~` passed, with a note: its sidecar was written before stabbur stopped counting
download bookkeeping (`.cache/`, `.stabbur/`, macOS `._` files) in the recorded totals, so it now
measures a few small files short. That is how the numbers were recorded, not damage to the model,
and it doesn't make `verify` exit non-zero. Two bounds keep that narrow — the missing bytes have to
average small per missing file *and* be a rounding error against the model — so a truncated pull, or
a small model that lost most of itself, still fails loudly.

## Rebuild a drive

Because every model already records where it came from, the library **is** its own manifest.
`sb library manifest --save models.toml` writes a portable want list — a `[[model]]` entry per
model (source + name + format, plus the `include` globs a partial pull used). Keep that file anywhere (commit it to a repo, drop it on another
machine); nothing is stored back in the library. On a fresh or replacement drive, point
`STABBUR_LIBRARY_ROOT` at it and run `sb library sync models.toml`: it diffs the list against what's
present and re-pulls only what's missing, via the normal per-source paths (`--dry-run` first to
preview). One model failing doesn't stop the rest, and it exits non-zero if any did. LM Studio
backups re-pull from their Hugging Face equivalent; Ollama entries need the model in your local
Ollama store first (`ollama pull <name>`). See [`sb library manifest` / `sync`](../cli.md).

## Model cards & metadata

Each pulled model gets a `.stabbur/` sidecar with `metadata.json` and a
`model-card.md` (the upstream README for HF/LM Studio; a generated Modelfile-style
card from the manifest layers for Ollama) — so every model carries its run
instructions. **Tags** live in one `<root>/.stabbur/tags.json` per library.

## Composing libraries in a project

A project (`stabbur.toml`) can use more than one library, in priority order — its own
**project-local** library plus the shared/default one — so you can keep a few hot
models next to a project while still using the big archive:

```toml
libraries = ["models", "@shared"]   # project-local first, then the machine default
```

`@shared` is the token for the machine's default library (`STABBUR_LIBRARY_ROOT`), so
the file stays portable. `sb init` scaffolds a `library/` directory and
this list; reads span all listed libraries (first match wins), while `sb library
pull` targets the first (project-local) one by default (`--shared` for the shared
one). See [Projects](projects.md).

## Storage on an external drive

A library is portable — point `STABBUR_LIBRARY_ROOT` at wherever it lives on each
machine; the models and their tags come along.

- **exFAT** is a good choice for a drive shared between macOS and Linux (the only
  filesystem both read/write natively). No journaling — **eject cleanly**; no
  symlinks — dedup is store-once-and-copy, not by link.
- Mount paths differ per machine (e.g. `/Volumes/<drive>` on macOS vs
  `/media/<user>/<drive>` on Linux) — set `library_root` in `stabbur.toml`, or override
  it per machine with `STABBUR_LIBRARY_ROOT`.
- Re-downloadable weights make the lack of journaling low-stakes.

!!! tip "Runtime assets travel too"
    Some runtimes fetch assets by Hugging Face repo id rather than from the library — e.g.
    mlx-audio's Dia loads its DAC codec that way. So they don't get left behind in
    `~/.cache/huggingface`, stabbur points `HF_HOME` at `<library_root>/.cache/huggingface` (unless
    you've set `HF_HOME`/`HF_HUB_CACHE` yourself). Run `sb voice setup` once to seed Dia's codec
    onto the drive; Dia then works offline and travels with it.
