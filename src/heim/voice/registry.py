"""The catalog of voice (TTS / STT) models heim knows how to run.

Voice models are a distinct category from chat LLMs — their job is audio in/out, not
next-token prediction. This module is a **declarative registry**: each model is one
:class:`VoiceModel` entry describing how to identify it, which backend runs it, and how its
voice is chosen. Adding a new voice model is one entry here (plus a runtime backend adapter
only when its backend is new). Nothing in this module loads weights or imports a runtime.

The key axis is ``voice_mode`` — how a TTS model's voice is determined:

* ``preset`` — pick from named built-in voices (Kokoro's 54, OuteTTS speakers).
* ``clone`` — the voice comes from a short reference clip (Dia's audio prompt).
* ``seeded`` — no fixed voice; a fresh one each run unless a seed is pinned (Dia with no
  reference clip). This is why "Dia sounds different every time".
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
    seeded = "seeded"  # random voice per run; a seed makes it reproducible
    none = "none"  # not applicable (e.g. STT)


class Backend(StrEnum):
    """The external runtime that executes a voice model."""

    kokoro_onnx = "kokoro-onnx"  # cross-platform ONNX; heim's built-in Kokoro path
    mlx_audio = "mlx-audio"  # Apple Silicon; serves Dia / Kokoro / Qwen3-TTS / Whisper
    llama_tts = "llama-tts"  # llama.cpp TTS (a GGUF TTS model + a vocoder)


class VoiceModel(BaseModel):
    """A voice model heim can run: how to find it, run it, and drive its voice."""

    model_config = ConfigDict(frozen=True)

    id: str  # heim's short id, e.g. "kokoro", "dia", "whisper"
    display_name: str
    repo: str  # the Hugging Face repo heim prefers (MLX variant on Apple Silicon)
    kind: VoiceKind
    backend: Backend
    description: str = ""

    # --- TTS voicing (ignored for STT) ---
    voice_mode: VoiceMode = VoiceMode.none
    cloneable: bool = False  # accepts a reference clip to clone a voice
    multi_speaker: bool = False  # dialogue with speaker tags (Dia's [S1]/[S2])
    voices: list[str] = Field(default_factory=list)  # named presets, if statically known

    languages: list[str] = Field(default_factory=list)  # BCP-47-ish; empty = unspecified
    sample_rate: int = 24000
    size_hint: str = ""  # rough on-disk size, informational

    # A lightweight model suitable as the in-chat "speak replies" voice — small enough to
    # run alongside a large chat LLM without a second multi-GB load.
    chat_default: bool = False

    # Whether heim can actually run this model today. False for models present in the
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
        description="Small, fast multi-voice TTS with 54 built-in named voices. heim's default "
        "in-chat voice — tiny enough to run alongside a large LLM.",
        voice_mode=VoiceMode.preset,
        languages=["en", "es", "fr", "hi", "it", "ja", "pt", "zh"],
        sample_rate=24000,
        size_hint="~310 MB",
        chat_default=True,
    ),
    VoiceModel(
        id="dia",
        display_name="Dia-1.6B",
        repo="mlx-community/Dia-1.6B",
        kind=VoiceKind.tts,
        backend=Backend.mlx_audio,
        description="Expressive TTS with nonverbal cues (laughter, coughs) and voice cloning "
        "from a reference clip. Its voice is random each run — pin a seed for a repeatable, "
        "reliable result (an unlucky seed can drone instead of speak).",
        voice_mode=VoiceMode.seeded,
        cloneable=True,
        multi_speaker=True,
        languages=["en"],
        sample_rate=44100,
        size_hint="~6 GB",
    ),
    VoiceModel(
        id="qwen3-tts",
        display_name="Qwen3-TTS-0.6B",
        repo="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16",
        kind=VoiceKind.tts,
        backend=Backend.mlx_audio,
        description="Compact multilingual TTS. Not yet runnable in heim: mlx-audio's simple "
        "loader doesn't wire up its separate speech tokenizer, so synthesis is disabled for now.",
        voice_mode=VoiceMode.preset,
        sample_rate=24000,
        size_hint="~1.2 GB",
        supported=False,
    ),
    VoiceModel(
        id="outetts",
        display_name="OuteTTS-0.2-500M",
        repo="OuteAI/OuteTTS-0.2-500M-GGUF",
        kind=VoiceKind.tts,
        backend=Backend.llama_tts,
        description="GGUF TTS run via llama.cpp with a vocoder; speaker-conditioned voices.",
        voice_mode=VoiceMode.preset,
        languages=["en", "zh", "ja", "ko"],
        sample_rate=24000,
        size_hint="~500 MB",
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
    # --- validated against mlx-audio 2026-07-04 (Apple Silicon) ---
    VoiceModel(
        id="soprano",
        display_name="Soprano-1.1-80M",
        repo="mlx-community/Soprano-1.1-80M-bf16",
        kind=VoiceKind.tts,
        backend=Backend.mlx_audio,
        description="Tiny (80M) high-quality English TTS, a Kokoro-family model with named voices — "
        "a lightweight alternative to Kokoro.",
        voice_mode=VoiceMode.preset,
        languages=["en"],
        sample_rate=24000,
        size_hint="~160 MB",
    ),
    VoiceModel(
        id="chatterbox",
        display_name="Chatterbox",
        repo="mlx-community/chatterbox-fp16",
        kind=VoiceKind.tts,
        backend=Backend.mlx_audio,
        description="Expressive TTS with an emotion/exaggeration control and voice cloning — the "
        "native-MLX path to emotion-controllable speech.",
        voice_mode=VoiceMode.preset,
        cloneable=True,
        sample_rate=24000,
        size_hint="~1 GB",
    ),
    VoiceModel(
        id="spark",
        display_name="Spark-TTS-0.5B",
        repo="mlx-community/Spark-TTS-0.5B-bf16",
        kind=VoiceKind.tts,
        backend=Backend.mlx_audio,
        description="Bilingual (English + Chinese) TTS. Needs the `soxr` resampler (in the voice extra).",
        voice_mode=VoiceMode.preset,
        languages=["en", "zh"],
        sample_rate=24000,
        size_hint="~1 GB",
    ),
    VoiceModel(
        id="csm",
        display_name="CSM-1B",
        repo="mlx-community/csm-1b",
        kind=VoiceKind.tts,
        backend=Backend.mlx_audio,
        description="Conversational TTS with voice cloning from a reference clip (Sesame CSM) — give a "
        "reference clip + its transcript to set the voice.",
        voice_mode=VoiceMode.clone,
        cloneable=True,
        languages=["en"],
        sample_rate=24000,
        size_hint="~2 GB",
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
)

_BY_ID = {m.id: m for m in BUILTIN}
_BY_REPO = {m.repo: m for m in BUILTIN}


def get(model_id: str) -> VoiceModel | None:
    """Look up a known voice model by heim id."""
    return _BY_ID.get(model_id)


def by_repo(repo: str) -> VoiceModel | None:
    """Look up a known voice model by Hugging Face repo id."""
    return _BY_REPO.get(repo)


def chat_voice() -> VoiceModel:
    """The default lightweight voice for in-chat 'speak replies' (Kokoro)."""
    return next(m for m in BUILTIN if m.chat_default)
