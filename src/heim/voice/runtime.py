"""Serve voice models via mlx-audio, in-process (Apple Silicon): synth + transcribe.

Calls mlx-audio directly rather than spawning its FastAPI server — that server drags a chain
of VAD/realtime deps (webrtcvad, pkg_resources, …) we don't need, while ``mlx_audio.tts`` /
``mlx_audio.stt`` work with just ``misaki[en]``. heim's own serve layer exposes the OpenAI
``/v1/audio/*`` routes on top of these functions. Models load off the library path (not a
fresh HF download) and are cached. Apple-Silicon only (the ``voice``/``mlx`` extras); on
Linux, Kokoro-ONNX (:mod:`heim.kokoro`) covers TTS. Cloning uses ``ref_audio`` + ``ref_text``.
"""

from __future__ import annotations

import importlib.util
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any


def available() -> bool:
    """Whether the mlx-audio runtime is importable (Apple-Silicon ``voice`` extra installed).

    ``find_spec`` on a dotted name imports the parent package, so it *raises* ModuleNotFoundError
    when ``mlx_audio`` is absent (Linux / no ``voice`` extra) — catch it so voice gates degrade to
    a 503/install-hint instead of a 500/traceback.
    """
    try:
        return importlib.util.find_spec("mlx_audio.tts.generate") is not None
    except ModuleNotFoundError:
        return False


@lru_cache(maxsize=4)
def _load(model_path: str) -> Any:
    """Load (and cache) an mlx-audio model from a local path."""
    from mlx_audio.tts.generate import load_model  # noqa: PLC0415

    return load_model(Path(model_path))


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
    # mlx-audio's generate_audio has no `seed` arg (it silently ignores one), so a seed only
    # takes effect by seeding MLX's RNG here — that's what makes a seeded model (Dia)
    # reproducible instead of a fresh random voice every run.
    seed = params.pop("seed", None)
    if seed is not None:
        import mlx.core as mx  # noqa: PLC0415

        mx.random.seed(int(seed))
    # join_audio=True merges the per-segment files mlx-audio writes (one per newline) into a single
    # out.<fmt>; without it we'd read only out_000 and silently truncate multi-line text to line 1.
    kwargs: dict[str, Any] = {
        "file_prefix": "out",
        "audio_format": audio_format,
        "save": True,
        "verbose": False,
        "join_audio": True,
    }
    if voice is not None:
        kwargs["voice"] = voice
    if ref_audio is not None:
        kwargs["ref_audio"] = str(ref_audio)
        kwargs["ref_text"] = ref_text or ""
    kwargs.update(params)
    with tempfile.TemporaryDirectory() as tmp:
        generate_audio(text=text, model=loaded, output_path=tmp, **kwargs)
        out = sorted(Path(tmp).glob(f"out*.{audio_format}"))
        if not out:
            # mlx-audio swallows some model errors (prints + returns nothing) rather than
            # raising — e.g. a model whose speech tokenizer/codec didn't load. Surface it
            # as a real failure so callers show an error instead of silent empty audio.
            raise RuntimeError(
                "mlx-audio produced no audio for this model — it may not be supported "
                "(a model needing a speech tokenizer or codec that didn't load)."
            )
        return out[0].read_bytes()


def transcribe(model: Path | str, audio: Path | str, *, language: str | None = None) -> str:
    """Transcribe an audio file to text with a Whisper model (a library path)."""
    if not available():
        raise RuntimeError("mlx-audio not installed — run `uv sync --extra voice` (Apple Silicon).")
    from mlx_audio.stt.generate import generate_transcription  # noqa: PLC0415

    kwargs: dict[str, Any] = {"language": language} if language else {}
    with tempfile.TemporaryDirectory() as tmp:
        result = generate_transcription(
            model=str(model), audio=str(audio), output_path=str(Path(tmp) / "t"), format="txt", verbose=False, **kwargs
        )
    return str(getattr(result, "text", result)).strip()
