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
_ORANGE = "\033[38;5;208m"
_GREY = "\033[38;5;244m"
_RESET = "\033[0m"


def _rl(seq: str) -> str:
    return f"\001{seq}\002"


USER_PROMPT = f"{_rl(_ORANGE)}●{_rl(_RESET)} you {_rl(_GREY)}›{_rl(_RESET)} "


def header(
    console: Console,
    *,
    model: str,
    model_format: str,
    tools: list[str],
    server: str | None = None,
    esc_cancel: bool = False,
) -> None:
    """Print the opening chat banner: model, format, tools, and the runtime URL.

    ``server`` is the local runtime's base URL — kodo runs ``llama-server`` /
    ``mlx_lm.server`` in the background and talks to its OpenAI ``/v1``; showing it
    makes the live endpoint discoverable (e.g. to curl during the session).
    ``esc_cancel`` adds the "ESC cancels" hint (only where that's wired up).
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
    hint = "/exit or Ctrl-D to quit  ·  ↑ recalls previous prompts"
    if esc_cancel:
        hint += "  ·  ESC cancels a reply"
    console.print(f"[grey50]{hint}[/]\n")


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
