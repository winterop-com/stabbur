"""Presentation helpers for the scripted ``kodo chat -p`` path.

Interactive chat now runs in the full-screen Textual app (:mod:`kodo.chat_tui`);
this module is just the small dressing the one-shot ``-p`` flow still needs: a
reply label and a "thinking" spinner around output that otherwise streams to
plain stdout (reliable + scriptable).
"""

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn


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
