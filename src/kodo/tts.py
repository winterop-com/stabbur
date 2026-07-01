"""Text-to-speech via llama.cpp's ``llama-tts`` (OuteTTS + WavTokenizer vocoder).

``llama-tts`` ships with llama.cpp (the same install kodo already uses for GGUF
chat). It's a one-shot CLI: given text it writes a WAV, using a small OuteTTS
GGUF plus a vocoder. ``--tts-oute-default`` auto-fetches both into the HF cache
on first use, so no library wiring is needed for a first cut.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

_TTS_BIN = "llama-tts"


def available() -> bool:
    """Whether the ``llama-tts`` binary is on PATH."""
    return shutil.which(_TTS_BIN) is not None


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
        raise RuntimeError(f"{_TTS_BIN!r} not found on PATH. Install llama.cpp (e.g. `brew install llama.cpp`).")
    if not text.strip():
        raise RuntimeError("nothing to speak (empty text)")
    if model is not None and vocoder is None:
        raise RuntimeError(f"{model.name} has no paired vocoder; can't synthesize")
    out = out_path or Path(tempfile.mkstemp(suffix=".wav")[1])
    if model is not None and vocoder is not None:
        cmd = [_TTS_BIN, "-m", str(model), "-mv", str(vocoder), "-p", text, "-o", str(out)]
    else:
        cmd = [_TTS_BIN, "--tts-oute-default", "-p", text, "-o", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603 - fixed binary, args not shell-interpolated
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError(f"{_TTS_BIN} failed: {(proc.stderr or proc.stdout)[-500:].strip()}")
    return out
