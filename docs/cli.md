# CLI reference

The CLI is the **`stabbur`** command. Everything centers on your **library** — the
models in your libraries (a project's, or the default `STABBUR_LIBRARY_ROOT`). Run any command
with `--help` for full options.

**Global flag:** `stabbur --debug <command>` turns on verbose diagnostics — it prints
the exact model-runtime command and streams the runtime's logs live (instead of
discarding them), which is the first thing to reach for when `sb chat`
reports a model that "exited before becoming ready". (Also settable with
`STABBUR_DEBUG=1`.)

## `sb init <path>`

Create a **self-contained project assistant** in a new directory. Everything it needs lives
inside: the model (downloaded into `<path>/library/`), the system prompt, the tools
(`.mcp.json`), and its own uv environment. The generated `stabbur.toml` lists that library and
**only** that library, so the project ignores this machine's library and default model — zip the
directory, move it to another machine, and it still runs.

An interactive wizard (a Textual TUI) walks the choices: kind, model, tools (space to toggle),
system prompt. With no terminal — a pipe, a script, CI — pass `--model` instead and it scaffolds
without the TUI.

It downloads a working package, not just weights: the chat model, the in-chat voice (Kokoro) and
the good one (VoxCPM2) — a few GB more, and the project can speak out of the box. A **Voice**
project also gets speech-to-text, so the mic works. `--no-voices` skips them.

```bash
sb init mybot                                  # the wizard, then a fresh download into mybot/
sb init mybot --model unsloth/Qwen3.5-4B-GGUF  # skip the wizard's model step
sb init mybot --git                            # also: git init + a .gitignore (excludes library/ + .env)
sb init mybot --no-voices                      # text only: skip Kokoro + VoxCPM2
sb init mybot --no-uv                          # no pyproject.toml (use a global stabbur instead)
sb init mybot --template dhis2                 # a preset assistant (model + prompt + bridge + files)
```

It **refuses an existing directory** (`--force` overrides): creating a project means making a new
nest, and scaffolding into a directory that already holds one is how two assistants end up
arguing over one `stabbur.toml`.

The model is always downloaded fresh into the project, never copied out of your machine library —
a project owns its weights outright, so what you move is what runs.

A project is a **reproducible assistant**: in a project directory — or any subdirectory
of it — both `sb chat` and `sb serve --ui` default to its model, system prompt, and MCP
tool servers, so `sb serve --ui` boots straight into that model, no manual picking.

**Which project applies:** stabbur walks up from the current directory and uses the first
`stabbur.toml` it finds (like `git` and `.git`), stopping at your home directory, at a
filesystem mount boundary, and never looking in `/`. Everything the manifest names —
its `libraries` entries and its `.mcp.json` — is relative to the manifest's own directory,
so a subdirectory gets the same libraries and tools the project root does. `sb project
init` still scaffolds in the current directory, warning if that nests inside an existing
project.

## `sb project show`

Show the active project (`stabbur.toml`) in full: the bound model's detail card
(format, size, capabilities, context, tags, path), the system prompt, and the
**actual tools** — it connects to the project's MCP servers and lists the tools
they expose (with descriptions), not just the server names. `--card` also renders
the bound model's model card (README). Run from a subdirectory it prints the full
path of the manifest it found, so you can see which project you are in.

```bash
sb project show
sb project show --card    # also print the model card (README)
```

## `sb mcp list` / `sb mcp add`

Browse MCP tool servers and attach them via the standard `mcpServers` JSON. `list`
shows a **curated catalog** (DHIS2, `fetch`, `git`, `sqlite`, `filesystem`, …) plus any
installed `stabbur-mcp-*` plugins; a `✓` marks servers already switched on here. `add`
writes a server entry to the project's `.mcp.json` — the one beside the `stabbur.toml`
found by walking up, or `./.mcp.json` outside a project — or the machine-global
`~/.config/stabbur/mcp.json` with `--global`, printing a `setup:` hint when the command
needs config; `remove` drops one again.

