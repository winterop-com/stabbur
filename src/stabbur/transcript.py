"""Render a conversation to Markdown — the one transcript format stabbur writes.

Both the Textual TUI's ``/export`` and the CLI's ``stabbur chat --save`` land here, so a saved
transcript reads the same whichever surface produced it. Deliberately free of any UI import
(the CLI must not drag in textual) and of any model/runtime import: it takes plain turns.
"""

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

# Heading per role. Anything else (tool turns, for instance) is skipped: a transcript is the
# conversation a person had, not the machinery underneath it.
_HEADINGS = {"system": "System prompt", "user": "You", "assistant": "Assistant"}


class TranscriptTurn(BaseModel):
    """One rendered turn: its role, its text, and (optionally) the thinking behind it."""

    model_config = ConfigDict(frozen=True)

    role: str
    text: str
    reasoning: str = ""  # included only when the caller asks for thinking


def render_markdown(model_name: str, turns: Iterable[TranscriptTurn], *, thinking: bool = False) -> str:
    """Render ``turns`` as a Markdown transcript titled with ``model_name``.

    Empty turns are dropped. With ``thinking``, an assistant turn's reasoning precedes its
    answer as a collapsed ``<details>`` block, so the transcript stays readable while the
    thinking remains available to expand.
    """
    lines = [f"# Chat — {model_name}", ""]
    for turn in turns:
        text = turn.text.strip()
        heading = _HEADINGS.get(turn.role)
        if not text or heading is None:
            continue
        lines += [f"## {heading}", ""]
        reason = turn.reasoning.strip() if thinking and turn.role == "assistant" else ""
        if reason:
            lines += ["<details>", "<summary>Thinking</summary>", "", reason, "", "</details>", ""]
        lines += [text, ""]
    return "\n".join(lines)
