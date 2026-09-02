# Projects

A **project** is a purpose-built assistant captured in a `stabbur.toml` file: which
model to load, its system prompt, its MCP tools, and which libraries it reads. In a
project directory both `sb chat` and `sb serve --ui` bind to that definition —
so the app boots straight into the right model with its tools, no manual picking.

A project is **portable and committable**: it references models by name (never by
absolute path) and can carry its own model files, so the whole directory moves to
another machine and still runs.

## Which project applies

stabbur finds the manifest by **walking up** from the current directory, like `git`
finding `.git` — so a command in `my-assistant/src/` uses `my-assistant`'s project, not
free-play. The first `stabbur.toml` found wins (a nested project shadows an enclosing one
from there down), and the walk stops at your home directory, at a filesystem mount
boundary, and never looks in `/`.

Everything the manifest names is relative to **its own directory**, not to where you
ran the command: its `libraries` entries and its `.mcp.json`. So a subdirectory reads the
same libraries and gets the same tools as the project root. `sb project show` prints the
full path of the manifest it found whenever that isn't the current directory.

## Scaffold one

Two entry points, same wizard:

```bash
sb init my-assistant         # create a new directory with its own model, config and tools
```

An interactive wizard (a Textual TUI) asks for a kind (chat/voice), a model, MCP tools from
installed plugins (space to toggle), and a system prompt. Everything is then **downloaded into the
project's own `library/`** — always a fresh download, never a copy out of your machine library, so
what you move is what runs.

What lands there is a working package, not just weights: the chat model, the in-chat voice
(Kokoro) and the good one (VoxCPM2) — a few GB more, and the project can actually speak. A
**Voice** project adds speech-to-text so the mic half works too. `--no-voices` skips the lot.

Roughly 10 GB for the recommended model (6.7 GB) plus the voices (3.4 GB); about 6 GB with a
small model, and 1.6 GB more for a Voice project's speech-to-text. The scaffolded `pyproject.toml`
pins the `voice` extra alongside stabbur, so `uv sync` builds an environment that can actually run
what was downloaded.

| Flag | Effect |
| --- | --- |
| `--model <name>` | Bind this model, skipping the wizard's model step. Required when there is no terminal to run the wizard in (a pipe, a script, CI). |
| `--no-voices` | Skip the voice package (Kokoro + VoxCPM2), for a text-only assistant. |
| `--git` | `git init` the project and write a `.gitignore` that excludes `library/` (the weights) and `.env`. |
| `--no-uv` | Skip the uv project (write only `stabbur.toml`, no `pyproject.toml`). |
| `--template <name>` | Preset the whole wizard from a template (e.g. `dhis2`). |
| `--force` | Scaffold into a directory that already exists (refused otherwise). |

A project also overrides the machine's `chat_server`: inside it, `sb chat` runs the project's own
model instead of attaching to a configured remote. Otherwise a freshly scaffolded project would
talk to someone else's build of the model it had just downloaded. `--server` still attaches when
you ask for it.

## Changing it later

`sb configure` (inside the project) reopens the same choices as a form: model, system prompt,
tools, voice, and what the project's library holds. It downloads what you add and removes what you
deselect, rewriting `stabbur.toml` and `.mcp.json`. Nothing happens until you save.

## A project is a uv project