```bash
sb mcp list             # curated catalog + installed plugins (ls is an alias)
sb mcp add fetch        # add to this project's .mcp.json
sb mcp add --global datetime   # add to ~/.config/stabbur/mcp.json (every chat gets it)
sb mcp add dhis2        # then edit the DHIS2_PROFILE in the entry's env
```

See [Tools (MCP)](guides/tools.md) for the full picture.

## `sb library search <query>`

Search the Hugging Face Hub for new models to pull (most-downloaded first).

```bash
sb library search qwen3            # text search
sb library search qwen3 --gguf     # only GGUF (llama.cpp-ready) repos
sb library search qwen3 -n 30      # more results
```

## `sb library ls`

List the models in **your library** — what you've pulled, ready to run — grouped
by format with sizes across the libraries in scope.

```bash
sb library ls
sb library ls -d     # detailed cards (caps, context, location, path, tags)
```

## `sb library formats`

One row per model with a column per format present (gguf / mlx / safetensors /
ollama) and their sizes, plus a NOTE flagging the format-policy cases: a
**redundant** safetensors copy (a GGUF or MLX build of the same model already
exists — safetensors is just the convert/fine-tune source and can be dropped) and
a model that's **only** safetensors (no ready-to-run quant — llama.cpp/mlx_lm
can't serve it; pull a GGUF or MLX build). The footer totals the space reclaimable
by removing every redundant safetensors copy.

```bash
sb library formats
sb library rm <name> --format safetensors   # act on a flagged copy
```

## `sb library rm <name>`

Remove a model from the library — **deletes its files from disk**. Resolves like
`sb chat` (use `--format` to disambiguate a model kept in more than one format);
all copies of the model are removed (e.g. one on the local disk and one on the
drive). Ollama models keep any blobs still shared with other installed models.
Prompts for confirmation unless `--yes`.

```bash
sb library rm Voxtral-Mini-3B-2507-GGUF          # confirm, then delete
sb library rm gemma-4-E4B-it-MLX-4bit --yes      # skip the prompt
sb library rm Qwen3.6-27B --format mlx           # disambiguate when kept in two formats
```

## `sb library sources`

Browse models sitting in your **app caches** (Hugging Face cache, Ollama, LM
Studio) that you could pull into the library. The IN LIBRARY column marks what
you already have: `✓` is the same copy (matched on name, format **and** size),
`~ other quant` / `~ other format` means the library has that model in a shape
this isn't — a tick there would claim you had a quant you don't. Non-chat
(embedding/vision) and partial entries are hidden unless `--all`.

```bash
sb library sources
sb library sources -s ollama       # --source: limit to one source
sb library sources --all           # include embedding/vision/partial entries
```

## `sb library pull <source> <name>`

Copy a model from a source cache into the library.

```bash
sb library pull lmstudio <name>
sb library pull ollama gemma4:31b --move    # delete the local source after a verified copy
sb library pull ollama --all                # import every model from the local store
sb library pull lmstudio --all --move       # import all, freeing local disk as it goes
# Hugging Face:
sb library pull huggingface lmstudio-community/gemma-4-12B-it-QAT-GGUF --include '*Q4_K_M*'
sb library pull huggingface OuteAI/OuteTTS-0.2-500M-GGUF --include '*Q4_K_M*' \
         --vocoder ggml-org/WavTokenizer          # a TTS model + its vocoder
```

- `--move` — relocate (copy, verify byte-for-byte, then delete the local source
  to free disk). Supported for LM Studio and Ollama.
- `--all` — import every model from that source's local store instead of a single
  name (the two are mutually exclusive). Idempotent (skips models already in the
  library, so it doubles as a sync) and resilient (a failing model is logged and
  the batch continues; exit code is non-zero if any failed).
- `--include <glob>` — Hugging Face only; fetch only matching files (repeatable),
  e.g. one GGUF quant from a multi-quant repo. Model cards and configs come along.
- `--vocoder <repo>` — Hugging Face only; co-locate a vocoder (e.g. WavTokenizer)
  with the model so it's recognized as a **text-to-speech** model (see `sb voice speak`).

## `sb library manifest`

