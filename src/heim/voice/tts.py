"""Text-to-speech via llama.cpp's ``llama-tts`` (OuteTTS + WavTokenizer vocoder).

``llama-tts`` ships with llama.cpp (the same install heim already uses for GGUF
chat). It's a one-shot CLI: given text it writes a WAV, using a small OuteTTS
GGUF plus a vocoder. ``--tts-oute-default`` auto-fetches both into the HF cache
on first use, so no library wiring is needed for a first cut.
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from heim import host

_TTS_BIN = "llama-tts"


def available() -> bool:
    """Whether the ``llama-tts`` binary is on PATH."""
    return shutil.which(_TTS_BIN) is not None


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


def synthesize(
    text: str,
    out_path: Path | None = None,
    model: Path | None = None,
    vocoder: Path | None = None,
) -> Path:
    """Generate a speech WAV from ``text``.

    With ``model`` + ``vocoder`` (a library TTS model and its paired vocoder),
    uses those; otherwise the default OuteTTS models (auto-downloaded on first
    use). Returns the path to the written WAV (a temp file if ``out_path`` is
    omitted).

    Raises:
        RuntimeError: If ``llama-tts`` is missing, the pairing is incomplete, or
            synthesis fails.
    """
    if shutil.which(_TTS_BIN) is None:
        raise RuntimeError(f"{_TTS_BIN!r} not found on PATH. {host.llama_cpp_hint()}")
    if not text.strip():
        raise RuntimeError("nothing to speak (empty text)")
    if model is not None and vocoder is None:
        raise RuntimeError(f"{model.name} has no paired vocoder; can't synthesize")
    if out_path:
        out = out_path
    else:  # NamedTemporaryFile closes its fd on __exit__ (mkstemp leaks it); delete=False keeps the file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            out = Path(f.name)
    if model is not None and vocoder is not None:
        cmd = [_TTS_BIN, "-m", str(model), "-mv", str(vocoder), "-p", text, "-o", str(out)]
    else:
        cmd = [_TTS_BIN, "--tts-oute-default", "-p", text, "-o", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603 - fixed binary, args not shell-interpolated
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError(f"{_TTS_BIN} failed: {(proc.stderr or proc.stdout)[-500:].strip()}")
    return out
