"""The catalog of voice (TTS / STT) models stabbur knows how to run.

Voice models are a distinct category from chat LLMs — their job is audio in/out, not
next-token prediction. This module is a **declarative registry**: each model is one
:class:`VoiceModel` entry describing how to identify it, which backend runs it, and how its
voice is chosen. Adding a new voice model is one entry here (plus a runtime backend adapter
only when its backend is new). Nothing in this module loads weights or imports a runtime.

The key axis is ``voice_mode`` — how a TTS model's voice is determined:

* ``preset`` — pick from named built-in voices.
* ``clone`` — the voice comes from a short reference clip.
* ``design`` — the voice is described in words ("a calm older man"); no clip needed.

Whether a *seed* pins the result is a separate axis (:attr:`VoiceModel.seedable`), not a mode:
a design model samples a fresh speaker per run too, and the same seed reproduces it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class VoiceKind(StrEnum):
    """What a voice model does."""

    tts = "tts"  # text -> audio
    stt = "stt"  # audio -> text (transcription)


class VoiceMode(StrEnum):
    """How a TTS model's voice is selected."""

    preset = "preset"  # choose a named built-in voice
    clone = "clone"  # voice cloned from a reference clip
    design = "design"  # voice described in natural language ("a bright young woman")
    none = "none"  # not applicable (e.g. STT)


class Backend(StrEnum):
    """The external runtime that executes a voice model."""

    kokoro_onnx = "kokoro-onnx"  # cross-platform ONNX; stabbur's built-in Kokoro path
    mlx_audio = "mlx-audio"  # Apple Silicon; the mlx-audio TTS/STT runtime


class VoiceModel(BaseModel):
    """A voice model stabbur can run: how to find it, run it, and drive its voice."""

    model_config = ConfigDict(frozen=True)

    id: str  # stabbur's short id, e.g. "kokoro", "whisper"
    display_name: str
    repo: str  # the Hugging Face repo stabbur prefers (MLX variant on Apple Silicon)
    kind: VoiceKind
    backend: Backend
    description: str = ""

    # --- TTS voicing (ignored for STT) ---
    voice_mode: VoiceMode = VoiceMode.none
    cloneable: bool = False  # accepts a reference clip to clone a voice
    multi_speaker: bool = False  # dialogue with speaker tags ([S1]/[S2])
    voices: list[str] = Field(default_factory=list)  # named presets, if statically known
    # Whether pinning a seed reproduces the output — a separate axis from ``voice_mode``, since a
    # voice-design model samples a fresh voice per run and MLX's RNG pins it just as well. Tie the
    # seed control to this flag, never to the mode.
    seedable: bool = False
    # Whether the model actually renders at a requested speed. mlx-audio forwards ``speed`` to
    # every model and the ones that don't implement it swallow it silently, so a slider tied to
    # nothing looks broken rather than absent — measured, not assumed (VoxCPM2 ignores it).
    honors_speed: bool = True

    languages: list[str] = Field(default_factory=list)  # BCP-47-ish; empty = unspecified
    sample_rate: int = 24000
    size_hint: str = ""  # rough on-disk size, informational

    # A lightweight model suitable as the in-chat "speak replies" voice — small enough to
    # run alongside a large chat LLM without a second multi-GB load.
    chat_default: bool = False

    # Whether stabbur can actually run this model today. False for models present in the
    # registry (so they're documented/listed) but not yet runnable via our runtime — e.g.
    # a model needing bespoke loading the runtime doesn't do. Clients disable synthesis.
    supported: bool = True


# Registry of known voice models. Extend by adding an entry (accurate metadata; conservative
# where a detail is unverified). Discovery (catalog.py) reports which of these are present.
BUILTIN: tuple[VoiceModel, ...] = (
    VoiceModel(
        id="kokoro",
        display_name="Kokoro-82M",
        repo="mlx-community/Kokoro-82M-bf16",
        kind=VoiceKind.tts,
        backend=Backend.kokoro_onnx,
        description="Small, fast multi-voice TTS with 54 built-in named voices. stabbur's default "
        "in-chat voice — tiny enough to run alongside a large LLM.",
        voice_mode=VoiceMode.preset,
        languages=["en", "es", "fr", "hi", "it", "ja", "pt", "zh"],
        sample_rate=24000,
        size_hint="~310 MB",
        chat_default=True,
    ),
    VoiceModel(
        id="whisper",
        display_name="Whisper large-v3-turbo",
        repo="mlx-community/whisper-large-v3-turbo-asr-fp16",
        kind=VoiceKind.stt,
        backend=Backend.mlx_audio,
        description="Fast multilingual speech-to-text — the voice-input side (mic -> prompt).",
        voice_mode=VoiceMode.none,
        size_hint="~1.6 GB",
    ),
    VoiceModel(
        id="parakeet",
        display_name="Parakeet TDT 0.6B v3",
        repo="mlx-community/parakeet-tdt-0.6b-v3",
        kind=VoiceKind.stt,
        backend=Backend.mlx_audio,
        description="Fast, accurate speech-to-text (NVIDIA Parakeet); English + 25 European languages. "
        "A lighter, quicker alternative to Whisper.",
        voice_mode=VoiceMode.none,
        size_hint="~1.2 GB",
    ),
    VoiceModel(
        id="qwen3-asr",
        display_name="Qwen3-ASR-1.7B",
        repo="mlx-community/Qwen3-ASR-1.7B-8bit",
        kind=VoiceKind.stt,
        backend=Backend.mlx_audio,
        description="Multilingual speech-to-text from Alibaba.",
        voice_mode=VoiceMode.none,
        size_hint="~1.8 GB",
    ),
    VoiceModel(
        id="distil-whisper",
        display_name="Distil-Whisper large-v3",
        repo="distil-whisper/distil-large-v3",
        kind=VoiceKind.stt,
        backend=Backend.mlx_audio,
        description="A faster distilled Whisper (English) — quicker transcription than the full model.",
        voice_mode=VoiceMode.none,
        languages=["en"],
        size_hint="~1.5 GB",
    ),
    VoiceModel(
        id="voxcpm2",
        display_name="VoxCPM2",
        repo="mlx-community/VoxCPM2-8bit",
        kind=VoiceKind.tts,
        backend=Backend.mlx_audio,
        description="Studio-quality 48 kHz multilingual TTS (30 languages) with voice design: "
        "describe the voice you want in words, or clone one from a reference clip.",
        voice_mode=VoiceMode.design,
        cloneable=True,
        seedable=True,  # a pinned seed reproduces a designed voice byte-for-byte
        honors_speed=False,  # it renders at its own pace; a speed request changes nothing
        # 30 languages upstream; only these are named in the model card, so only these are
        # claimed here (a listing that guesses is worse than one that is short).
        languages=["en", "zh", "id", "ja", "ko"],
        sample_rate=48000,
        size_hint="~3.2 GB",
    ),
)

_BY_ID = {m.id: m for m in BUILTIN}
_BY_REPO = {m.repo: m for m in BUILTIN}


def get(model_id: str) -> VoiceModel | None:
    """Look up a known voice model by stabbur id."""
    return _BY_ID.get(model_id)


def by_repo(repo: str) -> VoiceModel | None:
    """Look up a known voice model by Hugging Face repo id."""
    return _BY_REPO.get(repo)


def chat_voice() -> VoiceModel:
    """The default lightweight voice for in-chat 'speak replies' (Kokoro)."""
    return next(m for m in BUILTIN if m.chat_default)
