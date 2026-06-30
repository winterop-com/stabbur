# Project rules

1. **No emojis.** Never use emojis anywhere — code, comments, docs, commit messages, PR descriptions, chat output.
2. **No Claude Code attribution.** Do not add `Co-Authored-By: Claude ...`, "Generated with Claude Code", or any similar attribution to commits, PRs, or files.
3. **Conventional Commits** for all git activity — commit messages, branch names, and PR titles.
   - Format: `<type>(<scope>)?: <description>` (e.g. `feat(ci): add docker publish workflow`, `fix(main): correct db path creation`).
   - Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`, `build`, `perf`, `style`, `revert`.
   - Branch names: `<type>/<short-description>` (e.g. `feat/makefile-and-ci`, `fix/sqlite-path`).
4. **Pydantic only, never `@dataclass`.** All data containers — config records, API response shapes, internal value types — must use `pydantic.BaseModel`. Reasons: consistent validation, JSON (de)serialization, FastAPI integration, predictable equality / repr. If you spot an existing `@dataclass` (legacy), convert it the next time you touch the file. Use `model_config = ConfigDict(frozen=True)` when you'd reach for `@dataclass(frozen=True)`.

## Goal

Build and maintain a **full local library of LLM models** by downloading /
backing up from **Hugging Face**, **Ollama**, and **LM Studio**. Browse the
library via a Typer CLI and a small FastAPI service.

Downloads currently land in the project-local `data/` directory. **Later this
moves to a dedicated 5TB WD Passport external drive** — when that happens, only
`LOCAL_LLM_BACKUP_ROOT` changes (e.g. `/Volumes/llm-5tb/library`); no code
changes required. `data/` is gitignored — model weights must never be committed.

## Stack & conventions

- Python 3.13, `uv` (with the `uv_build` backend), **src layout**.
- **Typer** CLI; entry point `local-llm = "local_llm.cli:app"`.
- **FastAPI + Pydantic + pydantic-settings** for the browse layer.
- **huggingface_hub** is the canonical HF client (resumable, checksummed).
- Lint/type/test config mirrors `../../chap-sdk/chapkit`: ruff (120 cols,
  google docstrings), mypy + pyright (strict), pytest. Run `make lint` and
  `make test` before committing.

## Layout

```
src/local_llm/
├── config.py    # Settings (LOCAL_LLM_* env vars); backup_root = data/ default
├── models.py    # ModelSource, ModelFormat, ModelEntry, Catalog, PullResult
├── catalog.py   # aggregates list/pull across sources
├── library.py   # scans the on-drive library (gguf/ mlx/ ...)
├── runtime.py   # serves a model (llama.cpp / mlx_lm)
├── cards.py     # model-card + metadata sidecar helpers
├── cli.py       # Typer app: list / pull / library / run / serve
├── app.py       # FastAPI factory
├── routers/     # health.py, catalog.py
└── sources/     # base.py + huggingface.py / ollama.py / lmstudio.py
```

## Library organization

LM Studio / HF land in a **format-centric** layout; Ollama keeps its native
(restorable) layout:

- `<root>/gguf/<publisher>/<repo>/…` and `<root>/mlx/<publisher>/<repo>/…` —
  format-centric (LM Studio pulls; HF too once wired).
- `<root>/huggingface/<repo_id>/…` — full snapshot (HF pull, current behavior).
- `<root>/ollama/manifests/…` + `<root>/ollama/blobs/…` — Ollama's native
  content-addressed layout so it stays **restorable**; shared blobs are
  preserved on `--move`.

`pull --move` deletes the local source after a verified (byte-for-byte) copy.

## Model cards / instructions

Every pull writes a `.local-llm/` sidecar (`metadata.json` + `model-card.md`):

- HF / LM Studio: the card is the existing `README.md`; metadata records it.
- Ollama: the card is **generated** from the manifest's text layers — system
  prompt, template, parameters, license (the info needed to run the model).
  Its sidecar lives at `data/ollama/.library/<model_tag>/`.

## Storage location

- Source code: stays on the Mac + GitHub (small, version-controlled).
- Model library: on a 5TB **exFAT** WD Passport, mounted `/Volumes/LLM` on this
  Mac → `LOCAL_LLM_BACKUP_ROOT=/Volumes/LLM/Library` (set in gitignored `.env`).
  exFAT chosen for Mac + Linux read/write; allocation block 256 KB (good for
  large weights). No journaling — eject cleanly. No symlinks/hardlinks on the
  volume, so dedup must be by "store once, copy to each runtime", not by link.
  On Linux the mount path differs; set `LOCAL_LLM_BACKUP_ROOT` per machine.

## Formats, runtimes & the shared library (intended direction)

The current code stores a separate tree per *source* (huggingface/ ollama/
lmstudio/), which duplicates weights. The intended direction is a **format-
centric, deduplicated library** keyed by `(model × format)`, sourced from HF,
with Ollama / LM Studio / mlx_lm as *consumers* fed from it:

- **GGUF** — cross-runtime quant (Ollama, LM Studio, llama.cpp; Mac + Linux).
  The portable backbone; the most shareable tier.
- **MLX** — Apple Silicon native (HF `mlx-community` repos; LM Studio on Mac,
  `mlx_lm`). Apple-only but fastest on the Mac. No separate downloader needed —
  MLX models are just HF repos, so the HF source already covers them.
- **safetensors** — original full-precision weights; the convert/fine-tune
  source. Large (2–4× the quant). Keep only for models we'll re-quantize or
  fine-tune, not blanket for every model.

Default library policy: keep **GGUF + MLX** (ready-to-run); safetensors on
demand. Format should be a per-model choice, not "keep all".

Sharing reality: LM Studio reads loose GGUF/MLX directly; Ollama imports a GGUF
into its own content-addressed blob store (`ollama create` / `ollama pull
hf.co/...`) and won't run a loose file in place. So the win is one canonical
library copy that we *install into* whichever runtime, not a single file used
live by all.

## UI — Textual TUI (primary), web optional

Primary UI is a **Textual TUI** model browser: it works over SSH to the Linux
boxes (no browser needed). FastAPI stays as the headless/programmatic API and a
*possible* later web UI. CLI, TUI, and FastAPI are all thin frontends over the
UI-agnostic catalog core — keep runtime/serving logic out of them.

If a web UI is built later, take inspiration from
`../../chap-sdk/chapkit/frontend/`: React 19 + Vite + Tailwind v4 + shadcn, pnpm,
Playwright.

## Running models — llama.cpp first, mlx_lm for MLX

Serving is OpenAI-compatible so any client can attach:

- **GGUF → llama.cpp `llama-server`** — primary runtime, cross-platform
  (Mac + Linux), OpenAI-compatible. Cannot run MLX.
- **MLX → `mlx_lm.server`** — Apple Silicon only, OpenAI-compatible.
- (Ollama / LM Studio also serve OpenAI-compatible endpoints; we lean on
  llama.cpp directly as the common denominator.)

The browser doubles as a **launcher**: select a model → start the right server →
launch a client TUI (claude, opencode, pi, hermes — all OpenAI-API) pointed at
`http://localhost:<port>/v1`. See `docs/USAGE.md` for the concrete commands.

## Open / next ideas

- Refactor toward the format-centric shared library above (the big one).
- `make check` is the CI gate (read-only); `make lint` mutates locally.
- Auto-fetch HF model cards for LM Studio models (infer repo from path).
- A "want list" / sync command to (re-)download a declared set of models.
- Verify/repair: re-check sizes & checksums against metadata.
