"""Project templates for ``heim project new --template <name>``.

Each template presets the wizard — a default model, system prompt, MCP tools, uv extras, a
spoken-reply voice, and example files — so a purpose-built assistant is reproducible in one
command. Kept out of ``cli.py`` so the growing set (DHIS2, one per bundled MCP, one per voice
model) doesn't bloat the CLI module.
"""

from __future__ import annotations

from typing import Any

from heim.models import ProjectTemplate

# --- example prompt files -------------------------------------------------------------------

_DHIS2_PROMPTS_MD = """\
# Example prompts

Copy these into `uv run heim chat` (or the web UI). The assistant is bound to a DHIS2-capable
model and the DHIS2 CLI bridge (read-only), pointed at the public **play42** demo (DHIS2
"Sierra Leone").

## Discover the instance
- What DHIS2 version is this server running, and what is the system name?
- Who am I logged in as?
- List the organisation unit levels and their names.

## Counts and inventory
- How many organisation units, data elements, and indicators are there?
- How many data sets are configured? List a few of their names.

## Name to UID (and back)
- What is the UID of the data element named 'ANC 1st visit'?
- What is the UID of the organisation unit 'Bo', and what level is it at?
- What is the name of the organisation unit with UID ImspTQPwCqd?

## Analytics (multi-step: resolve name -> UID, then query)
- What was 'ANC 1st visit' for all of Sierra Leone over the last 12 months?
- Compare 'ANC 1st visit' and 'ANC 2nd visit' nationally for the last 4 quarters.
"""

_DHIS2_WRITE_PROMPTS_MD = """\
# Example prompts (write-enabled)

This assistant can **create, rename, and delete** metadata — it is pointed at a **local**
DHIS2 (localhost:8080), not a shared demo. Prefix throwaway objects with `HEIM_` so they're
easy to find and clean up. Always confirm before deleting.

## Safe reads first
- What DHIS2 version is this, and who am I logged in as?
- How many data elements are there right now?

## Create
- Create a NUMBER aggregate data element named 'HEIM_ANC_TEST' (short name 'HEIM_ANC') and
  tell me its UID.
- Create an indicator group named 'HEIM_TEST_GROUP'.

## Update / rename
- Rename the data element 'HEIM_ANC_TEST' to 'HEIM_ANC_TEST_v2'.
- Give 'HEIM_ANC_TEST_v2' the description 'created by the heim write assistant'.

## Delete (confirm first)
- Delete the data element 'HEIM_ANC_TEST_v2'. Show me it's gone afterwards.
"""

_DHIS2_PROFILE_EXAMPLE = """\
# Copy to .dhis2/profiles.toml (mkdir -p .dhis2) and fill in your instance, or run:
#   d2w profile add play42 --url <url> --auth basic --username <user> --local
# For the public DHIS2 demo the credentials are admin / district.
default = "play42"

[profiles.play42]
base_url = "https://play.im.dhis2.org/dev-2-42"
auth = "basic"
username = "admin"
# password = "district"   # public demo; for a real instance prefer a PAT (auth = "pat")
"""

_DHIS2_LOCAL_PROFILE_EXAMPLE = """\
# Copy to .dhis2/profiles.toml (mkdir -p .dhis2). This points at a LOCAL DHIS2 you control —
# write operations are enabled, so never point it at a shared/production instance.
# Demo credentials for a local dev instance are admin / district.
default = "local_basic"

[profiles.local_basic]
base_url = "http://localhost:8080"
auth = "basic"
username = "admin"
# password = "district"   # local dev; for anything real prefer a PAT (auth = "pat")
"""

_DHIS2_MULTI_PROMPTS_MD = """\
# Example prompts (two DHIS2 instances)

This assistant is a **registry of two read-only DHIS2 targets** on the same host, told apart by
path: **play42** (`/dev-2-42`) and **play41** (`/dev-2-41`). Each has its own tool bridge, and the
Chrome side panel routes tools to whichever instance the active tab is on (a picker resolves ties).
In the CLI, pick a target with `--target play42` / `--target play41`; with no flag the primary
(play42) is used.

## Per instance
- What DHIS2 version is play42 running, and what is the system name?
- Who am I logged in as on play41?
- How many organisation units are on play42?

## Compare the two
- Compare the DHIS2 version of play42 and play41.
- How many data elements does each instance have?

## Name to UID (per target)
- On play42, what is the UID of the data element named 'ANC 1st visit'?
- On play41, what level is the organisation unit 'Bo' at?
"""