By default the scaffolder also writes a **`pyproject.toml`**, making the project a
self-contained [uv](https://docs.astral.sh/uv/) project. It pins `stabbur` and the project's
pip-installable MCP servers, so the project carries its own environment:

```bash
cd my-assistant
uv sync                     # build the project's .venv (stabbur + its MCP servers)
uv run stabbur serve --ui      # runs this project's stabbur, not a global one
uv run stabbur chat
```

This is what makes a project *truly* portable: `uv run stabbur` uses the pinned stabbur and the
MCP servers installed into the project's `.venv`, instead of relying on a globally-installed
stabbur and runtime `uvx` fetches. Because the servers are real dependencies, their `.mcp.json`
commands drop the `uvx` runner. `.venv/` is gitignored; `uv.lock` is committed for
reproducibility. (`stabbur` isn't on PyPI yet, so its pin is a local path source — replace it
with a version once stabbur publishes. Pass `--no-uv` for the plain `stabbur.toml`-only shape.)

For a complete worked example — a DHIS2 assistant with its model copied in, a project-local
DHIS2 profile, and example prompts — see the
[DHIS2 assistant worked example](dhis2-project.md).

## Templates

`--template <name>` presets the whole wizard so a purpose-built assistant is reproducible in
one command — a model, a system prompt, tools, and example files:

```bash
sb init mydhis2 --template dhis2 --git
```

| Template | Model | Tools |
| --- | --- | --- |
| `dhis2` | `Ornith-1.0-9B` (the [benchmark](model-catalog.md) winner) | the DHIS2 bridge (read-only) + a profile template |
| `coder` | `Qwen3-Coder-30B` | `git` + `filesystem` |
| `research` | `gemma-4-12B` | `search` + `fetch` |

In a uv project the template's pip-installable tools are pinned in `pyproject.toml`
automatically; bundled sb servers and node (`bunx`) servers are left as-is. Override the
model with `--model`, and add more tools later with `sb mcp add` (also uv-aware).

```bash
sb init assistant --model unsloth/Qwen3.5-4B-GGUF --git
```

## What it writes

```toml
# stabbur.toml
libraries = ["library"]              # its own store, and only its own

[project]
model = "unsloth/Qwen3.5-4B-GGUF"
system_prompt = "You are a concise, helpful assistant."
chat_voice = "kokoro:af_heart"       # spoken-reply voice (Kokoro)
```

Tools are **not** in `stabbur.toml` — they live in a sibling `.mcp.json` (see below).

- **`libraries`** — the libraries this project reads, in priority order (first match
  wins). A scaffolded project lists its own `library/` and nothing else, so it ignores this
  machine's library and default model entirely — that is what makes the directory portable. Add
  `"@shared"` (the machine default, `STABBUR_LIBRARY_ROOT`) by hand to opt back in. See
  [The library](library.md).
- **`[project]`** — the bound model, system prompt, and spoken-reply voice.
- **`.mcp.json`** — the project's MCP tool servers (standard `mcpServers` JSON).

## A locked assistant

Binding `[project].model` makes the project a **locked assistant**: `sb serve --ui`
in that directory hides the model picker and serves only that model (the UI shows a
locked state); `sb chat` defaults to it. Outside a project, stabbur is free-play (pick
any model). Pass `--model` to override the binding.

## Tools

Tools are the standard `mcpServers` JSON in `./.mcp.json` (plus the machine-global
`~/.config/stabbur/mcp.json`, which merges in). Browse a curated catalog and add one with
`sb mcp`:

```bash
sb mcp list        # curated servers + installed plugins (✓ = already in .mcp.json)
sb mcp add fetch   # add to ./.mcp.json  (--global for ~/.config/stabbur/mcp.json)
```

…or write the JSON by hand:

```json
{
  "mcpServers": {
    "datetime": { "command": "stabbur-mcp-datetime" }
  }
}
```

`stabbur.toml`'s `command` is split like a shell line, so arguments work inline; there
is **no `env` field**, so pass environment variables with an `env` prefix when a
server needs them. See [Tools (MCP)](tools.md) for the full picture, and the
[DHIS2 assistant worked example](dhis2-project.md) for an end-to-end build (scaffold,
local model copy, external MCP server with a profile, and confirming the model calls
the tool).

## Inspect

`sb project show` prints the resolved project: the bound model's detail card, the
system prompt, and the **actual tools** (it connects to the MCP servers and lists
what they expose, not just the server names).

```bash
sb project show
sb project show --card    # also render the model card (README)
```
