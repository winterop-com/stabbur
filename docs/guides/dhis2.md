# DHIS2 tools, profiles & prompts

heim's north star is a **local, self-hosted DHIS2 assistant** — your own model driving
DHIS2 through the `d2w` tools, with nothing leaving your machine. This guide covers the
pieces you wire together: a **DHIS2 profile** (which server + how to authenticate), the
**MCP bridge** (the tool the model calls), and a big set of **suggested prompts** to try.

For the end-to-end "scaffold a portable project" walkthrough, see the
[DHIS2 assistant worked example](dhis2-project.md). For everything the `d2w` CLI and its
MCP servers can do, the canonical reference is the official docs:
**<https://winterop-com.github.io/dhis2w-utils/>**.

## The pieces

```
your model (heim serve --ui / heim chat)
  -> agent loop + MCP client (heim)
      -> dhis2w-mcp-bridge  (the dhis2_cli tool)
          -> d2w CLI  --(profile: URL + auth)-->  DHIS2 server
```

- **`d2w`** — the DHIS2 command-line client (from `dhis2w-utils`). Install with
  `uv tool install dhis2w-cli` (or `uvx d2w ...` to run without installing).
- **A profile** — a named `(base URL, auth)` pair. The model never sees a URL or token;
  it just drives `d2w`, and `d2w` resolves the profile. Credentials stay on the machine.
- **The bridge** — an MCP server exposing `d2w` as tools. Three sizes (below).

## 1. Create a DHIS2 profile

Profiles live in a TOML file and are managed with `d2w profile`. Two scopes:

- **Global** — `~/.config/dhis2/profiles.toml` (`--global`, the default): applies everywhere.
- **Project-local** — `./.dhis2/profiles.toml` (`--local`): committed *next to a project*,
  overrides global. Handy for pinning a project to one instance. **Never commit secrets** —
  tokens are stored separately / in env, not in the profile URL.

Secrets are never passed as flags (they would leak into shell history) — `d2w` reads them
from env (`DHIS2_PAT`, `DHIS2_PASSWORD`, …) or prompts interactively.

=== "PAT (recommended)"

    A Personal Access Token is the cleanest auth for an assistant. Create the profile and
    verify it in one step:

    ```bash
    # token read from the DHIS2_PAT env var (or prompted), never from a flag
    DHIS2_PAT=d2pat_xxx d2w profile add myserver \
      --url https://dhis2.example.org --auth pat --verify --default
    ```

    `d2w profile bootstrap` can even provision the PAT on the server for you in one shot.

=== "Basic auth (demo servers)"

    The public DHIS2 play/demo servers use username + password:

    ```bash
    DHIS2_PASSWORD='district' d2w profile add play42 \
      --url https://play.im.dhis2.org/dev-2-42 \
      --auth basic --username admin --verify
    ```

=== "OAuth2"

    ```bash
    d2w profile add myserver --url https://dhis2.example.org --auth oauth2 \
      --client-id heim --scope ALL
    d2w profile login myserver     # runs the authorization-code flow, persists tokens
    ```

=== "Project-local (.dhis2/)"

    Pin a profile to the current project directory:

    ```bash
    d2w profile add prod --url https://dhis2.example.org --auth pat --local --verify
    # -> writes ./.dhis2/profiles.toml (overrides global for commands run here)
    ```

Manage them:

```bash
d2w profile list                 # every profile, its source (global/local), and the default
d2w profile show myserver        # one profile (secrets redacted)
d2w profile verify --all         # probe /api/system/info + /api/me for each
d2w profile default myserver     # set the default profile
```

## 2. Pick a bridge tier

The bridge is what the model actually calls. Three sizes trade tool count against how much
context (and how capable a model) they need — pick by your model:

| Server | Tools | Best for |
|--------|-------|----------|
| **`dhis2w-mcp-bridge`** | 1 (`dhis2_cli`) | **Smaller local models.** One tool that runs any `d2w` command; the model composes CLI argv. heim's default. |
| **`dhis2w-mcp-router`** | 2 (`search_tools` / `call_tool`) | Mid models. Lazy typed discovery through a single guarded chokepoint; has a **read-only mode**. |
| **`dhis2w-mcp`** | ~304 typed tools | Big-context hosts. Every operation as its own typed tool. |

