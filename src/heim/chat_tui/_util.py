"""Small helpers + constants for the chat TUI: token formatting, spinner, slash/sampling tables."""


def _fmt_tokens(n: int) -> str:
    """Compact token count: 1234 -> 1.2K, 262144 -> 256K."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return f"{n / 1_000_000:.1f}M".replace(".0M", "M")


# Whimsical "working" words shown while the model thinks (one picked per turn), à la Claude Code.
_GERUNDS = (
    "Percolating",
    "Noodling",
    "Conjuring",
    "Ruminating",
    "Marinating",
    "Cogitating",
    "Simmering",
    "Pondering",
    "Tinkering",
    "Brewing",
    "Musing",
    "Whirring",
    "Moonwalking",
    "Computing",
    "Scheming",
    "Puzzling",
    "Deliberating",
    "Contemplating",
    "Synthesizing",
    "Wrangling",
)

# Braille spinner frames for the "thinking" indicator.
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Accepted aliases -> ModelSampling field, for the `/set <param> <value>` command.
_SAMPLING_FIELDS: dict[str, str] = {
    "temperature": "temperature",
    "temp": "temperature",
    "top_p": "top_p",
    "top-p": "top_p",
    "topp": "top_p",
    "top_k": "top_k",
    "top-k": "top_k",
    "topk": "top_k",
    "min_p": "min_p",
    "min-p": "min_p",
    "minp": "min_p",
    "repeat_penalty": "repeat_penalty",
    "repeat-penalty": "repeat_penalty",
    "penalty": "repeat_penalty",
}

# Slash commands typed in the input, for the in-line autocomplete menu. Each is
# (display, completion, help): the completion is what Tab fills in (a trailing space
# where an argument follows). The palette (Ctrl+P) is generated separately from live app state.
_SLASH_COMMANDS: tuple[tuple[str, str, str], ...] = (
    ("/mcp", "/mcp", "MCP servers + tools (alias /tools)"),
    ("/mcp on <server>", "/mcp on ", "enable a server's tools"),
    ("/mcp off <server>", "/mcp off ", "disable a server's tools"),
    ("/mcp reconnect", "/mcp reconnect", "respawn the MCP servers"),
    ("/copy", "/copy", "copy the last reply"),
    ("/model", "/model ", "switch the running model (or /model to pick)"),
    ("/export", "/export ", "save the transcript to a markdown file"),
    ("/export --thinking", "/export --thinking", "export including each turn's thinking (folded)"),
    ("/set", "/set ", "adjust sampling (temperature/top_p/…) or reasoning (off/low/…/max)"),
    ("/system", "/system ", "show or replace the system prompt (history kept)"),
    ("/system clear", "/system clear", "drop the system prompt"),
    ("/clear", "/clear", "clear the conversation"),
    ("/help", "/help", "commands + keyboard shortcuts"),
    ("/exit", "/exit", "quit"),
)
