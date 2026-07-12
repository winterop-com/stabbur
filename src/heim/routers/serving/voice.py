"""Voice API: list voices/TTS models, speak, and the OpenAI /v1/audio speech + transcription."""

import asyncio
import base64
import os
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from heim import library as library_ops
from heim.routers.serving._base import (  # shared router + request deps
    router,
)
from heim.voice import audio as audio_export
from heim.voice import kokoro, tts
from heim.voice import registry as voice_registry
from heim.voice import runtime as voice_runtime
from heim.voice.registry import Backend


class VoiceModelInfo(BaseModel):
    """A library voice (TTS/STT) model, enriched with registry metadata, for the Voice UI."""

    name: str  # library repo/name, e.g. "mlx-community/Dia-1.6B"
    kind: str  # "tts" | "stt"
    backend: str  # "kokoro-onnx" | "mlx-audio" | "llama-tts"
    display_name: str
    description: str = ""
    size_human: str
    cloneable: bool = False  # accepts a reference clip to clone a voice (Dia)
    multi_speaker: bool = False  # dialogue with [S1]/[S2] speaker tags (Dia)
    seeded: bool = False  # a fresh random voice per run unless a seed is pinned (Dia)
    voices: list[str] = []  # named preset voices, if statically known
    languages: list[str] = []
    chat_default: bool = False  # the lightweight in-chat "speak replies" voice (Kokoro)
    supported: bool = True  # False = listed but not runnable yet (UI disables synthesis)


@router.get("/api/voice")
def voice_models() -> list[VoiceModelInfo]:
    """List library voice (TTS/STT) models for the Voice UI, enriched from the registry.

    Sync (``def``) so the filesystem scan runs in a worker thread, off the loop.
    """
    out: list[VoiceModelInfo] = []
    seen: set[str] = set()
    for m in library_ops.scan():
        spec = voice_registry.by_repo(m.name)
        # Voice models are the voice/ bucket (voice_kind set) plus llama-tts GGUF models
        # (the legacy tts/ bucket) that the registry knows — so OuteTTS shows here too.
        if (not m.voice_kind and not (m.tts and spec is not None)) or m.name in seen:
            continue
        seen.add(m.name)
        out.append(
            VoiceModelInfo(
                name=m.name,
                kind=m.voice_kind or (spec.kind.value if spec else "tts"),
                backend=spec.backend.value if spec else "",
                display_name=spec.display_name if spec else m.name.split("/")[-1],
                description=spec.description if spec else "",
                size_human=m.size_human,
                cloneable=spec.cloneable if spec else False,
                multi_speaker=spec.multi_speaker if spec else False,
                seeded=bool(spec and spec.voice_mode == voice_registry.VoiceMode.seeded),
                voices=list(spec.voices) if spec else [],
                languages=list(spec.languages) if spec else list(m.languages),
                chat_default=spec.chat_default if spec else False,
                supported=spec.supported if spec else True,
            )
        )
    return out


class TTSModelInfo(BaseModel):
    """A library TTS model, for the UI's voice picker."""

    name: str
    languages: list[str] = []
    size_human: str


@router.get("/api/tts")
def tts_models() -> list[TTSModelInfo]:
    """List library text-to-speech models (empty if none pulled)."""
    return [TTSModelInfo(name=m.name, languages=m.languages, size_human=m.size_human) for m in library_ops.tts_models()]


class VoiceInfo(BaseModel):
    """A selectable voice for the Listen picker, spanning both TTS engines."""

    id: str
    """Voice id: ``kokoro:<name>``, ``oute`` (default), or ``oute:<model>``."""
    label: str
    engine: str  # "kokoro" | "oute"
    language: str = ""
    gender: str = ""


@router.get("/api/voices")
def voices() -> list[VoiceInfo]:
    """Every available voice: Kokoro's built-in voices plus OuteTTS (llama-tts).

    Kokoro (the ``tts`` extra) contributes 54 named voices; OuteTTS contributes a
    default plus any library TTS models. Empty if neither engine is installed.
    """
    out: list[VoiceInfo] = []
    if kokoro.available():
        out += [
            VoiceInfo(id=f"kokoro:{v.id}", label=v.name, engine="kokoro", language=v.language, gender=v.gender)
            for v in kokoro.voices()
        ]
    if tts.available():
        out.append(VoiceInfo(id="oute", label="OuteTTS (default)", engine="oute"))
        out += [
            VoiceInfo(id=f"oute:{m.name}", label=m.name.split("/")[-1], engine="oute", language=", ".join(m.languages))
            for m in library_ops.tts_models()
        ]
    return out


