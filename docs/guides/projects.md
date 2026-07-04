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
| `--force` | Overwrite an existing `kodo.toml`. |

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
