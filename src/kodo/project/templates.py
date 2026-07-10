"""Project templates for ``kodo project new --template <name>``.

Each template presets the wizard — a default model, system prompt, MCP tools, uv extras, a
spoken-reply voice, and example files — so a purpose-built assistant is reproducible in one
command. Kept out of ``cli.py`` so the growing set (DHIS2, one per bundled MCP, one per voice
model) doesn't bloat the CLI module.
"""

from __future__ import annotations

from kodo.models import ProjectTemplate

# --- example prompt files -------------------------------------------------------------------

_DHIS2_PROMPTS_MD = """\
# Example prompts

Copy these into `uv run kodo chat` (or the web UI). The assistant is bound to a DHIS2-capable
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
DHIS2 (localhost:8080), not a shared demo. Prefix throwaway objects with `KODO_` so they're
easy to find and clean up. Always confirm before deleting.

## Safe reads first
- What DHIS2 version is this, and who am I logged in as?
- How many data elements are there right now?

## Create
- Create a NUMBER aggregate data element named 'KODO_ANC_TEST' (short name 'KODO_ANC') and
  tell me its UID.
- Create an indicator group named 'KODO_TEST_GROUP'.

## Update / rename
- Rename the data element 'KODO_ANC_TEST' to 'KODO_ANC_TEST_v2'.
- Give 'KODO_ANC_TEST_v2' the description 'created by the kodo write assistant'.

## Delete (confirm first)
- Delete the data element 'KODO_ANC_TEST_v2'. Show me it's gone afterwards.
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

- Base64-encode the string "kodo rocks", then decode it back.
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

Notes: it opens a visible browser window (add `--headless` in `.mcp.json` for servers). For
slow/dynamic pages, ask it to "wait for the content to load, then read it".
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
- Search the project for the string "kodo.toml" and show where it appears.

The files server is read-only by default (rooted at the project directory). Set
`KODO_FILES_WRITABLE=1` in `.env` to allow writes, and `KODO_FILES_ROOT` to change the root.
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
the web UI (`uv run kodo serve --ui`) and enable Listen, or use `uv run kodo chat`.

- Introduce yourself in one sentence.
- Tell me a very short joke.
- Read this back to me: "the quick brown fox".

Pick a different Kokoro voice with `chat_voice` in `kodo.toml` (see `uv run kodo voice voices`).
"""

_WHISPER_PROMPTS_MD = """\
# A dictation assistant (listen + speak)

This project is a full voice loop: **dictate** with your mic (transcribed by Whisper STT) and
hear replies spoken back (Kokoro). Open the web Voice surface with `uv run kodo serve --ui`.

- Hold the mic button and say: "what's on my todo list?"
- Dictate a note and ask the assistant to summarize it.

Whisper (`whisper-large-v3-turbo`) is the transcription model; replies are voiced by Kokoro.
"""

_DIA_PROMPTS_MD = """\
# Dia — expressive dialogue + voice cloning (Voice studio)

Dia is a Voice-studio model (dialogue and voice cloning), used via `kodo voice`, not as an
in-chat reply voice. The chat assistant here speaks replies with Kokoro; explore Dia with:

```bash
uv run kodo voice speak --model dia --seed 42 "Kodo can talk. [laughs] Isn't that nice?"
# Clone a voice from a short clip:
uv run kodo voice speak --model dia --ref-audio ref.wav --ref-text "exact transcript" "New line in that voice."
```

Pin `--seed` for a reproducible voice (Dia is otherwise random each run). Run `uv run kodo
voice setup` once so Dia's codec lives on the drive and works offline.
"""

