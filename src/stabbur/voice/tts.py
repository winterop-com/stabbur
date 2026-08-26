"""Speech-text preparation shared by the TTS engines.

Assistant replies are Markdown; read verbatim, a TTS model would speak the
syntax ("asterisk asterisk", backticks, raw URLs) and recite whole code blocks.
:func:`speech_text` reduces a reply to the prose worth hearing.
"""

import re

# Markdown-to-speech cleanup. Assistant replies are Markdown; read verbatim, a
# TTS model would speak the syntax ("asterisk asterisk", backticks, raw URLs) and
# recite whole code blocks. These reduce a reply to the prose worth hearing.
_FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)  # drop fenced code entirely
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")  # images: drop
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")  # links: keep the text
_INLINE_CODE = re.compile(r"`([^`]+)`")  # inline code: keep the word
_HTML_TAG = re.compile(r"<[^>\n]+>")  # stray HTML/details tags
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)  # heading markers
_BLOCKQUOTE = re.compile(r"^\s{0,3}>\s?", re.MULTILINE)  # quote markers
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)  # bullets/numbers
_EMPHASIS = re.compile(r"(\*\*|\*|__|~~)")  # bold/italic/strike markers
_HRULE = re.compile(r"^\s*([-*_])\1{2,}\s*$", re.MULTILINE)  # --- *** ___
_EXTRA_SPACE = re.compile(r"[ \t]{2,}")
_EXTRA_NEWLINE = re.compile(r"\n{3,}")


def speech_text(raw: str) -> str:
    """Reduce Markdown to plain prose suitable for speech synthesis.

    Drops fenced code blocks and images, unwraps links/inline code to their text,
    and strips heading/list/quote/emphasis markers so the model reads words, not
    formatting. Returns the cleaned text (may be empty if the input was only code
    or markup).
    """
    text = _FENCED_CODE.sub(" ", raw)
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _HTML_TAG.sub("", text)
    text = _HRULE.sub("", text)
    text = _HEADING.sub("", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _LIST_MARKER.sub("", text)
    text = _EMPHASIS.sub("", text)
    text = text.replace("|", " ")  # table pipes read as noise
    text = _EXTRA_SPACE.sub(" ", text)
    text = _EXTRA_NEWLINE.sub("\n\n", text)
    return text.strip()
