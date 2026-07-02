"""Shared presentation for the terminal chat REPL (rich-styled).

Kept deliberately light: a framed header, a colored input prompt, styled reply
labels, and a "thinking" spinner. The streamed reply itself stays plain stdout
(reliable + scriptable); this module only dresses the frame around it. Textual
was dropped for this project — the browser UI is the rich surface; the terminal
REPL just needs a clean, legible face.
"""

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.text import Text

# readline-safe colored input prompt: non-printing ANSI is wrapped in \001..\002
# so readline computes the cursor column correctly (arrow keys / history stay sane).
_GREY = "\033[38;5;244m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _rl(seq: str) -> str:
    return f"\001{seq}\002"


# A single bold arrow (no "● you ›") — Claude-Code style. The turn's context/model
# summary is shown just above it by ``status_line``.
USER_PROMPT = f"{_rl(_BOLD)}❯{_rl(_RESET)} "


def _fmt_tokens(n: int) -> str:
    """Compact token count: 1234 → 1.2K, 262144 → 256K."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return f"{n / 1_000_000:.1f}M".replace(".0M", "M")


def status_line(
    console: Console,
    *,
    model: str,
    model_format: str,
    ctx_used: int | None,
    ctx_max: int | None,
    tools: int,
) -> None:
    """Print the two-line status footer above the prompt (Claude-Code style).

    Line 1 is live state — the active model plus context usage (server-reported
    token total for the last turn; ``None`` before the first reply), the fraction
    turning amber/red as the window fills. Line 2 is the runtime "mode" marker plus
    the key hints, so a long chat always shows where it stands and how to drive it.
    """
    bar = Text()
    bar.append(model, style="grey62")
    if ctx_max:
        used = ctx_used or 0
        pct = used / ctx_max
        color = "green" if pct < 0.5 else "yellow" if pct < 0.85 else "red"
        # Used is a live token count (decimal K); the window is the model's trained
        # size, conventionally a power of two (262144 → 256K), so round it binary.
        window = f"{round(ctx_max / 1024)}K" if ctx_max >= 1024 else str(ctx_max)
        bar.append("  ·  ", style="grey37")
        bar.append(f"{_fmt_tokens(used)}/{window}", style=color)
        bar.append(f" ({pct * 100:.0f}%)", style="grey50")
    if tools:
        bar.append("  ·  ", style="grey37")
        bar.append(f"{tools} tool{'s' if tools != 1 else ''}", style="cyan")
    console.print(bar)

    hint = Text()
    hint.append("▸ ", style="cyan")
    hint.append(model_format, style="grey50")
    hint.append("   ↑ history  ·  ESC cancels  ·  /exit to quit", style="grey42")
    console.print(hint)


def header(
    console: Console,
    *,
    model: str,
    model_format: str,
    tools: list[str],
    server: str | None = None,
) -> None:
    """Print the opening chat banner: model, format, tools, and the runtime URL.

    ``server`` is the local runtime's base URL — kodo runs ``llama-server`` /
    ``mlx_lm.server`` in the background and talks to its OpenAI ``/v1``; showing it
    makes the live endpoint discoverable (e.g. to curl during the session). The
    per-turn key hints live in ``status_line`` instead of here.
    """
    body = Text()
    body.append(model, style="bold")
    body.append(f"  ·  {model_format}", style="grey62")
    body.append("\ntools   ", style="grey62")
    body.append(", ".join(tools) if tools else "none", style="cyan" if tools else "grey62")
    if server is not None:
        # Label it as the OpenAI API base, not a web page — a browser GET on /v1
        # itself 404s (it's a prefix); GET /v1/models is the browsable check.
        body.append("\napi     ", style="grey62")
        body.append(f"{server}/v1", style="grey62")
        body.append("  (OpenAI-compatible)", style="grey50")
    console.print(
        Panel(body, title="[bold]kodo chat[/]", title_align="left", border_style="grey37", padding=(0, 1), expand=False)
    )
    console.print()


def assistant_prefix(console: Console, *, inline: bool) -> None:
    """Print the assistant reply label; ``inline`` keeps the cursor on the line."""
    console.print("[bold cyan]kodo[/] [grey62]›[/] ", end="" if inline else "\n")


def thinking(console: Console) -> Progress:
    """A spinner + elapsed timer shown while the model works.

    Elapsed time makes a slow reply (a big model, or a buffered tool-mode
    response) visibly *working* rather than frozen. Call ``.start()`` / ``.stop()``.
    """
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[grey62]thinking …[/]"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    progress.add_task("", total=None)
    return progress


def render_reply(console: Console, text: str) -> None:
    """Print the reply label + the reply rendered as Markdown (--render mode).

    Trades live token streaming for formatted output: headers, lists, and
    syntax-highlighted fenced code blocks.
    """
    assistant_prefix(console, inline=False)
    console.print(Markdown(text))
