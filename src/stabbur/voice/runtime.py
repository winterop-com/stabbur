"""Serve voice models via mlx-audio, in-process (Apple Silicon): synth + transcribe.

Calls mlx-audio directly rather than spawning its FastAPI server — that server drags a chain
of VAD/realtime deps (webrtcvad, pkg_resources, …) we don't need, while ``mlx_audio.tts`` /
``mlx_audio.stt`` work with just ``misaki[en]``. stabbur's own serve layer exposes the OpenAI
``/v1/audio/*`` routes on top of these functions. Models load off the library path (not a
fresh HF download) and are cached. Apple-Silicon only (the ``voice``/``mlx`` extras); on
Linux, Kokoro-ONNX (:mod:`stabbur.kokoro`) covers TTS. Cloning uses ``ref_audio`` + ``ref_text``.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import logging
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
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


@contextmanager
def _quiet() -> Generator[None]:
    """Swallow the third-party chatter mlx-audio and transformers emit around a synthesis.

    Two sources, neither of them ours and neither switchable:

    * mlx-audio prints a ``Text:/Voice:/Speed:/Language:`` banner from ``generate_audio``
      unconditionally — not under its ``verbose`` flag — and it reports the *default* voice
      (``af_heart``) even for a model that has no named voices, so it is misleading as well as
      noisy: a `stabbur serve` log claimed Kokoro's voice while VoxCPM2 was speaking.
    * transformers warns "You are using a model of type ``voxcpm2`` to instantiate a model of
      type ``" whenever a checkpoint's architecture isn't one of its own. That one goes to
      *stderr* through the library's own logging setup, so it is silenced through its API —
      ``logging.getLogger("transformers")`` does not reach it.

    ``redirect_stdout`` is safe here even inside a server: uvicorn's and Rich's handlers bind to
    the real ``sys.stdout`` when they are constructed, so only ``print`` calls that resolve it at
    call time — mlx-audio's — are captured. The captured text goes to the debug log rather than
    being dropped.
    """
    buffer = io.StringIO()
    # Imported here, not at module scope: transformers is a heavy import that only the mlx-audio
    # path pulls in anyway, and it must be set *before* the model load that emits the warning.
    # It is absent entirely off Apple Silicon, where this context manager still has to work.
    tf_logging: Any = None
    with contextlib.suppress(ImportError):
        from transformers.utils import logging as tf_logging  # noqa: PLC0415

    previous = None if tf_logging is None else tf_logging.get_verbosity()
    if tf_logging is not None:
        tf_logging.set_verbosity_error()
    try:
        with contextlib.redirect_stdout(buffer):
            yield
    finally:
        if tf_logging is not None:
            tf_logging.set_verbosity(previous)
        captured = buffer.getvalue().strip()
        if captured:
            logging.getLogger(__name__).debug("mlx-audio: %s", captured)


@lru_cache(maxsize=4)
def _load(model_path: str) -> Any:
    """Load (and cache) an mlx-audio model from a local path."""
    from mlx_audio.tts.generate import load_model  # noqa: PLC0415

    with _quiet():
        return load_model(Path(model_path))


def synthesize(
    model: Path | str,
    text: str,
    *,
    voice: str | None = None,
    ref_audio: Path | str | None = None,
    ref_text: str | None = None,
    audio_format: str = "wav",
    **params: float | int | str,
) -> bytes:
    """Synthesize ``text`` to audio bytes.

    Give ``voice`` for a preset model (Kokoro) or ``ref_audio`` + ``ref_text`` to clone a
    voice (a cloneable model). ``model`` is a library path so mlx-audio loads it off the drive.
    """
    if not available():
        raise RuntimeError("mlx-audio not installed — run `uv sync --extra voice` (Apple Silicon).")
    from mlx_audio.tts.generate import generate_audio  # noqa: PLC0415

    # Some models shape their voice with parameters instead of named presets; map the common
    # "female"/"male" voice names onto the gender parameter such models expect. This runs
    # BEFORE the prompt fallback below, which would otherwise consume the name and silently
    # drop the requested gender on a model that ships both prompts/ and a gender parameter.
    if voice in ("female", "male"):
        params["gender"] = voice
        voice = None

    # A cloneable model with no reference clip: fall back to a prompt clip bundled with the
    # model itself (a ``prompts/*.wav`` dir in its repo). Naming a prompt's stem as ``voice``
    # picks it; otherwise the first is used. This keeps such models fully local — without it
    # the runtime downloads its default prompt from an upstream repo that may be gated (401).
    model_dir = Path(model) if Path(model).is_dir() else Path(model).parent
    if ref_audio is None:
        prompt_dir = model_dir / "prompts"
        prompts = sorted(prompt_dir.glob("*.wav")) if prompt_dir.is_dir() else []
        if prompts:
            picked = {p.stem: p for p in prompts}.get(voice or "", prompts[0])
            ref_audio = picked
            transcript = picked.with_suffix(".txt")
            if ref_text is None and transcript.is_file():
                ref_text = transcript.read_text().strip()
            voice = None  # consumed as a prompt pick, not an engine voice name

    loaded = _load(str(model))
    # mlx-audio's generate_audio has no `seed` arg (it silently ignores one), so a seed only
    # takes effect by seeding MLX's RNG here — that's what makes a stochastic model
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
    with tempfile.TemporaryDirectory() as tmp, _quiet():
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
    with tempfile.TemporaryDirectory() as tmp, _quiet():
        result = generate_transcription(
            model=str(model), audio=str(audio), output_path=str(Path(tmp) / "t"), format="txt", verbose=False, **kwargs
        )
    return str(getattr(result, "text", result)).strip()
