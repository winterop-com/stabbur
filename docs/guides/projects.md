# Projects

A **project** is a purpose-built assistant captured in a `kodo.toml` file: which
model to load, its system prompt, its MCP tools, and which libraries it reads. In a
project directory both `kodo chat` and `kodo serve --ui` bind to that definition —
so the app boots straight into the right model with its tools, no manual picking.

A project is **portable and committable**: it references models by name (never by
absolute path) and can carry its own model files, so the whole directory moves to
another machine and still runs.

## Scaffold one

Two entry points, same wizard:

```bash
kodo project init              # scaffold kodo.toml in the current directory
kodo project new my-assistant  # create a fresh directory and scaffold in it (like `cargo new`)
```

The wizard asks for a kind (chat/voice), a default model (a library model or a
curated starter to pull), MCP tools from installed plugins, a system prompt, and a
spoken-reply voice. Flags skip or change parts of it:

| Flag | Effect |
| --- | --- |
| `--model <name>` | Bind this model, skipping the model picker. |
| `--copy` (`--local`) | Copy the model into a **project-local `library/`** (a fast local-disk copy if it's already in your shared library) — makes the project self-contained. |
| `--git` | `git init` the project and write a `.gitignore` that excludes `library/` (the weights) and `.env`. |
| `--no-uv` | Skip the uv project (write only `kodo.toml`, no `pyproject.toml`). |
| `--force` | Overwrite an existing `kodo.toml`. |

## A project is a uv project

By default the scaffolder also writes a **`pyproject.toml`**, making the project a
self-contained [uv](https://docs.astral.sh/uv/) project. It pins `kodo` and the project's
pip-installable MCP servers, so the project carries its own environment:

```bash
cd my-assistant
uv sync                     # build the project's .venv (kodo + its MCP servers)
uv run kodo serve --ui      # runs this project's kodo, not a global one
uv run kodo chat
```

This is what makes a project *truly* portable: `uv run kodo` uses the pinned kodo and the
MCP servers installed into the project's `.venv`, instead of relying on a globally-installed
kodo and runtime `uvx` fetches. Because the servers are real dependencies, their `[[mcp]]`
commands drop the `uvx` runner. `.venv/` is gitignored; `uv.lock` is committed for
reproducibility. (`kodo` isn't on PyPI yet, so its pin is a local path source — replace it
with a version once kodo publishes. Pass `--no-uv` for the plain `kodo.toml`-only shape.)

For a complete worked example — a DHIS2 assistant with its model copied in, a project-local
DHIS2 profile, and example prompts — see the
[DHIS2 assistant worked example](dhis2-project.md).

## Templates

`--template <name>` presets the whole wizard so a purpose-built assistant is reproducible in
one command — a model, a system prompt, tools, and example files:

```bash
kodo project new mydhis2 --template dhis2 --copy --git
```

| Template | Model | Tools |
| --- | --- | --- |
| `dhis2` | `Ornith-1.0-9B` (the [benchmark](model-catalog.md) winner) | the DHIS2 bridge (read-only) + a profile template |
| `coder` | `Qwen3-Coder-30B` | `git` + `filesystem` |
| `research` | `gemma-4-12B` | `search` + `fetch` |

In a uv project the template's pip-installable tools are pinned in `pyproject.toml`
automatically; bundled kodo servers and node (`bunx`) servers are left as-is. Override the
model with `--model`, and add more tools later with `kodo mcp add` (also uv-aware).

```bash
kodo project new assistant --model unsloth/Qwen3.5-4B-GGUF --copy --git
```

## What it writes

```toml
# kodo.toml
libraries = ["library", "@shared"]   # only when --copy is used; else just @shared

[project]
model = "unsloth/Qwen3.5-4B-GGUF"
system_prompt = "You are a concise, helpful assistant."
chat_voice = "kokoro:af_heart"       # spoken-reply voice (Kokoro)

# [[mcp]] blocks add tools (see below)
```

- **`libraries`** — the libraries this project reads, in priority order (first match
  wins). With `--copy` it lists the project-local `library/` first, then `@shared`
  (the machine default, `KODO_LIBRARY_ROOT`). See [The library](library.md).
- **`[project]`** — the bound model, system prompt, and spoken-reply voice.
- **`[[mcp]]`** — one block per MCP tool server (repeatable).

## A locked assistant

Binding `[project].model` makes the project a **locked assistant**: `kodo serve --ui`
in that directory hides the model picker and serves only that model (the UI shows a
locked state); `kodo chat` defaults to it. Outside a project, kodo is free-play (pick
any model). Pass `--model` to override the binding.

## Tools

Add tools with `[[mcp]]` blocks — installed `kodo-mcp-*` plugins or any external MCP
server command. Browse a curated catalog and append one with `kodo mcp`:

```bash
kodo mcp list        # curated servers + installed plugins (✓ = already in kodo.toml)
kodo mcp add fetch   # append its [[mcp]] block to ./kodo.toml
```

…or write the block by hand:

```toml
[[mcp]]
name = "datetime"
command = "kodo-mcp-datetime"
```

`kodo.toml`'s `command` is split like a shell line, so arguments work inline; there
is **no `env` field**, so pass environment variables with an `env` prefix when a
server needs them. See [Tools (MCP)](tools.md) for the full picture, and the
[DHIS2 assistant worked example](dhis2-project.md) for an end-to-end build (scaffold,
local model copy, external MCP server with a profile, and confirming the model calls
the tool).

## Inspect

`kodo project show` prints the resolved project: the bound model's detail card, the
system prompt, and the **actual tools** (it connects to the MCP servers and lists
what they expose, not just the server names).

```bash
kodo project show
kodo project show --card    # also render the model card (README)
```