_DHIS2_MULTI_PROFILE_EXAMPLE = """\
# Copy to .dhis2/profiles.toml (mkdir -p .dhis2). This project targets TWO DHIS2 instances, so it
# needs a d2w profile per instance — one named 'play42', one named 'play41'. Each target's bridge is
# spawned with its own DHIS2_PROFILE (see .mcp.json), so the names must match.
# For the public DHIS2 demo the credentials are admin / district.
default = "play42"

[profiles.play42]
base_url = "https://play.im.dhis2.org/dev-2-42"
auth = "basic"
username = "admin"
# password = "district"   # public demo; for a real instance prefer a PAT (auth = "pat")

[profiles.play41]
base_url = "https://play.im.dhis2.org/dev-2-41"
auth = "basic"
username = "admin"
# password = "district"   # public demo; for a real instance prefer a PAT (auth = "pat")
"""

_CODER_PROMPTS_MD = """\
# Example prompts

- Read `README.md` and summarize what this project does.
- What files changed in the last commit? Explain the diff.
- Find every place `foo()` is called and list the files.
- Refactor `x.py` to ... (describe the change), then show me the new version.
"""

_RESEARCH_PROMPTS_MD = """\
# Example prompts

- Search the web for the latest on <topic> and summarize the top results.
- Fetch <url> and give me the key points.
- Compare <A> and <B> using current sources; cite the pages you used.
"""

_DATETIME_PROMPTS_MD = """\
# Example prompts

- What time is it right now in Tokyo, and what's the UTC offset?
- How many days until 2027-01-01?
- Convert 2026-07-05T14:30 in Europe/Oslo to America/New_York.
- What day of the week was 1994-05-17?
"""

_UTILS_PROMPTS_MD = """\
# Example prompts

- Base64-encode the string "heim rocks", then decode it back.
- Give me the SHA-256 of "hello world".
- Generate a UUID and a 24-character random token.
- URL-encode `a b&c=d`.
"""

_SEARCH_PROMPTS_MD = """\
# Example prompts

- Search for the latest release notes for DHIS2 and summarize what's new.
- Who won the most recent Formula 1 race? Cite your source.
- Find three tutorials on MLX for Apple Silicon and list their URLs.
"""

_WEB_PROMPTS_MD = """\
# Example prompts

- Read https://example.com and give me the page as clean Markdown.
- Summarize the main points of <url>.
- From <url>, extract every link in the "Documentation" section.

The web reader runs a headless Chromium (installed by `uv sync` via the `web` extra), so the
first run downloads the browser once.
"""

_BROWSE_PROMPTS_MD = """\
# Example prompts

This assistant drives a real browser (Playwright). Unlike the one-page `web` reader, it can
navigate, read, click, fill forms, and move across pages in one task.

## Read / extract
- Go to https://news.ycombinator.com and list the top 5 story titles with their points.
- Open <url> and extract every link under the "Documentation" heading.
- Navigate to <a JavaScript app> and tell me the text it renders (it runs the page's scripts).

## Locate
- On <url>, find the section that mentions "pricing" and quote it.

## Visual (a vision model sees the screenshot)
- Open <url>, take a screenshot, and describe the header's color and layout.

## Multi-step
- Search <site> for "release notes", open the first result, and summarize it.

Notes: it runs headless by default; remove `--headless` in `.mcp.json` to watch/drive the
browser window. For slow/dynamic pages, ask it to "wait for the content to load, then read it".
"""

_MEMORY_PROMPTS_MD = """\
# Example prompts

- Remember that my project deadline is 2026-08-01.
- Note that the staging DB password rotates every 90 days.
- What have I asked you to remember so far?
- Forget the note about the staging DB.

Notes persist across sessions in the memory server's store.
"""

_WEATHER_PROMPTS_MD = """\
# Example prompts

- What's the weather in Oslo right now?
- Will it rain in Bergen tomorrow afternoon?
- Compare the next-24h forecast for Tromso and Trondheim.

Data comes from the Norwegian Meteorological Institute (met.no / yr.no); place names are
geocoded, so worldwide cities work too.
"""

