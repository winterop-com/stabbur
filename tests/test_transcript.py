"""Tests for the shared Markdown transcript renderer."""

from stabbur import transcript


def _turn(role: str, text: str, reasoning: str = "") -> transcript.TranscriptTurn:
    return transcript.TranscriptTurn(role=role, text=text, reasoning=reasoning)


def test_renders_the_conversation_with_role_headings() -> None:
    out = transcript.render_markdown(
        "pub/Model",
        [_turn("system", "Be terse."), _turn("user", "hi"), _turn("assistant", "hello")],
    )
    assert out.startswith("# Chat — pub/Model")
    assert "## System prompt" in out and "Be terse." in out
    assert "## You" in out and "## Assistant" in out
    assert out.index("## You") < out.index("## Assistant")  # conversation order preserved


def test_skips_empty_turns_and_non_conversation_roles() -> None:
    # A tool turn is machinery, not conversation; a blank turn is nothing to record.
    out = transcript.render_markdown(
        "m",
        [_turn("user", "  "), _turn("tool", "tool output"), _turn("assistant", "kept")],
    )
    assert "tool output" not in out
    assert "## You" not in out
    assert "kept" in out


def test_thinking_is_opt_in_and_folded() -> None:
    turns = [_turn("user", "q"), _turn("assistant", "a", reasoning="secret thoughts")]

    plain = transcript.render_markdown("m", turns)
    assert "secret thoughts" not in plain  # default export stays lean

    with_thinking = transcript.render_markdown("m", turns, thinking=True)
    assert "secret thoughts" in with_thinking
    assert "<details>" in with_thinking and "<summary>Thinking</summary>" in with_thinking
    # Folded *before* the answer, so the transcript still reads top-to-bottom.
    assert with_thinking.index("secret thoughts") < with_thinking.index("\na\n")


def test_reasoning_on_a_user_turn_is_never_rendered() -> None:
    # Only an assistant turn has thinking; anything attached elsewhere is a caller mistake
    # and must not leak into the file.
    out = transcript.render_markdown("m", [_turn("user", "q", reasoning="not mine")], thinking=True)
    assert "not mine" not in out
