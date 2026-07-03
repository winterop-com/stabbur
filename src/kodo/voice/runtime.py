"""Serve voice models via mlx-audio, in-process (Apple Silicon): synth + transcribe.

Calls mlx-audio directly rather than spawning its FastAPI server — that server drags a chain
of VAD/realtime deps (webrtcvad, pkg_resources, …) we don't need, while ``mlx_audio.tts`` /
``mlx_audio.stt`` work with just ``misaki[en]``. kodo's own serve layer exposes the OpenAI
``/v1/audio/*`` routes on top of these functions. Models load off the library path (not a
fresh HF download) and are cached. Apple-Silicon only (the ``voice``/``mlx`` extras); on
Linux, Kokoro-ONNX (:mod:`kodo.kokoro`) covers TTS. Cloning uses ``ref_audio`` + ``ref_text``.
"""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any


def available() -> bool:
    """Whether the mlx-audio runtime is importable (Apple-Silicon ``voice`` extra installed)."""
    try:
        import mlx_audio.tts.generate  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


@lru_cache(maxsize=4)
def _load(model_path: str) -> Any:
    """Load (and cache) an mlx-audio model from a local path."""
    from mlx_audio.tts.generate import load_model  # noqa: PLC0415

    return load_model(model_path)


def synthesize(
    model: Path | str,
    text: str,
    *,
    voice: str | None = None,
    ref_audio: Path | str | None = None,
    ref_text: str | None = None,
    audio_format: str = "wav",
    **params: float | int,
) -> bytes:
    """Synthesize ``text`` to audio bytes.

    Give ``voice`` for a preset model (Kokoro) or ``ref_audio`` + ``ref_text`` to clone a
    voice (Dia). ``model`` is a library path so mlx-audio loads it off the drive.
    """
    if not available():
        raise RuntimeError("mlx-audio not installed — run `uv sync --extra voice` (Apple Silicon).")
    from mlx_audio.tts.generate import generate_audio  # noqa: PLC0415

    loaded = _load(str(model))
    kwargs: dict[str, Any] = {"file_prefix": "out", "audio_format": audio_format, "save": True, "verbose": False}
    if voice is not None:
        kwargs["voice"] = voice
    if ref_audio is not None:
        kwargs["ref_audio"] = str(ref_audio)
        kwargs["ref_text"] = ref_text or ""
    kwargs.update(params)
    with tempfile.TemporaryDirectory() as tmp:
        generate_audio(text=text, model=loaded, output_path=tmp, **kwargs)
        out = sorted(Path(tmp).glob(f"out*.{audio_format}"))
        return out[0].read_bytes() if out else b""


def transcribe(model: Path | str, audio: Path | str, *, language: str | None = None) -> str:
    """Transcribe an audio file to text with a Whisper model (a library path)."""
    if not available():
        raise RuntimeError("mlx-audio not installed — run `uv sync --extra voice` (Apple Silicon).")
    from mlx_audio.stt.generate import generate as stt_generate  # noqa: PLC0415

    result = stt_generate(model=str(model), audio=str(audio), language=language, verbose=False)
    text = getattr(result, "text", result)
    return str(text).strip()