class SpeakRequest(BaseModel):
    """Text to synthesize into speech, with an optional voice id."""

    text: str
    voice: str | None = None  # "kokoro:<name>" | "oute" | "oute:<model>"; None → default
    model: str | None = None  # deprecated: a library OuteTTS model name (→ oute:<model>)


def _default_voice() -> str:
    """The voice to use when a request specifies none (Kokoro if installed)."""
    return "kokoro:af_heart" if kokoro.available() else "oute"


@router.post("/api/speak")
async def speak(req: SpeakRequest) -> Response:
    """Text-to-speech: synthesize ``text`` to a WAV via the chosen voice's engine.

    Markdown is reduced to prose first (so syntax/code aren't read aloud). Kokoro
    voices route to the ONNX engine (``tts`` extra); ``oute``/``oute:<model>``
    route to ``llama-tts``. Blocking synthesis runs in a worker thread; returns
    ``audio/wav`` bytes. 503 if the chosen engine isn't installed.
    """
    text = tts.speech_text(req.text)
    if not text:
        raise HTTPException(status_code=422, detail="nothing speakable (only code or formatting)")

    voice = req.voice or (f"oute:{req.model}" if req.model else _default_voice())
    try:
        if voice.startswith("kokoro:"):
            if not kokoro.available():
                raise HTTPException(status_code=503, detail="Kokoro TTS is unavailable — reinstall heim (`uv sync`)")
            name = voice.split(":", 1)[1]
            # An unknown voice id is a client error: validate here so it 422s, rather than letting
            # kokoro.synthesize raise RuntimeError("unknown Kokoro voice …") that maps to a 500 below.
            if name not in {v.id for v in kokoro.voices()}:
                raise HTTPException(status_code=422, detail=f"unknown Kokoro voice {name!r}")
            wav_path = await asyncio.to_thread(kokoro.synthesize, text, name, None)
        else:
            if not tts.available():
                raise HTTPException(status_code=503, detail="llama-tts is not installed (install llama.cpp)")
            model_path = vocoder_path = None
            if voice.startswith("oute:"):
                name = voice.split(":", 1)[1]
                matches = [m for m in library_ops.find(name) if m.tts]
                if not matches:
                    raise HTTPException(status_code=404, detail=f"No TTS model matches {name!r}")
                model_path, vocoder_path = matches[0].load_target, matches[0].vocoder
            wav_path = await asyncio.to_thread(tts.synthesize, text, None, model_path, vocoder_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    data = wav_path.read_bytes()
    wav_path.unlink(missing_ok=True)
    return Response(content=data, media_type="audio/wav")


# OpenAI's own TTS model names, accepted as aliases for the default chat voice so a stock
# OpenAI client pointed at heim works unchanged.
_OPENAI_TTS_ALIASES = frozenset({"tts-1", "tts-1-hd", "gpt-4o-mini-tts"})


class AudioSpeechRequest(BaseModel):
    """OpenAI ``/v1/audio/speech`` request, plus heim's voice-cloning extensions."""

    model: str = "kokoro"  # a voice id ("kokoro"/"dia"/"qwen3-tts") or a library repo
    input: str  # the text to speak
    voice: str | None = None  # named preset voice (Kokoro/Qwen3-TTS); ignored when cloning
    response_format: str = "wav"  # wav | mp3 | flac | opus | ogg | aac (non-wav needs ffmpeg)
    # heim extensions for voice cloning (Dia): a reference clip (base64 WAV) + its transcript.
    ref_audio_b64: str | None = None
    ref_text: str | None = None
    seed: int | None = None  # pin Dia's otherwise-random voice for reproducibility


def _voice_library_model(repo: str, *, kind: str | None = None) -> library_ops.LibraryModel:
    """Resolve a library voice model by repo (optionally constrained to tts/stt), or 404."""
    matches = [m for m in library_ops.find(repo) if m.voice_kind and (kind is None or m.voice_kind == kind)]
    if not matches:
        raise HTTPException(status_code=404, detail=f"voice model {repo!r} is not in the library")
    return matches[0]


@router.post("/v1/audio/speech")
async def audio_speech(req: AudioSpeechRequest) -> Response:
    """Synthesize speech (OpenAI ``/v1/audio/speech``) across heim's voice backends.

    Routes by the model's backend: Kokoro -> the cross-platform ONNX path (heim's
    lightweight chat voice); mlx-audio models (Dia, Qwen3-TTS) -> the Apple-Silicon
    runtime, where ``ref_audio_b64`` + ``ref_text`` clone a voice (Dia). Markdown is
    reduced to prose first; blocking synthesis runs off-loop. Returns ``audio/wav``.
    """
    text = tts.speech_text(req.input)
    if not text:
        raise HTTPException(status_code=422, detail="nothing speakable (only code or formatting)")

    spec = voice_registry.get(req.model) or voice_registry.by_repo(req.model)
    if spec is None and req.model in _OPENAI_TTS_ALIASES:
        # Generic OpenAI clients send OpenAI's own model names; map them to the default
        # chat voice so the endpoint stays drop-in compatible.
        spec = voice_registry.get("kokoro")
    if spec is None:
        # An unknown model must 404, not silently synthesize with the Kokoro fallback voice —
        # a caller asking for a specific model would get wrong-voice audio with a 200.
        raise HTTPException(
            status_code=404,
            detail=f"unknown TTS model {req.model!r} — use a registry voice id or repo (see /api/voice)",
        )
    # Enforce the registry's supported flag here, at the action, not only in the UI (A6/VO-M3):
    # a model marked unsupported (e.g. Qwen3-TTS — mlx-audio can't load its speech tokenizer) would
    # otherwise be attempted and fail as a slow, opaque 502. Reject it upfront with a clear reason.
    if not spec.supported:
        raise HTTPException(status_code=422, detail=f"{req.model!r} isn't supported for synthesis in heim yet.")
    backend = spec.backend

    if backend == Backend.kokoro_onnx:
        if not kokoro.available():
            raise HTTPException(status_code=503, detail="Kokoro TTS is unavailable — reinstall heim (`uv sync`)")
        name = (req.voice or "af_heart").split(":")[-1]
        wav_path = await asyncio.to_thread(kokoro.synthesize, text, name, None)
        data = wav_path.read_bytes()
        wav_path.unlink(missing_ok=True)
    elif backend == Backend.mlx_audio:
        if not voice_runtime.available():
            raise HTTPException(status_code=503, detail="mlx-audio is not installed (uv sync --extra voice)")
        model = _voice_library_model(spec.repo if spec else req.model, kind="tts")
        ref_path: Path | None = None
        try:
            if req.ref_audio_b64:
                fd, name = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                ref_path = Path(name)
                ref_path.write_bytes(base64.b64decode(req.ref_audio_b64))
            params: dict[str, Any] = {"seed": req.seed} if req.seed is not None else {}
            data = await asyncio.to_thread(
                _synthesize_mlx, model.load_target, text, req.voice, ref_path, req.ref_text, params
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            if ref_path is not None:
                ref_path.unlink(missing_ok=True)
    elif backend == Backend.llama_tts:
        if not tts.available():
            raise HTTPException(status_code=503, detail="llama-tts is not installed (install llama.cpp)")
        # Resolve a copy that carries its vocoder (the functional tts/ layout), not a
        # bare weights dup — llama-tts needs both the model and its paired vocoder.
        repo = spec.repo if spec else req.model
        matches = [m for m in library_ops.tts_models() if m.name == repo and m.vocoder]
        if not matches:
            raise HTTPException(status_code=404, detail=f"{repo!r} has no runnable llama-tts copy (missing vocoder)")
        wav_path = await asyncio.to_thread(tts.synthesize, text, None, matches[0].load_target, matches[0].vocoder)
        data = wav_path.read_bytes()
        wav_path.unlink(missing_ok=True)
    else:
        raise HTTPException(status_code=422, detail=f"model {req.model!r} is not a TTS model")

    # Synthesis produces WAV; transcode to the requested format (ffmpeg) if it isn't WAV.
    fmt = audio_export.normalize(req.response_format)
    try:
        data = await asyncio.to_thread(audio_export.convert, data, fmt)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=data, media_type=audio_export.media_type(fmt))


def _synthesize_mlx(
    model: Path, text: str, voice: str | None, ref_audio: Path | None, ref_text: str | None, params: dict[str, Any]
) -> bytes:
    """Thread body: call the mlx-audio runtime (kept out of the endpoint for a clean to_thread)."""
    return voice_runtime.synthesize(model, text, voice=voice, ref_audio=ref_audio, ref_text=ref_text, **params)


@router.post("/v1/audio/transcriptions")
async def audio_transcriptions(
    file: Annotated[UploadFile, File()],
    model: Annotated[str, Form()] = "whisper",
    language: Annotated[str | None, Form()] = None,
) -> dict[str, str]:
    """Transcribe audio to text (OpenAI ``/v1/audio/transcriptions``) via Whisper (mlx-audio)."""
    if not voice_runtime.available():
        raise HTTPException(status_code=503, detail="mlx-audio is not installed (uv sync --extra voice)")
    spec = voice_registry.get(model) or voice_registry.by_repo(model)
    stt_model = _voice_library_model(spec.repo if spec else model, kind="stt")
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    fd, name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    clip = Path(name)
    try:
        clip.write_bytes(await file.read())
        text = await asyncio.to_thread(voice_runtime.transcribe, stt_model.load_target, clip, language=language)
    finally:
        clip.unlink(missing_ok=True)
    return {"text": text}