_FILES_PROMPTS_MD = """\
# Example prompts

- List the files in this project and tell me what each top-level one is.
- Read `README.md` and summarize it.
- Search the project for the string "heim.toml" and show where it appears.

The files server is read-only by default (rooted at the project directory). Set
`HEIM_FILES_WRITABLE=1` in `.env` to allow writes, and `HEIM_FILES_ROOT` to change the root.
"""

_EXEC_PROMPTS_MD = """\
# Example prompts

- Compute the 30th Fibonacci number in Python.
- Parse this CSV text and give me the column sums: "a,b\\n1,2\\n3,4".
- What's the SHA-256 of "hello" — write and run the Python.

Python runs in a locked-down Docker sandbox (no network, read-only filesystem, capped
memory/CPU, timeout), so Docker must be running.
"""

_KOKORO_PROMPTS_MD = """\
# Example prompts (spoken replies)

This assistant speaks its replies aloud with **Kokoro** (the lightweight in-chat voice). Open
the web UI (`uv run heim serve --ui`) and enable Listen, or use `uv run heim chat`.

- Introduce yourself in one sentence.
- Tell me a very short joke.
- Read this back to me: "the quick brown fox".

Pick a different Kokoro voice with `chat_voice` in `heim.toml` (see `uv run heim voice voices`).
"""

_WHISPER_PROMPTS_MD = """\
# A dictation assistant (listen + speak)

This project is a full voice loop: **dictate** with your mic (transcribed by Whisper STT) and
hear replies spoken back (Kokoro). Open the web Voice surface with `uv run heim serve --ui`.

- Hold the mic button and say: "what's on my todo list?"
- Dictate a note and ask the assistant to summarize it.

Whisper (`whisper-large-v3-turbo`) is the transcription model; replies are voiced by Kokoro.
"""

_DIA_PROMPTS_MD = """\
# Dia — expressive dialogue + voice cloning (Voice studio)

Dia is a Voice-studio model (dialogue and voice cloning), used via `heim voice`, not as an
in-chat reply voice. The chat assistant here speaks replies with Kokoro; explore Dia with:

```bash
uv run heim voice speak --model dia --seed 42 "Heim can talk. [laughs] Isn't that nice?"
# Clone a voice from a short clip:
uv run heim voice speak --model dia --ref-audio ref.wav --ref-text "exact transcript" "New line in that voice."
```

Pin `--seed` for a reproducible voice (Dia is otherwise random each run). Run `uv run heim
voice setup` once so Dia's codec lives on the drive and works offline.
"""

_QWEN3TTS_PROMPTS_MD = """\
# Qwen3-TTS (Voice studio)

Qwen3-TTS is included as a Voice-studio TTS model. Note: mlx-audio does not yet load its
separate speech tokenizer, so `generate` produces no audio today (tracked in heim's roadmap);
this project stages the model + config so it works once support lands. The chat assistant
speaks replies with Kokoro in the meantime.

```bash
# Once supported:
uv run heim voice speak --model qwen3-tts "Hello from Qwen3-TTS."
```
"""

# --- shared prompt fragments ----------------------------------------------------------------

_VOICE_ASSISTANT_PROMPT = (
    "You are a friendly voice assistant. Keep replies short and natural for speech — a sentence "
    "or two, no bullet lists or code unless asked. Spell things out the way you'd say them aloud."
)


def _mcp_prompt(tool_desc: str) -> str:
    """A system prompt for a single-MCP showcase assistant."""
    return (
        f"You are a helpful assistant with access to {tool_desc}. Always use the available tools "
        "to get real results instead of guessing, and state what the tool returned. Keep answers concise."
    )


