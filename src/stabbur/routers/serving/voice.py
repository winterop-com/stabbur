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

from stabbur import library as library_ops
from stabbur.routers.serving._base import (  # shared router + request deps
    router,
)
from stabbur.voice import audio as audio_export
from stabbur.voice import kokoro, tts
from stabbur.voice import registry as voice_registry
from stabbur.voice import runtime as voice_runtime
from stabbur.voice.registry import Backend


class VoiceModelInfo(BaseModel):
    """A library voice (TTS/STT) model, enriched with registry metadata, for the Voice UI."""

    name: str  # library repo/name
    kind: str  # "tts" | "stt"
    backend: str  # "kokoro-onnx" | "mlx-audio" | "llama-tts"
    display_name: str
    description: str = ""
    size_human: str
    cloneable: bool = False  # accepts a reference clip to clone a voice
    multi_speaker: bool = False  # dialogue with [S1]/[S2] speaker tags
    seeded: bool = False  # a fresh random voice per run unless a seed is pinned
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
        if not m.voice_kind or m.name in seen:
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
    """A selectable voice for the Listen picker."""

    id: str
    """Voice id: ``kokoro:<name>``."""
    label: str
    engine: str  # "kokoro"
    language: str = ""
    gender: str = ""


@router.get("/api/voices")
def voices() -> list[VoiceInfo]:
    """Every available Listen voice: Kokoro's 54 built-ins (empty if the engine is missing)."""
    if not kokoro.available():
        return []
    return [
        VoiceInfo(id=f"kokoro:{v.id}", label=v.name, engine="kokoro", language=v.language, gender=v.gender)
        for v in kokoro.voices()
    ]


# Request-size caps. Voice requests carry the only large payloads in stabbur's API, and an
# uncapped one is a memory fault, not a rejection: text is synthesized wholesale, and a clip is
# held in RAM while it is decoded and written. The limits are set well above real use — 20k
# characters is hours of speech, 25 MB is well over an hour of 16 kHz mono audio — so they bite
# only on abuse or a client bug.
_MAX_TEXT_CHARS = 20_000
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
# Base64 inflates by 4/3, so cap the encoded string rather than the decode's output: the point
# is to reject before allocating, not after.
_MAX_REF_AUDIO_B64 = _MAX_AUDIO_BYTES * 4 // 3 + 16

_DEFAULT_KOKORO_VOICE = "af_heart"


def _validated_text(raw: str) -> str:
    """Reduce request text to speakable prose, capped (413) and non-empty (422)."""
    if len(raw) > _MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail=f"text exceeds the {_MAX_TEXT_CHARS} character limit")
    text = tts.speech_text(raw)
    if not text:
        raise HTTPException(status_code=422, detail="nothing speakable (only code or formatting)")
    return text


async def _kokoro_wav(text: str, voice: str | None, speed: float) -> bytes:
    """Synthesize ``text`` with Kokoro and return the WAV bytes; one path for both TTS routes.

    Shared so ``/api/speak`` and ``/v1/audio/speech`` answer identically instead of drifting:
    503 when the engine isn't installed, 422 for an unknown voice id (validated here rather
    than surfacing the engine's ``RuntimeError`` as an opaque 500), 500 for a genuine synthesis
    failure. Synthesis, the read of the produced WAV and its cleanup all run off the event loop.
    """
    if not kokoro.available():
        raise HTTPException(status_code=503, detail="Kokoro TTS is unavailable — reinstall stabbur (`uv sync`)")
    name = (voice or _DEFAULT_KOKORO_VOICE).split(":", 1)[-1]
    if name not in {v.id for v in kokoro.voices()}:
        raise HTTPException(status_code=422, detail=f"unknown Kokoro voice {name!r}")
    try:
        wav_path = await asyncio.to_thread(lambda: kokoro.synthesize(text, name, None, speed=speed))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    try:
        return await asyncio.to_thread(wav_path.read_bytes)
    finally:
        await asyncio.to_thread(lambda: wav_path.unlink(missing_ok=True))


class SpeakRequest(BaseModel):
    """Text to synthesize into speech, with an optional voice id."""

    text: str
    voice: str | None = None  # "kokoro:<name>"; None → the default Kokoro voice
    speed: float | None = None  # playback speed multiplier (0.5-2.0); None → 1.0


@router.post("/api/speak")
async def speak(req: SpeakRequest) -> Response:
    """Text-to-speech: synthesize ``text`` to a WAV via Kokoro (the in-chat Listen engine).

    Markdown is reduced to prose first (so syntax/code aren't read aloud). Blocking
    synthesis runs in a worker thread; returns ``audio/wav`` bytes. 503 if the engine
    is unavailable. Other voice models speak through the OpenAI ``/v1/audio/speech``
    route, which knows the full registry.
    """
    text = _validated_text(req.text)
    speed = _validated_speed(req.speed)
    data = await _kokoro_wav(text, req.voice, speed)
    return Response(content=data, media_type="audio/wav")


# OpenAI's own TTS model names, accepted as aliases for the default chat voice so a stock
# OpenAI client pointed at stabbur works unchanged.
_OPENAI_TTS_ALIASES = frozenset({"tts-1", "tts-1-hd", "gpt-4o-mini-tts"})


