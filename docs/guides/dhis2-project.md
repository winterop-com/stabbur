# Building a DHIS2 assistant (worked example)

A complete, reproducible walkthrough: scaffold a **portable DHIS2 project** whose
model lives next to it, attach the **DHIS2 CLI bridge** as an MCP tool, point it at
a named DHIS2 profile, and confirm the local model actually calls the tool.

The result is a self-contained project directory you can copy to another machine —
the model weights, the assistant definition, and the tool wiring all travel together
(the only per-machine bit is the DHIS2 profile, see below).

See [Tools (MCP)](tools.md) for choosing between the three DHIS2 MCP sizes; this
guide uses the **bridge** (one `dhis2_cli` tool) because it pairs well with a
smaller local model.

!!! tip "One command (the reproducible shortcut)"
    Everything below is scaffolded by a single template:

    ```bash
    sb init mydhis2 --template dhis2 --git
    ```

    That presets the model (`Ornith-1.0-9B`, the [benchmark](model-catalog.md) winner), a
    DHIS2 system prompt, the read-only bridge, and example files — then prints the profile
    setup. The rest of this guide walks through what that command sets up, so you can build
    or adapt it by hand.

## Prerequisites

- **`STABBUR_LIBRARY_ROOT`** set to your library (so the model can be copied from it).
- **`llama-server`** on `PATH` (`brew install llama.cpp`) — the GGUF runtime.
- **`uv` / `uvx`** — used to run the DHIS2 bridge (`uvx dhis2w-mcp-bridge`).
- A **DHIS2 profile** in `~/.config/dhis2/profiles.toml`. The bridge selects the
  target server by profile name; credentials/tokens stay on the machine, never in
  the committed project. Example:

    ```toml
    [profiles.play42]
    base_url = "https://play.im.dhis2.org/dev-2-42"
    ```

    Confirm the profile reaches the server before wiring stabbur:

    ```bash
    env DHIS2_PROFILE=play42 uvx dhis2w-mcp-bridge   # starts the MCP server (Ctrl-C to stop)
    ```

## 1. Scaffold the project with a local model copy

`sb init <dir>` runs an interactive wizard. `--model` skips the model
picker; the chosen model is always downloaded **into the project's own
`library/`** instead of relying on the shared library — that is what makes the
project portable.

```bash
sb init dhis2 \
  --model lmstudio-community/gemma-4-12B-it-QAT-GGUF \
  --git
```

`--git` initializes a repo and writes a `.gitignore` that excludes the local
`library/` (the weights) and `.env` — so the project is committable out of the box.
Drop it if you don't want version control.

The wizard asks three things:

1. **Kind** — `1` (Chat).
2. **Tools** — leave blank. The DHIS2 bridge is *not* a stabbur plugin, so it does not
   appear in the plugin picker; we add it by hand in the next step.
3. **System prompt** — e.g. *"You are a DHIS2 assistant. Use the dhis2 tools to
   query the connected DHIS2 instance instead of guessing…"*.

stabbur copies the model (a fast local-disk copy if it is already in your shared
library) and writes `dhis2/stabbur.toml`:

```toml
# This project ships its own "library/" store (the model was copied there);
# @shared is the machine default library (STABBUR_LIBRARY_ROOT) if you set one.
libraries = ["library", "@shared"]

[project]
model = "lmstudio-community/gemma-4-12B-it-QAT-GGUF"
system_prompt = "You are a DHIS2 assistant. …"
chat_voice = "kokoro:af_heart"
```

The weights land under `dhis2/library/gguf/lmstudio-community/gemma-4-12B-it-QAT-GGUF/`.

!!! note "Don't commit the weights"
    The project-local `library/` holds multi-GB model files. `--git` (above) already
    gitignores it; if you version the project some other way, exclude `library/`
    (or at least the weight files) yourself.

## 2. Attach the DHIS2 bridge

Add a server entry to `dhis2/.mcp.json` (standard `mcpServers` JSON, so it has a
first-class `env` object). `DHIS2_MCP_READONLY=1` keeps it query-only:

```json
{
  "mcpServers": {
    "dhis2": {
      "command": "uvx",
      "args": ["dhis2w-mcp-bridge"],
      "env": { "DHIS2_PROFILE": "play42", "DHIS2_MCP_READONLY": "1" }
    }
  }
}
```

The DHIS2 CLI bridge is a single `dhis2_cli` tool that runs d2 CLI calls against the
connected instance; `DHIS2_PROFILE` selects the target (from `profiles.toml`). To
retarget later, swap `play42` for another profile name (e.g. `play43`) — no other change.

## 3. Verify the tool wiring

`sb project show` (run inside `dhis2/`) prints the resolved model and **spawns the
MCP servers to list their real tools** — so it confirms the bridge connects before
you ever load the model:

```bash
cd dhis2
sb project show
```

```
Tools
  connecting to 1 MCP server(s) …
  dhis2 (env DHIS2_PROFILE=play42 DHIS2_MCP_READONLY=1 uvx dhis2w-mcp-bridge) — 1 tool(s)
    dhis2_cli  Run a `d2w` CLI command against a DHIS2 server.
```

You can also list installed plugin servers (the bridge is a project MCP, not a
plugin, so it won't appear here) with either spelling:

```bash
sb mcp list      # or the alias:
sb mcp ls
```

## 4. Try it — the model calls the tool

Run a one-shot chat (`-p`) that forces a lookup. stabbur loads gemma via `llama-server`,
connects the bridge, and runs the agent loop: the model emits a `dhis2_cli` tool
call, stabbur executes it against `play42`, and feeds the result back:

```bash
sb chat -p "What DHIS2 version is the server running, and what is the system name? Use your dhis2 tool to check, don't guess."
```

```
  ⚙ dhis2__dhis2_cli({"args":["system","info"]})
  ↳ Root(exit_code=0, stdout='{"version":"2.42.6-SNAPSHOT", …, "systemName":"DHIS 2 Demo - Sierra Leone", …}')

The DHIS2 server is running version 2.42.6-SNAPSHOT, and the system name is
DHIS 2 Demo - Sierra Leone.
```

For the interactive TUI (or the browser), run `sb chat` or `sb serve --ui` in the
same directory — both bind to the project's model with the DHIS2 tool available.

!!! tip "Next to a live DHIS2 tab"
    Scaffold from the **`dhis2` template** (`sb init mydhis2 --template dhis2`) to also
    get an `[assistant]` block, then serve it on a pinned port and drive it from the
    [Chrome side panel](extension.md): a target banner that confirms your active tab matches the
    instance, **Verify**, **Who am I here?**, and **Use my login** to let the tools act as you
    (a read-only PAT minted in the tab, never a copied password).

## Recap

| Step | Command |
| --- | --- |
| Scaffold a project | `sb init dhis2 --model …gemma-4-12B-it-QAT-GGUF` |
| Attach bridge | add a `.mcp.json` entry: `uvx dhis2w-mcp-bridge` with `env.DHIS2_PROFILE=play42` |
| Verify tools | `sb project show` |
| Try it | `sb chat -p "…"` (or `sb serve --ui`) |
| Retarget server | change the profile name in the `env` prefix |
