"""Tests for the terminal chat presentation helpers."""

import io

from rich.console import Console

from kodo import chatui


def test_render_reply_renders_markdown() -> None:
    # --render turns a Markdown reply into formatted output: the label, the heading
    # text, and the fenced code content all make it through.
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    chatui.render_reply(console, '# Person\n\n```json\n{"name": "Jane"}\n```')
    out = buf.getvalue()

    assert "kodo" in out  # reply label
    assert "Person" in out  # rendered heading
    assert "Jane" in out  # code block content


def test_header_shows_model_tools_and_server() -> None:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    chatui.header(console, model="pub/Foo", model_format="gguf", tools=["datetime"], server="http://127.0.0.1:49512")
    out = buf.getvalue()

    assert "kodo chat" in out
    assert "pub/Foo" in out
    assert "datetime" in out
    assert "127.0.0.1:49512/v1" in out  # runtime endpoint shown