class AudioSpeechRequest(BaseModel):
    """OpenAI ``/v1/audio/speech`` request, plus stabbur's voice-cloning extensions."""

    model: str = "kokoro"  # a registry voice id, or a library repo
    input: str  # the text to speak
    voice: str | None = None  # named preset voice; ignored when cloning
    response_format: str = "wav"  # wav | mp3 | flac | opus | ogg | aac (non-wav needs ffmpeg)
    # stabbur extensions for voice cloning: a reference clip (base64 WAV) + its transcript.
    ref_audio_b64: str | None = None
    ref_text: str | None = None
    seed: int | None = None  # pin a seeded model's otherwise-random voice for reproducibility
    speed: float | None = None  # playback speed multiplier (0.5-2.0); None → 1.0


def _validated_speed(speed: float | None) -> float:
    """Clamp-check a requested speed multiplier (422 outside the engine's range); None -> 1.0.

    The bound is Kokoro's own (:mod:`stabbur.voice.kokoro`), not a number chosen here: a request
    the validator waved through only for the engine to reject was a 500, not a 422.
    """
    if speed is None:
        return 1.0
    if not kokoro.SPEED_MIN <= speed <= kokoro.SPEED_MAX:
        raise HTTPException(status_code=422, detail=f"speed must be between {kokoro.SPEED_MIN} and {kokoro.SPEED_MAX}")
    return speed


def _voice_library_model(repo: str, *, kind: str | None = None) -> library_ops.LibraryModel:
    """Resolve a library voice model by repo (optionally constrained to tts/stt), or 404."""
    matches = [m for m in library_ops.find(repo) if m.voice_kind and (kind is None or m.voice_kind == kind)]
    if not matches:
        raise HTTPException(status_code=404, detail=f"voice model {repo!r} is not in the library")
    return matches[0]


@router.post("/v1/audio/speech")
async def audio_speech(req: AudioSpeechRequest) -> Response:
    """Synthesize speech (OpenAI ``/v1/audio/speech``) across stabbur's voice backends.

    Routes by the model's backend: Kokoro -> the cross-platform ONNX path (stabbur's
    lightweight chat voice); other registry models -> the Apple-Silicon mlx-audio
    runtime, where ``ref_audio_b64`` + ``ref_text`` clone a voice. Markdown is
    reduced to prose first; blocking synthesis runs off-loop. Returns ``audio/wav``.
    """
    text = _validated_text(req.input)
    if req.ref_audio_b64 is not None and len(req.ref_audio_b64) > _MAX_REF_AUDIO_B64:
        raise HTTPException(
            status_code=413,
            detail=f"ref_audio_b64 exceeds the {_MAX_AUDIO_BYTES // (1024 * 1024)} MB reference-clip limit",
        )

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
        raise HTTPException(status_code=422, detail=f"{req.model!r} isn't supported for synthesis in stabbur yet.")
    backend = spec.backend

    speed = _validated_speed(req.speed)
    if backend == Backend.kokoro_onnx:
        data = await _kokoro_wav(text, req.voice, speed)
    elif backend == Backend.mlx_audio:
        if not voice_runtime.available():
            raise HTTPException(status_code=503, detail="mlx-audio is not installed (uv sync --extra voice)")
        # The library scan is filesystem work: off the loop, like every other scan in the API.
        model = await asyncio.to_thread(_voice_library_model, spec.repo if spec else req.model, kind="tts")
        ref_path: Path | None = None
        try:
            if req.ref_audio_b64:
                fd, tmp_name = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                ref_path = Path(tmp_name)
                await asyncio.to_thread(_write_ref_clip, ref_path, req.ref_audio_b64)
            params: dict[str, Any] = {"seed": req.seed} if req.seed is not None else {}
            if speed != 1.0:
                params["speed"] = speed  # honored by models that support it; ignored otherwise
            data = await asyncio.to_thread(
                _synthesize_mlx, model.load_target, text, req.voice, ref_path, req.ref_text, params
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            if ref_path is not None:
                ref_path.unlink(missing_ok=True)
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


def _write_ref_clip(dest: Path, ref_audio_b64: str) -> None:
    """Thread body: decode a base64 reference clip to ``dest`` (422 if it isn't valid base64)."""
    try:  # binascii.Error (what b64decode raises) is a ValueError subclass
        data = base64.b64decode(ref_audio_b64, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="ref_audio_b64 is not valid base64") from exc
    dest.write_bytes(data)


async def _save_upload(file: UploadFile, dest: Path) -> None:
    """Stream an upload to ``dest`` in bounded chunks, rejecting anything past the size cap (413).

    ``await file.read()`` with no argument pulls the whole body into memory before anything can
    object to its size, so a multi-gigabyte POST is an out-of-memory kill rather than a refusal.
    Reading in chunks bounds what is ever resident and lets the cap fire early; each write goes
    through a thread so the disk IO stays off the event loop.
    """
    total = 0
    with dest.open("wb") as fh:
        while chunk := await file.read(1 << 20):
            total += len(chunk)
            if total > _MAX_AUDIO_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"audio upload exceeds the {_MAX_AUDIO_BYTES // (1024 * 1024)} MB limit",
                )
            await asyncio.to_thread(fh.write, chunk)


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
    # The library scan is filesystem work: off the loop, like every other scan in the API.
    stt_model = await asyncio.to_thread(_voice_library_model, spec.repo if spec else model, kind="stt")
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    fd, tmp_name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    clip = Path(tmp_name)
    try:
        await _save_upload(file, clip)
        text = await asyncio.to_thread(voice_runtime.transcribe, stt_model.load_target, clip, language=language)
    finally:
        clip.unlink(missing_ok=True)
    return {"text": text}
