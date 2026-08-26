"""Tests for the scripted -p chat presentation helpers."""

import io

from rich.console import Console

from stabbur import chatui


def test_assistant_prefix_prints_label() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    chatui.assistant_prefix(console, inline=False)
    assert "stabbur" in buf.getvalue()  # reply label


def test_thinking_returns_progress() -> None:
    # The spinner is a Progress the caller drives with start()/stop().
    from rich.progress import Progress

    console = Console(file=io.StringIO(), force_terminal=False, width=80)
    assert isinstance(chatui.thinking(console), Progress)