def _dhis2_assistant(
    profile: str,
    base_url: str,
    readonly: bool,
    *,
    mcp_server: str = "dhis2",
    owns_server: bool = False,
) -> dict[str, Any]:
    """Build an ``[assistant]`` / ``[[assistants]]`` target block for the DHIS2 templates.

    Shared by the single-target ``dhis2`` / ``dhis2-write`` templates and the multi-target
    ``dhis2-multi`` template. Targets differ only in the profile name, base URL, and readonly flag;
    the probe + mint recipe are identical, and each bind mode's ``command`` / ``unbind_command``
    target the given profile with the secret handed over via its ``secret_env``.

    ``mcp_server`` is the ``.mcp.json`` server name this target's tools are namespaced under — it sets
    the ``verify`` tool namespace (``<mcp_server>__dhis2_cli``). ``owns_server`` controls whether the
    block declares ``mcp_servers = [mcp_server]``: the **multi-target** template turns it on so each
    target owns *only* its own bridge (per-turn tool routing). The single-target templates leave it off
    (the default) and emit **no** ``mcp_servers`` — the compat "owns all resolved servers" rule — so a
    user's extra machine-global servers (datetime, …) keep reaching the assistant untouched.
    """

    def _mode(auth: str, secret_env: str, extra_secret_env: str | None = None) -> dict[str, Any]:
        mode: dict[str, Any] = {
            "command": ["d2w", "profile", "add", profile, "--url", "{base_url}", "--auth", auth, "--local"],
            "secret_env": secret_env,
            # --yes: d2w >= 1.3 confirms interactively; heim runs bind commands with stdin closed,
            # so without it the prompt reads EOF, the remove aborts, and the PAT-holding profile
            # silently survives the unbind.
            "unbind_command": ["d2w", "profile", "remove", profile, "--local", "--yes"],
            "unbind_note": (
                "Restore the shared demo profile with: cp examples/dhis2-profiles.toml .dhis2/profiles.toml"
            ),
        }
        if extra_secret_env is not None:
            mode["extra_secret_env"] = extra_secret_env
        return mode

    block: dict[str, Any] = {
        "name": profile,
        "base_url": base_url,
        "auth": "basic",
        "readonly": readonly,
        "source": f"d2w profile {profile}",
        "verify": {"tool": f"{mcp_server}__dhis2_cli", "args": {"args": ["profile", "verify", profile]}},
        "probe": {
            "paths": ["/api/me.json?fields=name,username", "/api/system/info.json"],
            "fields": {
                "username": ["0.username"],
                "name": ["0.name"],
                "version": ["1.version"],
                "instanceName": ["1.systemName", "1.instanceName"],
            },
            "label": "Browsing as {name} on {instanceName} ({version})",
        },
        "bind": {
            "mint_mode": "pat",
            "fallback_mode": "session",
            "mint_path": "/api/apiToken",
            "mint_method": "POST",
            "mint_payload": (
                '{"type":"PERSONAL_ACCESS_TOKEN_V2","expire":{expires_ms},'
                '"attributes":[{"type":"MethodAllowedList","allowedMethods":{allowed_methods}}],'
                '"description":{description}}'
            ),
            "mint_token_field": "response.key",
            "mint_id_field": "response.uid",
            "revoke_path": "/api/apiToken/{credential_id}",
            "expires_in_days": 30,
            "methods_readonly": ["GET"],
            "methods_full": ["GET", "POST", "PUT", "PATCH", "DELETE"],
            "session_cookie": "JSESSIONID",
            "modes": {
                "pat": _mode("pat", "DHIS2_PAT"),
                # A session bind carries the JSESSIONID cookie plus the CSRF token DHIS2 requires on
                # writes; the extra secret rides into the stored d2w profile via DHIS2_SESSION_XSRF.
                "session": _mode("session", "DHIS2_SESSION_COOKIE", extra_secret_env="DHIS2_SESSION_XSRF"),
            },
        },
    }
    if owns_server:
        # Multi-target: this target owns ONLY its own bridge, so per-turn routing sends just this
        # server's tools to it. Single-target templates leave this out (owns-all compat).
        block["mcp_servers"] = [mcp_server]
    return block


# --- the templates --------------------------------------------------------------------------

