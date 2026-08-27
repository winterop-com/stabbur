# Architecture

A UI-agnostic core (catalog + library + runtime) with thin frontends (CLI, web
API/SPA) on top. Everything is **Pydantic** — settings via pydantic-settings,
and every data type is a `BaseModel` (no `@dataclass`).

## Modules

Large concerns are **packages** (a re-exporting `__init__` keeps `from stabbur import X` working while
the internals live in focused submodules); small cross-cutting modules stay top-level.

```
src/stabbur/
├── config.py / userconfig.py  # Settings (stabbur.toml + STABBUR_* env) + the durable machine config
├── models.py / host.py / locking.py / doctor.py / fsatomic.py  # value types, OS helpers, lock, health, atomic writes
├── cli/         # the Typer app, one module per command group (_app/_common + library/project/mcp/
│                #   voice/config/health/chat/serve) — was one 2400-line cli.py
├── library/     # scan the on-drive library → LibraryModel: _model, _roots, _scan, _manage
├── runtime/     # spawn + run models: runtime (commands), supervisor (reaper), serve_registry, sampling
├── voice/       # TTS/STT: kokoro, tts, the mlx-audio runtime, audio export, the voice registry
├── chat_tui/    # the Textual terminal chat: _util, _widgets, app (ChatApp)
├── project/     # the stabbur.toml manifest (one parser+writer) + scaffolding: __init__, scaffold, templates
├── agent.py / tools.py / mcpservers.py / mcp_catalog.py / plugins.py  # agent loop + MCP client/config
├── catalog.py / consumers.py / cards.py / tags.py / arch.py / capabilities.py / wantlist.py  # source + library support
├── attach.py / chatui.py / hfcache.py  # media attach, shared chat rendering, HF-cache redirect
├── app.py / server.py  # FastAPI factory (CORS/auth/SPA/lifespan) + ServerManager (one runtime child)
├── routers/     # FastAPI routers: health, catalog (browse/pull), and serving/ (a package: _base/core/
│                #   chat/voice/proxy behind one APIRouter)
└── sources/     # base + huggingface / ollama / lmstudio adapters
```

## Two views of "models"

- **Sources** (`catalog` + `sources/`) — what's in the local HF cache, Ollama,
  and LM Studio stores; the candidates for `sb library pull`.
- **Library** (`library`) — what's on the drive under `library_root`; the
  runnable set. `LibraryModel` carries `load_target` (the exact file/dir to hand
  the runtime) and `mmproj` (multimodal projector, if any).

A library is scanned per bucket — `voice/`, the format dirs (`gguf/` / `mlx/` / …), and Ollama's
native store each have their own layout — but a model's **identity** is a `ModelRef` (name +
format), not a bare name string: that's what `scan()` dedups on (the same model+format in two
libraries is one entry; a GGUF and an MLX build of the same repo are distinct and both survive).
Every bucket scanner funnels its per-item construction through one fault-isolation helper, so a
single corrupt or half-written model on disk is skipped rather than crashing the whole listing —
`scan()` returns the healthy models and never raises on a bad one.

## Serving

`serve` runs FastAPI. A `ServerManager` owns at most one runtime child process
(`llama-server` / `mlx_lm.server`) on an internal port — auto-picked free by
default, or pinned via `runtime_port` / `--runtime-port`. The
`serving` router exposes `/api/status`, `/api/load/{name}`, and a streaming
`/v1/{path}` proxy, so the browser SPA and the [Chrome side panel](guides/extension.md)
talk to one stable origin while the underlying model is swapped underneath.

```mermaid
flowchart LR
    client["SPA / side panel"] --> api["FastAPI (serve)"]
    api -->|"/api/load/{name}"| mgr["ServerManager.load()"]
    mgr -->|spawn| rt["runtime (llama-server / mlx_lm.server)"]
    api -->|"/v1/* stream-proxy"| rt
```