**Read-only vs write.** The bridge enforces read-only when `DHIS2_MCP_READONLY=1` — a
fail-closed allowlist of read commands; any mutating command is refused. Start here. Drop
the variable to allow writes (create / update / delete). Mutating commands against known
shared/demo hosts are refused regardless, so a confused model can't scribble on the public
play servers.

## 3. Wire it into heim

heim ships a curated entry, so one command adds the bridge to a project's `.mcp.json`:

```bash
heim mcp add dhis2     # then edit DHIS2_PROFILE in the generated entry
```

That writes a standard `mcpServers` entry roughly like:

```json
{
  "mcpServers": {
    "dhis2": {
      "command": "dhis2w-mcp-bridge",
      "env": { "DHIS2_PROFILE": "play42", "DHIS2_MCP_READONLY": "1" }
    }
  }
}
```

Change `DHIS2_PROFILE` to your profile name; drop `DHIS2_MCP_READONLY` to allow writes.
Swap `dhis2w-mcp-bridge` for `dhis2w-mcp-router` or `dhis2w-mcp` to move up a tier. (In a
non-uv project the command is `uvx dhis2w-mcp-bridge`; the older single-string form —
`command = "env DHIS2_PROFILE=play42 … uvx dhis2w-mcp-bridge"` — still works too.)

Confirm the model reaches it:

```bash
heim project show          # lists the wired MCP servers + their tool counts
heim chat                  # then: "How many organisation units are there? Use the tools."
```

In `heim chat`, type `/mcp` to see the loaded servers and tools, or press `Ctrl+P` for the
command palette (enable/disable/reconnect a server).

## Suggested prompts

Copy-paste these into `heim chat` (or the web chat) with the DHIS2 bridge attached. They
work against the **play42** demo (DHIS2 "Sierra Leone"); adapt names/UIDs for your server.
The system prompt should tell the model to **always use the tools, never answer from memory**.

### Discover the instance

- "What DHIS2 version is this server running, and what is the system name?"
- "Who am I logged in as?"
- "List the organisation unit levels and their names."
- "What resource types does this DHIS2 instance have? Show me the metadata types."

### Counts and inventory

- "How many organisation units, data elements, indicators, and data sets are there?"
- "How many programs are configured? List their names."
- "How many option sets does this instance have?"
- "List the first 10 data sets with their period types."

### Name to UID (and back)

- "What is the UID of the data element named 'ANC 1st visit'?"
- "What is the UID of the organisation unit 'Bo'? What level is it at?"
- "What is the name of the organisation unit with UID ImspTQPwCqd?"
- "Search for anything matching 'malaria' and tell me what types the matches are."

### Narrowing and fields

- "List all AGGREGATE data elements whose name contains 'ANC' — just id and name."
- "What fields does a dataElement have? Show me its schema."
- "Which data sets is the data element 'ANC 1st visit' part of?"
- "Show the indicators whose name starts with 'ANC', with their numerator descriptions."

### Analytics (aggregate data)

Analytics needs UIDs, not names — a good test of multi-step tool use (resolve name -> UID,
then query):

- "What was the value of 'ANC 1st visit' for all of Sierra Leone over the last 12 months?"
- "Compare 'ANC 1st visit' and 'ANC 2nd visit' nationally for the last 4 quarters."
- "Which district had the highest 'ANC 1st visit' count last month?"

### Org-unit hierarchy

- "Show the organisation unit tree for Sierra Leone down to district level."
- "How many facilities (level 4) are under the Bo district?"
- "What are the child organisation units of 'Bombali'?"

### Writes (only with read-only mode off, on a server you own)

Do these against your **own** instance, never a shared demo (mutations to demo hosts are
refused):

- "Create a new data element group called 'heim test group'."
- "Rename the option set 'X' to 'Y'."
- "Add the data element 'ANC 1st visit' to the data set 'Z'."

## Where next

- [DHIS2 assistant worked example](dhis2-project.md) — scaffold a portable project with a
  local model copy, wire the bridge, and confirm the model calls it.
- [Chrome side panel](extension.md) — put the assistant next to a live DHIS2 tab: the target
  banner, Verify, "Who am I here?", and "Use my login" (read-only PAT bind).
- [Tools (MCP)](tools.md) — how heim's MCP client and agent loop work across all servers.
- [Model catalog](model-catalog.md) — validated chat models; the `tools-dhis2` benchmark
  suite (`heim benchmark run tools-dhis2`) scores which of them best drive the bridge.
- Official `dhis2w-utils` docs: <https://winterop-com.github.io/dhis2w-utils/>.