_QWEN3TTS_PROMPTS_MD = """\
# Qwen3-TTS (Voice studio)

Qwen3-TTS is included as a Voice-studio TTS model. Note: mlx-audio does not yet load its
separate speech tokenizer, so `generate` produces no audio today (tracked in kodo's roadmap);
this project stages the model + config so it works once support lands. The chat assistant
speaks replies with Kokoro in the meantime.

```bash
# Once supported:
uv run kodo voice speak --model qwen3-tts "Hello from Qwen3-TTS."
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
            "  uv sync && uv run kodo chat"
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
            ("search", "kodo-mcp-search"),  # bundled with kodo -> no extra pin
            ("fetch", "uvx mcp-server-fetch"),  # pip -> pinned in a uv project
        ],
        files={"examples/prompts.md": _RESEARCH_PROMPTS_MD},
        next_steps=(
            "search uses DuckDuckGo by default (no key). For Brave/Exa set the relevant API key.\n"
            "  uv sync && uv run kodo chat"
        ),
    ),
    "dhis2": ProjectTemplate(
        # Ornith-1.0-9B won the tools-dhis2 benchmark (12/12, fastest, smallest).
        model="deepreinforce-ai/Ornith-1.0-9B-GGUF",
        system_prompt=(
            "You are a DHIS2 assistant for a connected DHIS2 instance. ALWAYS use the dhis2 tools "
            "(the dhis2_cli tool) to look up real data - never answer counts, UIDs, or metadata from "
            "memory. To use a name in analytics or a filter, resolve it to a UID first with a metadata "
            "search or a filtered list. Keep answers concise and state the values you retrieved."
        ),
        mcp=[("dhis2", "env DHIS2_PROFILE=play42 DHIS2_MCP_READONLY=1 uvx dhis2w-mcp-bridge")],
        files={"examples/prompts.md": _DHIS2_PROMPTS_MD, "examples/dhis2-profiles.toml": _DHIS2_PROFILE_EXAMPLE},
        next_steps=(
            "Set up the DHIS2 profile, then run:\n"
            "  mkdir -p .dhis2 && cp examples/dhis2-profiles.toml .dhis2/profiles.toml   # demo: admin/district\n"
            "  uv sync && uv run kodo project show      # confirm the model + dhis2 tools are wired\n"
            "  uv run kodo serve --ui                   # or: uv run kodo chat\n"
            "Point it at your own instance: edit .dhis2/profiles.toml (or `d2w profile add … --local`) "
            "and DHIS2_PROFILE in kodo.toml; drop DHIS2_MCP_READONLY=1 to allow writes."
        ),
    ),
    "dhis2-write": ProjectTemplate(
        # A write-enabled DHIS2 assistant against a LOCAL instance you control (not a shared demo).
        model="deepreinforce-ai/Ornith-1.0-9B-GGUF",
        system_prompt=(
            "You are a DHIS2 assistant that can READ and WRITE metadata on a connected DHIS2 instance "
            "via the dhis2 tools (the dhis2_cli tool). ALWAYS use the tools for real data - never answer "
            "counts, UIDs, or metadata from memory. Resolve any name to its UID before acting on it. "
            "Before creating, updating, or DELETING anything, state exactly what you are about to change; "
            "after a write, confirm the result by reading it back. Prefer NUMBER value types and sensible "
            "defaults when creating. Keep answers concise and report the UIDs and outcomes you got."
        ),
        mcp=[("dhis2", "env DHIS2_PROFILE=local_basic uvx dhis2w-mcp-bridge")],  # no READONLY -> writes enabled
        files={
            "examples/prompts.md": _DHIS2_WRITE_PROMPTS_MD,
            "examples/dhis2-profiles.toml": _DHIS2_LOCAL_PROFILE_EXAMPLE,
        },
        next_steps=(
            "This assistant can MUTATE metadata — point it only at a local/throwaway DHIS2.\n"
            "  mkdir -p .dhis2 && cp examples/dhis2-profiles.toml .dhis2/profiles.toml   # local: admin/district\n"
            "  uv sync && uv run kodo project show\n"
            "  uv run kodo serve --ui                   # or: uv run kodo chat\n"
            "The bridge runs read-write (no DHIS2_MCP_READONLY). Prefix test objects with KODO_ so "
            "they're easy to clean up."
        ),
    ),
    # --- one showcase per bundled MCP server ---
    "mcp-datetime": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=_mcp_prompt("date & time tools (current time, timezones, conversions, arithmetic)"),
        mcp=[("datetime", "kodo-mcp-datetime")],
        files={"examples/prompts.md": _DATETIME_PROMPTS_MD},
        next_steps="  uv sync && uv run kodo chat",
    ),
    "mcp-utils": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=_mcp_prompt("text & encoding utilities (base64, hashes, UUIDs, URL-encoding, random tokens)"),
        mcp=[("utils", "kodo-mcp-utils")],
        files={"examples/prompts.md": _UTILS_PROMPTS_MD},
        next_steps="  uv sync && uv run kodo chat",
    ),
    "mcp-search": ProjectTemplate(
        model="lmstudio-community/gemma-4-12B-it-QAT-GGUF",
        system_prompt=(
            "You are a research assistant with a web search tool. Search for current information "
            "instead of answering from memory, and cite the sources you used. Be clear about uncertainty."
        ),
        mcp=[("search", "kodo-mcp-search")],
        files={"examples/prompts.md": _SEARCH_PROMPTS_MD},
        next_steps=(
            "search uses DuckDuckGo by default (no key). For Brave/Exa set the relevant API key in .env.\n"
            "  uv sync && uv run kodo chat"
        ),
    ),
    "mcp-web": ProjectTemplate(
        model="lmstudio-community/gemma-4-12B-it-QAT-GGUF",
        system_prompt=(
            "You are a web-reading assistant. Use the web tool to fetch and read pages, then answer "
            "from what the page actually says. Quote or cite the parts you relied on."
        ),
        mcp=[("web", "kodo-mcp-web")],
        files={"examples/prompts.md": _WEB_PROMPTS_MD},
        extras=["web"],
        next_steps=(
            "The web reader needs the `web` extra (Playwright + Chromium); `uv sync` installs it and\n"
            "the first run downloads the browser once.\n"
            "  uv sync && uv run playwright install chromium && uv run kodo chat"
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
        mcp=[("playwright", "bunx @playwright/mcp@latest --isolated")],
        files={"examples/prompts.md": _BROWSE_PROMPTS_MD},
        next_steps=(
            "Playwright MCP runs via bunx (needs bun) and downloads a browser on first run.\n"
            "It opens a visible window; add --headless in .mcp.json for servers/automation.\n"
            "  uv run kodo chat"
        ),
    ),
    "mcp-memory": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=(
            "You are an assistant with a persistent memory tool. When the user asks you to remember "
            "something, store it; recall stored notes when relevant; forget on request. Use the tool "
            "rather than relying on the conversation alone."
        ),
        mcp=[("memory", "kodo-mcp-memory")],
        files={"examples/prompts.md": _MEMORY_PROMPTS_MD},
        next_steps="  uv sync && uv run kodo chat",
    ),
    "mcp-weather": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=_mcp_prompt("a weather tool (met.no/yr.no forecasts by place name)"),
        mcp=[("weather", "kodo-mcp-weather-yr")],
        files={"examples/prompts.md": _WEATHER_PROMPTS_MD},
        next_steps="  uv sync && uv run kodo chat",
    ),
    "mcp-files": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=(
            "You are an assistant that can browse, read, and search files under a project directory "
            "using the files tool. Use it to read real file contents instead of guessing. Keep answers concise."
        ),
        mcp=[("files", "kodo-mcp-files")],
        files={"examples/prompts.md": _FILES_PROMPTS_MD},
        next_steps=(
            "Read-only by default, rooted at the project. Set KODO_FILES_WRITABLE=1 / KODO_FILES_ROOT in .env.\n"
            "  uv sync && uv run kodo chat"
        ),
    ),
    "mcp-exec": ProjectTemplate(
        model="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
        system_prompt=(
            "You are a coding assistant that can execute Python in a sandbox via the exec tool. When a "
            "question needs computation, write and run Python rather than doing it in your head, then "
            "report the output. The sandbox has no network and a read-only filesystem."
        ),
        mcp=[("exec", "kodo-mcp-exec")],
        files={"examples/prompts.md": _EXEC_PROMPTS_MD},
        next_steps=(
            "exec runs Python in Docker (no network, read-only fs, capped resources) — Docker must be running.\n"
            "  uv sync && uv run kodo chat"
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
            "  uv sync && uv run kodo serve --ui        # enable Listen, or: uv run kodo chat"
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
            "  uv sync && uv run kodo serve --ui        # use the Voice surface"
        ),
    ),
    "voice-dia": ProjectTemplate(
        model="unsloth/Qwen3.5-4B-GGUF",
        system_prompt=_VOICE_ASSISTANT_PROMPT,
        files={"examples/prompts.md": _DIA_PROMPTS_MD},
        extras=["voice"],
        chat_voice="kokoro:af_heart",
        next_steps=(
            "Dia (dialogue + voice cloning) is used via `kodo voice speak --model dia` (Apple Silicon\n"
            "`voice` extra). Chat replies use Kokoro.\n"
            "  uv sync && uv run kodo voice setup      # seed Dia's codec onto the drive (offline-ready)\n"
            "  uv run kodo serve --ui"
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
            "kodo's roadmap). Chat replies use Kokoro in the meantime.\n"
            "  uv sync && uv run kodo serve --ui"
        ),
    ),
}