The **cross-site guard** (`app.py`) is part of this surface: `serve` is same-origin by
default, and a mutating `/api` / `/v1` call a browser flags cross-site (`Sec-Fetch-Site`) is
rejected `403` unless its origin is in `cors_origins` — that is what a Chrome extension origin
(`chrome-extension://<id>`) must be added to. Read-only traffic is unaffected.

## Agent loop, tools & the assistant surface

stabbur is the **MCP client** and owns the **agent loop**, so every frontend (CLI, web, side
panel) stays thin. `POST /api/chat` (`routers/serving/chat.py`) runs it server-side: the model
emits a `tool_call`, `agent.py` executes it via the `tools.MCPToolset` (spawned from the merged
`mcpServers` config, `mcpservers.py`), feeds the result back as a `tool` message, and continues —
streaming typed SSE (tokens, reasoning, tool-call chips) to the client. A tool result that returns
an **image** is fed to a vision model as a follow-up user image message (gated on the detected
`vision` capability); text-only models get a note instead.

With a **multi-target registry**, servers spawn lazily: `tools.MCPBridge` (built by `connect_bridge` in
the `serve` lifespan) starts only the eager set — shared/unowned servers plus the **primary** target's
own servers — and defers a non-primary scoped target's servers to its **first use** (first `/api/chat`
turn with that `target`, first `GET /api/assistants/{id}?verify=1`, or a bind/unbind). Free-play,
single-`[assistant]`, and `STABBUR_EAGER_MCP=1` stay full-eager. Spawns are single-flight per server under
one exit stack (teardown closes eager + lazy together), and a lazy spawn failure surfaces exactly like a
startup one (recorded in `toolset.errors`, tools absent). `GET /api/assistants` stays honest about a
not-yet-spawned target's `can_verify` (computed from the declared verify server, not the live tools), but
`GET /api/tools` lists **live** tools only — a lazily-pending target's tools appear there after first use.

A project can also carry a domain-generic **`[assistant]` block** — target metadata stabbur
**echoes but never interprets** (`routers/serving/assistant.py`, `project.AssistantInfo`):

- `GET /api/assistant` returns the block verbatim (name / base_url / auth / readonly / source),
  404 if absent. `?verify=1` runs the project-declared **verify** recipe (a named MCP tool call)
  once and caches the outcome for 60s, so a UI can show a live connection state without stabbur
  knowing what "connected" means for the domain.