Export your library as a **want list** — a portable, human-editable TOML file of `[[model]]`
entries (source + name + format), one per model, enough to re-pull each. Reads each model's
recorded source from its `.stabbur/` sidecar (inferring it for older pulls). A model pulled with
`--include` also carries those globs, so a rebuild fetches the one quant you keep rather than
every quant in a multi-quant repo; an entry with no `include` re-pulls the way a plain pull does
(one quant, chosen from what the repo ships).
Prints to stdout by
default; `--save <file>` writes it. No state is kept in the library — the manifest is generated
on demand, so you keep the file wherever you like (commit it to a repo, copy it to another drive).

```bash
sb library manifest                     # print the want list (pipeable)
sb library manifest --save models.toml  # write it to a file
```

LM Studio backups (which can't be re-downloaded as such) are recorded as their Hugging Face
equivalent; Ollama models are recorded as-is; voice models as their registry id.

## `sb library sets`

The **curated sets** — validated groups of models you can pull in one command, so filling a
fresh library isn't a page of copy-paste pulls. Each set pins the exact quant it was validated
with, rather than leaving the choice to the default.

```bash
sb library sets                  # name, model count, rough size, what it's for
sb library sync starter          # a first working library: transcription + one small chat model
sb library sync voice            # every runnable voice model
sb library sync chat             # the main model: one capable all-rounder
```

## `sb library sync <wantfile|set>`

Re-download every model in a want list — or a curated set — that's **missing** from your library.
Diffs it against your library (models already present are skipped) and pulls the rest via the
normal per-source paths — the rebuild-a-drive companion to `sb library manifest`. A file on disk
always wins over a set of the same name.

```bash
sb library sync starter                 # a curated set (see `sb library sets`)
sb library sync models.toml             # pull everything missing
sb library sync models.toml --dry-run   # show the plan, download nothing
sb library sync models.toml --shared    # into the shared/default library
sb library sync models.toml --repair    # also re-pull models that fail verification
```

`--repair` runs `sb library verify` over each model the want list already covers and treats a
failure as absent, so the pull rewrites it — for a drive that came back with a half-finished or
corrupted copy. Add `--deep` to extend verification to re-hashing Ollama blobs (slow, but true
content integrity). Re-pulling genuinely repairs rather than skipping: the Hugging Face snapshot
re-fetches any file whose size or etag no longer matches.

One model failing doesn't stop the others; the command exits non-zero if any failed. Ollama
entries need the model in your **local Ollama store** first (`ollama pull <name>`), since the
Ollama pull path copies from there rather than the internet.

## `sb chat <name>`

Chat with a library model — a full-screen Textual TUI by default, one-shot with `-p`.

```bash
sb chat <name>                      # interactive full-screen TUI
sb chat <name> -p "prompt"          # one-shot, prints just the answer (pipeable)
sb chat <name> -p "prompt" -n 256   # --max-tokens
sb chat <name> --system "..."       # session system prompt (overrides stabbur.toml)
sb chat <name> --mcp <cmd>          # attach an MCP tool server (repeatable)
sb chat <name> -p "prompt" --server http://127.0.0.1:2222   # reuse a running `sb serve`
sb chat <name> -p "prompt" --no-server                      # force a local load, ignore config
```

Interactive chat opens a scrolling TUI: markdown replies, collapsible reasoning,
live tool activity, and a context footer. Enter sends; Shift+Return / Ctrl-J / a
trailing backslash insert a newline; type a new message while one streams to
**queue** it; Esc stops. `-p` stays a plain scripted one-shot (streamed stdout).

**Reuse a loaded model (`--server`)** — by default each `sb chat` spawns its own
runtime and loads the model, so a one-shot pays that load every time. Point `-p` at a
running `sb serve` instead and it attaches to that server's `/v1`, reusing the
already-loaded model (tools still run):

```bash
sb serve --model <name> --port 2222        # load the model once, keep it resident
sb chat -p "what is todays date"           # instant — no reload
```

A loopback `sb serve --model <name>` is **auto-detected**: with no `--server` (and none in
config), `sb chat -p` finds a running server locked to that model and attaches to it (a `↳
attaching…` note goes to stderr). This auto-detection is one-shot (`-p`) only — the interactive
TUI never attaches implicitly, since that would silently disable `/model`.

Set an explicit default with `sb config set server <url>` (or `STABBUR_CHAT_SERVER`); `--server`
overrides it, and an explicit default *does* apply to the interactive TUI as well. Because that
default then applies to every run, **`--no-server` is the per-run way back to a local load**: it
ignores the configured server and skips the auto-attach above, so the model really is loaded
here. `--server` and `--no-server` are mutually exclusive.

```bash
sb config set server http://gpu-box:8080   # every chat now attaches to the remote
sb chat <name> -p "prompt" --no-server     # ...except this one, which loads locally
```

**Multimodal input** — for vision/audio models, attach files:

```bash
sb chat <name> -p "what is this?" --image photo.jpg    # vision model
sb chat <name> -p "transcribe" --audio clip.wav         # audio model
```

`--image`/`-i` and `--audio`/`-a` are repeatable. In the REPL you can also just
**drag a file into the terminal** — the inserted path is detected and attached:
image/audio go as OpenAI multimodal content (stabbur warns if the model lacks that
modality), while **text/code files** (`.md`, `.py`, `.json`, …) are inlined into
the prompt as fenced blocks, so you can drop a file into *any* model as context.

Non-chat models (embeddings, vision encoders) are refused with a clear message —
stabbur runs generative LLMs only.

## `sb voice voices`

List the built-in **Kokoro** voices (54 across 9 languages) with their id,
language, and gender. Kokoro ships built in (no extra to install).

```bash
sb voice voices                            # id · name · language · gender
```

## `sb voice speak <text...>`

Text-to-speech. The default engine is **Kokoro (ONNX)** — cross-platform, built in,
its model fetched on first use; `--voice`/`-v` picks one of its named voices (run
`sb voice voices` to list them). `--model` uses a registry voice model through the
mlx-audio runtime instead (`sb voice list`), where `--ref-audio` + `--ref-text` clone
a voice and `--seed` pins a seeded model's otherwise-random one. `--speed` takes
0.5-2.0. Markdown/code in the text is reduced to prose first.

```bash
sb voice speak hello there                 # default Kokoro voice, play aloud
sb voice speak -v af_heart "hello there"   # a specific Kokoro voice
sb voice speak "some text" -o out.wav      # write a WAV instead of playing
sb voice speak hi --model <voice-id> --seed 10    # a registry voice model, seed pinned
```

## `sb setup`

First-run **machine setup** — the write-mode companion to `sb doctor` (machine scope,
whereas `sb init` scaffolds one project). It persists per-machine defaults to
`~/.config/stabbur/config.toml` (library location + default model), **downloads the in-chat voice
and a small starting model** so a fresh install has something to talk to, builds the browser UI if
[Bun](https://bun.sh) is present, and prints an OS-specific hint for anything it can't install
(the llama.cpp binary). Safe to re-run — anything already present is skipped, not re-fetched.

```bash
sb setup                              # interactive first-run setup
sb setup --no-download                # ...without fetching the voice or a model
sb setup --library-root /path --model <name> --yes   # non-interactive
```

The download is the default rather than a question: stabbur speaks and chats out of the box, and
the alternative is a surprise 310 MB fetch part-way into the first conversation. `--no-download`
turns it off; `sb library sets` pulls more when you want it.

## `sb configure`

Change a project after it exists — the settings `sb init` asked for once, editable now that you
know what you are building. A Textual screen with four tabs:

- **Assistant** — the bound model (its own library's chat models, plus curated ones it can
  download) and the system prompt.
- **Tools** — the MCP servers in `.mcp.json`, pre-checked, space to toggle.
- **Voice** — the reply voice, whether the Voice surface is shown at all, and which voice models
  the project holds (adding one downloads it; unchecking leaves it in place — remove it in
  **Library**).
- **Library** — what is on disk in the project's library, with sizes, selectable for removal.

```bash
sb configure          # inside a project
```

Nothing is written or downloaded until you save (Ctrl-S); escape leaves the project untouched.
Saving rewrites `stabbur.toml` and `.mcp.json` first and does the disk-heavy work after, so an
interrupted download never costs you the settings. Project-scoped: run it inside a project — the
two machine-wide defaults are `sb config` below.

## `sb config`

Read and write the **machine defaults** (`~/.config/stabbur/config.toml`) — the lowest-priority
settings source, below `STABBUR_*` env vars and a project `stabbur.toml`. Writable keys:
`library-root`, `model` (the default model outside a project), and `server` (a default
`sb serve` URL for `sb chat` to attach to — it applies to every chat until you override it
with `--server`, or opt out of it for one run with `--no-server`).

```bash
sb config set library-root /path/to/your/library   # where your library lives
sb config set model lmstudio-community/gemma-4-12B-it-QAT-GGUF   # default model
sb config list                        # show every stored value (ls is an alias)
sb config get library-root            # one value
sb config path                        # print the config file location
```

## `sb doctor`

Pre-flight system health: are the runtime binaries stabbur spawns installed
(`llama-server`, and `mlx_lm.server`/`mlx_vlm.server` on Apple Silicon), is the
library reachable and non-empty, and does the project point at a present model.
Exits non-zero if any check fails.

Two rows lead the report and the rest is filed under them. **Backend** says where the
models actually run: normally `Local runtime`; with an upstream configured
(`STABBUR_UPSTREAM`, or `sb serve --upstream`) it probes the remote `/v1` and reports
what it serves — or fails with the reason it could not be reached (unresolvable name,
refused connection, no answer, not an OpenAI `/v1`). **Model** names the model in play:
the one a running `sb serve` has loaded, or — on the CLI, where there is no runtime to
ask — the default that would load. Everything else (runtimes, library, project, tools)
is indented under a group row, and `/api/doctor` sends the same tree to the web UI.

With an upstream, the **local runtime rows go quiet**: models run on the remote, so a missing
`llama-server` or `mlx_vlm.server` here is a fact about a machine that isn't running anything,
and the library count stops partitioning by a local binary. What matters in that mode is the
Backend row, which is exactly what the report leads with.

```bash
sb doctor
STABBUR_UPSTREAM=http://gpu-box:8080 sb doctor  # also check the remote backend
```

## `sb serve`

Run the web server (browse API + `/v1` proxy; browser UI with `--ui`). The port is
**fixed** (2222 by default) so the URL is stable across restarts — if it's already
taken, `serve` says so and stops rather than quietly moving to another one. Pass
`--port`, or set a new default with `sb config set port`.

```bash
sb serve --ui                       # browse + chat, switch models (port 2222 by default)
sb serve --ui --port 8080           # a different port
sb serve --ui --model <name>        # locked single-model mode (extension backend)
sb serve --reload                   # dev auto-reload
```

Equivalent Makefile targets: `make run` and `make run MODEL=<name>`.

## `sb ext-dev`

Test-drive the browser extension interactively: builds the extension fresh, launches a
**headed Chromium** with it loaded, and starts the live-tier `sb serve` (a locked model +
DHIS2 bridge pointed at the play demo, read-only) so the side panel can be driven end-to-end.
It seeds the panel settings and opens a page for prompt-catalog testing, then leaves everything
running. **Ctrl+C** tears down the browser and `sb serve` together.

This is a **repo-only dev tool** — it needs the extension source, so it must run from a stabbur
source checkout (it walks up from the current directory to find `extension/`) and requires
[bun](https://bun.sh) with the extension deps installed (`bun install` in `extension/`).

```bash
sb ext-dev                          # single play42 target (generic build)
sb ext-dev --multi                  # two targets (play42 + play41) for tab-driven switching
sb ext-dev --flavor dhis2           # build and load the DHIS2-branded flavor
sb ext-dev --no-build               # skip the build, load the existing output dir
```

The cold model load can take minutes on first run; the console prints the panel URL and backend
once the server is ready.