TEMPLATES: dict[str, ProjectTemplate] = {
    "coder": ProjectTemplate(
        model="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
        system_prompt=(
            "You are a coding assistant working in a local repository. Use the git and filesystem "
            "tools to read real files and history instead of guessing. Make minimal, focused changes; "
            "show diffs; explain what you changed and why. Never invent file contents or APIs."
        ),
        mcp=[
            ("git", "uvx mcp-server-git --repository ."),  # pip -> pinned in a uv project
            ("filesystem", "bunx @modelcontextprotocol/server-filesystem ."),  # node -> stays as-is
        ],
        files={"examples/prompts.md": _CODER_PROMPTS_MD},
        next_steps=(
            "The filesystem tool needs bun (bunx) on PATH; the git tool reads the current repo.\n"
            "  uv sync && uv run heim chat"
        ),
    ),
    "research": ProjectTemplate(
        model="lmstudio-community/gemma-4-12B-it-QAT-GGUF",
        system_prompt=(
            "You are a research assistant. Use the search and fetch tools to find and read current "
            "sources instead of answering from memory. Cite the pages you used and be clear about "
            "what is uncertain."
        ),
        mcp=[
            ("search", "heim-mcp-search"),  # bundled with heim -> no extra pin
            ("fetch", "uvx mcp-server-fetch"),  # pip -> pinned in a uv project
        ],
        files={"examples/prompts.md": _RESEARCH_PROMPTS_MD},
        next_steps=(
            "search uses DuckDuckGo by default (no key). For Brave/Exa set the relevant API key.\n"
            "  uv sync && uv run heim chat"
        ),
    ),
    "dhis2": ProjectTemplate(
        # Ornith-1.0-9B won the tools-dhis2 benchmark (12/12, fastest, smallest).
        model="deepreinforce-ai/Ornith-1.0-9B-GGUF",
        system_prompt=(
            "You are a DHIS2 assistant for a connected DHIS2 instance. For questions about DHIS2 data or "
            "metadata - counts, UIDs, names, analytics, system details - use the dhis2 tools (the dhis2_cli "
            "tool) to look up real values; never invent counts, UIDs, or metadata. To use a name in analytics "
            "or a filter, resolve it to a UID first with a metadata search or a filtered list. Messages may "
            "begin with page context supplied by the user's browser: lines labeled 'Page URL:', 'Page title:', "
            "'Selected text:', 'Page text (truncated):', 'Browser session user:', and 'Tool account:'. Treat "
            "that context as information the user gave you: answer questions about the current page, its visible "
            "content, or the signed-in user directly from it, without calling tools, and answer general "
            "questions normally instead of refusing. Two accounts can differ: the 'Browser session user' is the "
            "person viewing the page in their browser; your tools authenticate separately as the 'Tool "
            "account'. When asked 'who am I', answer with the browser session user from the context when "
            "present; report the tool account only when asked which credentials the tools use. Keep answers "
            "concise and state the values you retrieved."
        ),
        mcp=[("dhis2", "env DHIS2_PROFILE=play42 DHIS2_MCP_READONLY=1 uvx dhis2w-mcp-bridge")],
        assistant=_dhis2_assistant("play42", "https://play.im.dhis2.org/dev-2-42", readonly=True),
        files={"examples/prompts.md": _DHIS2_PROMPTS_MD, "examples/dhis2-profiles.toml": _DHIS2_PROFILE_EXAMPLE},
        next_steps=(
            "Set up the DHIS2 profile, then run:\n"
            "  mkdir -p .dhis2 && cp examples/dhis2-profiles.toml .dhis2/profiles.toml   # demo: admin/district\n"
            "  uv sync && uv run heim project show      # confirm the model + dhis2 tools are wired\n"
            "  uv run heim serve --ui                   # or: uv run heim chat\n"
            "Point it at your own instance: edit .dhis2/profiles.toml (or `d2w profile add … --local`) "
            "and DHIS2_PROFILE in heim.toml; drop DHIS2_MCP_READONLY=1 to allow writes."
        ),
    ),
    "dhis2-write": ProjectTemplate(
        # A write-enabled DHIS2 assistant against a LOCAL instance you control (not a shared demo).
        model="deepreinforce-ai/Ornith-1.0-9B-GGUF",
        system_prompt=(
            "You are a DHIS2 assistant that can READ and WRITE metadata on a connected DHIS2 instance via the "
            "dhis2 tools (the dhis2_cli tool). For DHIS2 data or metadata - counts, UIDs, names, analytics - "
            "use the tools for real values; never invent them. Work one step at a time and finish what you "
            "start. For any write: first resolve every name or dependency to its UID (for example, look up "
            "an indicator type before creating an indicator); then state exactly what you will change; then "
            "make the change with the tool; then read it back to confirm it took and note the UID. When a "
            "task creates several objects or links them, at the end delete them in dependency-safe order "
            "(members and dependents before what they belong to) and read back to confirm each is gone - "
            "leave nothing behind. Prefer NUMBER value types and sensible defaults when creating. Do not "
            "report a task done until a read-back confirms the final state; if a step fails, say so plainly "
            "instead of claiming success. Messages may begin with page context supplied by the user's browser: "
            "lines labeled 'Page URL:', 'Page title:', 'Selected text:', 'Page text (truncated):', 'Browser "
            "session user:', and 'Tool account:'. Answer questions about the current page or the signed-in "
            "user directly from that context, without calling tools, and answer general questions normally "
            "instead of refusing. The 'Browser session user' is the person viewing the page; your tools "
            "authenticate separately as the 'Tool account' - writes happen as the tool account, so say so "
            "when it matters. When asked 'who am I', prefer the browser session user from the context. Keep "
            "answers concise and report the UIDs and outcomes you got."
        ),
        mcp=[("dhis2", "env DHIS2_PROFILE=local_basic uvx dhis2w-mcp-bridge")],  # no READONLY -> writes enabled
        assistant=_dhis2_assistant("local_basic", "http://localhost:8080", readonly=False),
        files={
            "examples/prompts.md": _DHIS2_WRITE_PROMPTS_MD,
            "examples/dhis2-profiles.toml": _DHIS2_LOCAL_PROFILE_EXAMPLE,
        },
        next_steps=(
            "This assistant can MUTATE metadata — point it only at a local/throwaway DHIS2.\n"
            "  mkdir -p .dhis2 && cp examples/dhis2-profiles.toml .dhis2/profiles.toml   # local: admin/district\n"
            "  uv sync && uv run heim project show\n"
            "  uv run heim serve --ui                   # or: uv run heim chat\n"
            "The bridge runs read-write (no DHIS2_MCP_READONLY). Prefix test objects with HEIM_ so "
            "they're easy to clean up."
        ),
    ),
    "dhis2-multi": ProjectTemplate(
        # A registry of TWO read-only DHIS2 targets on the same host (told apart by path): play42
        # (/dev-2-42) and play41 (/dev-2-41). Each target owns its own bridge, so per-turn routing
        # sends only that instance's tools to it — the browser panel auto-selects by the active tab.
        model="deepreinforce-ai/Ornith-1.0-9B-GGUF",
        system_prompt=(
            "You are a DHIS2 assistant that can talk to more than one connected DHIS2 instance. Each "
            "instance has its own set of dhis2 tools (a dhis2_cli tool namespaced per instance, e.g. "
            "play42__dhis2_cli and play41__dhis2_cli); use the tools for the instance the question is "
            "about. For questions about DHIS2 data or metadata - counts, UIDs, names, analytics, system "
            "details - use those tools to look up real values; never invent counts, UIDs, or metadata. To "
            "use a name in analytics or a filter, resolve it to a UID first with a metadata search or a "
            "filtered list. When a question compares two instances, query each with its own tools and report "
            "both. Messages may begin with page context supplied by the user's browser: lines labeled 'Page "
            "URL:', 'Page title:', 'Selected text:', 'Page text (truncated):', 'Browser session user:', and "
            "'Tool account:'. Treat that context as information the user gave you: answer questions about the "
            "current page, its visible content, or the signed-in user directly from it, without calling "
            "tools, and answer general questions normally instead of refusing. The 'Browser session user' is "
            "the person viewing the page in their browser; your tools authenticate separately as the 'Tool "
            "account'. When asked 'who am I', answer with the browser session user from the context when "
            "present; report the tool account only when asked which credentials the tools use. Keep answers "
            "concise and state the values you retrieved and which instance they came from."
        ),
        mcp=[
            ("play42", "env DHIS2_PROFILE=play42 DHIS2_MCP_READONLY=1 uvx dhis2w-mcp-bridge"),
            ("play41", "env DHIS2_PROFILE=play41 DHIS2_MCP_READONLY=1 uvx dhis2w-mcp-bridge"),
        ],
        assistants=[
            _dhis2_assistant(
                "play42", "https://play.im.dhis2.org/dev-2-42", readonly=True, mcp_server="play42", owns_server=True
            ),
            _dhis2_assistant(
                "play41", "https://play.im.dhis2.org/dev-2-41", readonly=True, mcp_server="play41", owns_server=True
            ),
        ],
        files={
            "examples/prompts.md": _DHIS2_MULTI_PROMPTS_MD,
            "examples/dhis2-profiles.toml": _DHIS2_MULTI_PROFILE_EXAMPLE,
        },
        next_steps=(
            "Two read-only DHIS2 targets (play42 + play41), auto-selected by the browser tab.\n"
            "  mkdir -p .dhis2 && cp examples/dhis2-profiles.toml .dhis2/profiles.toml   # demo: admin/district\n"
            "  uv sync && uv run heim project show      # confirm both targets + their bridges are wired\n"
            "  uv run heim serve --ui                   # panel routes tools by the active tab\n"
            "  uv run heim chat --target play41         # in the CLI, pick a target (default: play42)\n"
            "Point at your own instances: edit .dhis2/profiles.toml, the two base_urls in heim.toml, and\n"
            "DHIS2_PROFILE in each .mcp.json bridge; drop DHIS2_MCP_READONLY=1 to allow writes."
        ),
    ),
    # --- one showcase per bundled MCP server ---
    "mcp-datetime": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=_mcp_prompt("date & time tools (current time, timezones, conversions, arithmetic)"),
        mcp=[("datetime", "heim-mcp-datetime")],
        files={"examples/prompts.md": _DATETIME_PROMPTS_MD},
        next_steps="  uv sync && uv run heim chat",
    ),
    "mcp-utils": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=_mcp_prompt("text & encoding utilities (base64, hashes, UUIDs, URL-encoding, random tokens)"),
        mcp=[("utils", "heim-mcp-utils")],
        files={"examples/prompts.md": _UTILS_PROMPTS_MD},
        next_steps="  uv sync && uv run heim chat",
    ),
    "mcp-search": ProjectTemplate(
        model="lmstudio-community/gemma-4-12B-it-QAT-GGUF",
        system_prompt=(
            "You are a research assistant with a web search tool. Search for current information "
            "instead of answering from memory, and cite the sources you used. Be clear about uncertainty."
        ),
        mcp=[("search", "heim-mcp-search")],
        files={"examples/prompts.md": _SEARCH_PROMPTS_MD},
        next_steps=(
            "search uses DuckDuckGo by default (no key). For Brave/Exa set the relevant API key in .env.\n"
            "  uv sync && uv run heim chat"
        ),
    ),
    "mcp-web": ProjectTemplate(
        model="lmstudio-community/gemma-4-12B-it-QAT-GGUF",
        system_prompt=(
            "You are a web-reading assistant. Use the web tool to fetch and read pages, then answer "
            "from what the page actually says. Quote or cite the parts you relied on."
        ),
        mcp=[("web", "heim-mcp-web")],
        files={"examples/prompts.md": _WEB_PROMPTS_MD},
        extras=["web"],
        next_steps=(
            "The web reader needs the `web` extra (Playwright + Chromium); `uv sync` installs it and\n"
            "the first run downloads the browser once.\n"
            "  uv sync && uv run playwright install chromium && uv run heim chat"
        ),
    ),
    "browse": ProjectTemplate(
        # gemma-4-12B has vision, so it can also read the screenshots the browser returns.
        model="lmstudio-community/gemma-4-12B-it-QAT-GGUF",
        system_prompt=(
            "You are a web-browsing assistant driving a real browser via the Playwright tools. "
            "To read or extract page content, use browser_snapshot (the page's accessibility tree). "
            "To locate a specific known string, use browser_find. If content is missing or the page "
            "is still loading, use browser_wait_for on the expected text, then snapshot again — the "
            "browser runs the page's JavaScript, so client-rendered pages work once they finish "
            "loading. Use browser_take_screenshot only for questions about visual appearance, layout, "
            "or images. Answer from what the page actually shows, and say when something isn't there."
        ),
        mcp=[("playwright", "bunx @playwright/mcp@latest --headless --isolated")],
        files={"examples/prompts.md": _BROWSE_PROMPTS_MD},
        next_steps=(
            "Playwright MCP runs via bunx (needs bun) and downloads a browser on first run.\n"
            "Runs headless; remove --headless in .mcp.json to watch/drive the browser window.\n"
            "  uv run heim chat"
        ),
    ),
    "mcp-memory": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=(
            "You are an assistant with a persistent memory tool. When the user asks you to remember "
            "something, store it; recall stored notes when relevant; forget on request. Use the tool "
            "rather than relying on the conversation alone."
        ),
        mcp=[("memory", "heim-mcp-memory")],
        files={"examples/prompts.md": _MEMORY_PROMPTS_MD},
        next_steps="  uv sync && uv run heim chat",
    ),
    "mcp-weather": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=_mcp_prompt("a weather tool (met.no/yr.no forecasts by place name)"),
        mcp=[("weather", "heim-mcp-weather-yr")],
        files={"examples/prompts.md": _WEATHER_PROMPTS_MD},
        next_steps="  uv sync && uv run heim chat",
    ),
    "mcp-files": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=(
            "You are an assistant that can browse, read, and search files under a project directory "
            "using the files tool. Use it to read real file contents instead of guessing. Keep answers concise."
        ),
        mcp=[("files", "heim-mcp-files")],
        files={"examples/prompts.md": _FILES_PROMPTS_MD},
        next_steps=(
            "Read-only by default, rooted at the project. Set HEIM_FILES_WRITABLE=1 / HEIM_FILES_ROOT in .env.\n"
            "  uv sync && uv run heim chat"
        ),
    ),
    "mcp-exec": ProjectTemplate(
        model="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
        system_prompt=(
            "You are a coding assistant that can execute Python in a sandbox via the exec tool. When a "
            "question needs computation, write and run Python rather than doing it in your head, then "
            "report the output. The sandbox has no network and a read-only filesystem."
        ),
        mcp=[("exec", "heim-mcp-exec")],
        files={"examples/prompts.md": _EXEC_PROMPTS_MD},
        next_steps=(
            "exec runs Python in Docker (no network, read-only fs, capped resources) — Docker must be running.\n"
            "  uv sync && uv run heim chat"
        ),
    ),
    # --- one per voice model (Kokoro speaks replies; the rest are featured per README) ---
    "voice-kokoro": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=_VOICE_ASSISTANT_PROMPT,
        files={"examples/prompts.md": _KOKORO_PROMPTS_MD},
        extras=[],
        chat_voice="kokoro:af_heart",
        next_steps=(
            "Kokoro speaks replies aloud — it's built in (no extra, espeak-ng bundled).\n"
            "  uv sync && uv run heim serve --ui        # enable Listen, or: uv run heim chat"
        ),
    ),
    "voice-whisper": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=_VOICE_ASSISTANT_PROMPT,
        files={"examples/prompts.md": _WHISPER_PROMPTS_MD},
        extras=["voice"],
        chat_voice="kokoro:af_heart",
        next_steps=(
            "A full voice loop: Whisper transcribes your mic (Apple Silicon `voice` extra), Kokoro\n"
            "speaks replies (built in).\n"
            "  uv sync && uv run heim serve --ui        # use the Voice surface"
        ),
    ),
    "voice-dia": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=_VOICE_ASSISTANT_PROMPT,
        files={"examples/prompts.md": _DIA_PROMPTS_MD},
        extras=["voice"],
        chat_voice="kokoro:af_heart",
        next_steps=(
            "Dia (dialogue + voice cloning) is used via `heim voice speak --model dia` (Apple Silicon\n"
            "`voice` extra). Chat replies use Kokoro.\n"
            "  uv sync && uv run heim voice setup      # seed Dia's codec onto the drive (offline-ready)\n"
            "  uv run heim serve --ui"
        ),
    ),
    "voice-qwen3-tts": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=_VOICE_ASSISTANT_PROMPT,
        files={"examples/prompts.md": _QWEN3TTS_PROMPTS_MD},
        extras=["voice"],
        chat_voice="kokoro:af_heart",
        next_steps=(
            "Qwen3-TTS is staged as a Voice-studio model; mlx-audio can't yet synthesize with it (see\n"
            "heim's roadmap). Chat replies use Kokoro in the meantime.\n"
            "  uv sync && uv run heim serve --ui"
        ),
    ),
}