- The **probe** recipe is echoed for the *client* to run same-origin in the target tab (e.g. the
  side panel's "Who am I here?").
- The **bind** recipe (`POST /api/assistant/bind` / `/unbind`) installs a credential the client
  minted — e.g. the extension's "Use my login" read-only PAT — by running a named mode's argv
  (secret handed via env, redacted from captured output, child killed by process group). Only the
  browser-side mint recipe and mode *names* are exposed; a mode's argv / `secret_env` stay
  server-side.

This keeps stabbur domain-neutral: the DHIS2 assistant is just a project whose `[assistant]` block
and MCP tools happen to describe a DHIS2 instance. See the
[Chrome side panel](guides/extension.md) guide for the client half.

## Runtimes

- **GGUF → llama.cpp** (`llama-server`, `llama-cli`) — cross-platform, web UI,
  tool calling (`--jinja`), and a native router mode for hot-swap.
- **MLX → mlx_lm** (`mlx_lm.server`, `mlx_lm.chat`, `mlx_lm.generate`) — Apple
  Silicon. Vision-capable MLX checkpoints route to **mlx-vlm** (`mlx_vlm.server`)
  instead, since text-only mlx_lm errors on the extra multimodal params.

Command names are pinned to current upstream (verified mid-2026). See the
[CLI reference](cli.md) for the command surface.

## Configuration & the project manifest

`stabbur.toml` is one file with **two readers, by design**:

- **Machine config** (`config.py`) — `library_root`, `host`/`port`, `cors_origins`,
  `auth_token`, and other per-machine settings. These are `pydantic-settings` fields, so any
  value can be overridden per machine with a `STABBUR_*` env var (precedence: CLI args > `STABBUR_*`
  env > `stabbur.toml` > `.env` > `~/.config/stabbur/config.toml`). `library_root` has **no default** —
  it is `None` when unset, and every consumer routes through `library.roots()` /
  `library.default_root()`, which raise `LibraryNotConfigured` rather than silently using `./data`.
- **The project manifest** (`project/`) — the *portable, committable* assistant definition:
  `[project]` (model + system prompt), `[voice]`, and `libraries` (which stores this project
  composes, in priority order). Tools are separate — the standard `mcpServers` JSON in `.mcp.json`
  (`mcpservers.py`), merged with the machine-global `~/.config/stabbur/mcp.json`. No machine-specific
  paths, so a project directory is git-committable and moves between machines.

Despite the two readers, the file has **one parser and one writer** (`project/`):

- `project.read_raw()` is the single TOML parse. `config.py`'s settings source routes through it
  too, so a malformed `stabbur.toml` fails one way — a clean `ProjectError` — instead of crashing
  differently in each reader. `project.load()` validates on top of it: a wrong-*typed* value
  (`libraries` that isn't an array of strings, a `[voice] enabled` that isn't a boolean) is a typo
  like any other, so it raises `ProjectError` rather than being coerced or silently dropped.
- `project.render_manifest()` renders a fresh manifest from values (`sb project init` / `new`) —
  the only thing that writes `stabbur.toml`. Tool servers are no longer part of it: `sb mcp add`
  writes `.mcp.json` through `mcpservers.py`, which owns that file's reads and writes.

### Import-time HF cache (the one deliberate side effect)

`stabbur/__init__.py` points the Hugging Face hub cache at `<library_root>/.cache/huggingface` **at
import time** (`hfcache.configure()`), so assets some runtimes fetch by repo id — e.g. mlx-audio's
Dia DAC codec — travel with the drive instead of landing in `~/.cache/huggingface`. This *must*
run before `huggingface_hub` is imported, because hf_hub freezes its cache path at its own import
— and importing almost any stabbur module transitively imports hf_hub. It is best-effort and guarded:
a no-op if the user set `HF_HOME`/`HF_HUB_CACHE`, or there is no mounted, configured library. This
is the only intentional import-time side effect; everything else is lazy.

### Serve → worker config handoff

`sb serve` passes its runtime config to the app through a small set of `STABBUR_*` env vars
(`_export_serve_env`). This env channel is deliberate: with `--reload`, uvicorn imports the app in
a *fresh subprocess* that has none of the CLI's in-process overrides, so env is the only thing
that crosses. Centralized in one documented function rather than scattered `os.environ` writes.

## Process lifecycle & concurrency

Model runtimes are **external processes** stabbur spawns, and both the CLI (`runtime.start`) and the
server (`ServerManager`) go through one **supervisor** (`runtime/supervisor.py`):

- Each runtime is spawned in its own session (`start_new_session`), so stopping it `killpg`s the
  whole group — the runtime *and* any workers it forked, not just the direct child.
- Each records a `meta.json` (its pid/pgid/command + the pid of the stabbur that owns it) under
  the XDG runtime/cache dir (`$XDG_RUNTIME_DIR/stabbur/runtimes`, else `~/.cache/stabbur/runtimes`) —
  ephemeral, machine-local state (a pid means nothing on another machine, so it deliberately
  does **not** live in a library). On a graceful exit an `atexit` hook stops live
  runtimes; for an ungraceful death (SIGKILL/OOM), `sweep_orphans()` runs at the next stabbur start
  and reclaims a runtime whose owning stabbur is gone (and whose live command still matches — a
  PID-reuse guard), so a crashed stabbur never leaves a model holding memory with no way to reclaim it.
- The auto-picked runtime port is retried on a bind collision, closing the find-a-free-port race.

Because the CLI and `sb serve` are expected to run **concurrently** against the same library,
mutations that read-modify-write shared files take a per-library advisory lock (`locking.py`, a
`flock` on `<root>/.stabbur/lock`): tag edits and destructive removes can't lose each other's changes
across processes. Long-running pulls are intentionally not held under this lock (they stage into
distinct model dirs with an atomic final move).
