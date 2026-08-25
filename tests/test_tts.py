"""Tests for text-to-speech helpers (Markdown-to-speech cleanup)."""

from heim.voice import tts


def test_speech_text_passes_plain_prose_through() -> None:
    assert tts.speech_text("Hello there, how are you?") == "Hello there, how are you?"


def test_speech_text_drops_fenced_code_blocks() -> None:
    md = "Here you go:\n\n```python\nprint('hi')\n```\n\nThat's it."
    out = tts.speech_text(md)
    assert "print" not in out
    assert "Here you go:" in out
    assert "That's it." in out


def test_speech_text_unwraps_inline_code_and_links() -> None:
    out = tts.speech_text("Run `heim serve` then open [the docs](https://example.com/docs).")
    assert "`" not in out
    assert "heim serve" in out
    assert "the docs" in out
    assert "https://example.com" not in out


def test_speech_text_strips_emphasis_headings_and_bullets() -> None:
    md = "# Title\n\nThis is **bold** and *italic*.\n\n- one\n- two\n\n> a quote"
    out = tts.speech_text(md)
    assert "*" not in out
    assert "#" not in out
    assert "Title" in out
    assert "This is bold and italic." in out
    assert "one" in out and "two" in out
    assert "a quote" in out
    # List/quote markers gone from line starts.
    assert not any(line.lstrip().startswith(("-", ">", "#")) for line in out.splitlines())


def test_speech_text_drops_images_and_tables_pipes() -> None:
    out = tts.speech_text("![alt](img.png)\n\n| a | b |\n| - | - |\n| 1 | 2 |")
    assert "![" not in out
    assert "|" not in out


def test_speech_text_empty_for_code_only() -> None:
    assert tts.speech_text("```\njust code\n```") == ""
