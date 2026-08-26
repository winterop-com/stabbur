"""Tests for the voice HTTP surface (``stabbur.routers.serving.voice``).

test_api.py already covers the unsupported-model 422, the unknown-model 404, and the tts-1 alias;
these cover the backend-dispatch branches those don't reach, by monkeypatching the engine
modules (``kokoro`` / ``tts`` / ``voice_runtime`` / ``audio_export``) rather than requiring
real, platform-gated engines. They pin down: engine routing (kokoro vs mlx), the
503-when-uninstalled gates, the 404 for a voice/repo not in the library, temp-WAV cleanup,
the 422 for unspeakable input, and the non-WAV transcode path.
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from stabbur.app import create_app
from stabbur.config import Settings


@pytest.fixture
def app() -> FastAPI:
    """App with a clean (no model loaded) manager."""
    return create_app(Settings(serve_model=None))


@pytest.fixture
async def client(app: FastAPI):
    """Async client running the app's lifespan."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _wav_file(tmp_path: Path, name: str = "out.wav") -> Path:
    """Write a placeholder WAV the endpoint will read + unlink (contents are opaque to it)."""
    p = tmp_path / name
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake-audio")
    return p


# ---------------------------------------------------------------------------
# /api/speak
# ---------------------------------------------------------------------------


async def test_speak_kokoro_returns_wav_and_cleans_up_temp(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A kokoro:<voice> id routes to the ONNX engine; the endpoint returns audio/wav and must
    # unlink the temp WAV it read (a leak would fill the temp dir over a session).
    wav = _wav_file(tmp_path)
    monkeypatch.setattr("stabbur.routers.serving.voice.kokoro.available", lambda: True)
    monkeypatch.setattr(
        "stabbur.routers.serving.voice.kokoro.synthesize",
        lambda text, voice, out_path=None, speed=1.0: wav,
    )
    r = await client.post("/api/speak", json={"text": "hello there", "voice": "kokoro:af_heart"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content == b"RIFF\x00\x00\x00\x00WAVEfake-audio"
    assert not wav.exists()  # temp file cleaned up after the response is built


async def test_speak_kokoro_unavailable_is_503(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # A kokoro voice when the tts extra isn't installed must 503 (install hint), not 500.
    monkeypatch.setattr("stabbur.routers.serving.voice.kokoro.available", lambda: False)
    r = await client.post("/api/speak", json={"text": "hello", "voice": "kokoro:af_heart"})
    assert r.status_code == 503
    assert "kokoro" in r.json()["detail"].lower()


async def test_speak_unspeakable_input_is_422(client: AsyncClient) -> None:
    # Input that reduces to nothing after markdown/code stripping (only a fenced code block)
    # must 422 — there's nothing to synthesize, so don't hand empty text to an engine.
    r = await client.post("/api/speak", json={"text": "```\nprint('hi')\n```"})
    assert r.status_code == 422
    assert "nothing speakable" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# /v1/audio/speech
# ---------------------------------------------------------------------------


async def test_audio_speech_kokoro_happy_path_returns_wav(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The default model "kokoro" routes to the kokoro_onnx backend; a stubbed synthesize yields
    # a WAV that passes through the (WAV-noop) exporter unchanged as audio/wav.
    wav = _wav_file(tmp_path)
    monkeypatch.setattr("stabbur.routers.serving.voice.kokoro.available", lambda: True)
    monkeypatch.setattr(
        "stabbur.routers.serving.voice.kokoro.synthesize",
        lambda text, voice, out_path=None, speed=1.0: wav,
    )
    r = await client.post("/v1/audio/speech", json={"model": "kokoro", "input": "hello world"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content == b"RIFF\x00\x00\x00\x00WAVEfake-audio"
    assert not wav.exists()  # temp cleaned up


async def test_audio_speech_mp3_goes_through_export_convert(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # response_format="mp3" must transcode via audio_export.convert and change the media type
    # (kokoro synthesizes WAV; the exporter is the only thing that produces mp3).
    wav = _wav_file(tmp_path)
    monkeypatch.setattr("stabbur.routers.serving.voice.kokoro.available", lambda: True)
    monkeypatch.setattr(
        "stabbur.routers.serving.voice.kokoro.synthesize",
        lambda text, voice, out_path=None, speed=1.0: wav,
    )
    monkeypatch.setattr("stabbur.routers.serving.voice.audio_export.convert", lambda data, fmt: b"ID3-fake-mp3")
    r = await client.post("/v1/audio/speech", json={"model": "kokoro", "input": "hello", "response_format": "mp3"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/mpeg"  # media type reflects the requested format
    assert r.content == b"ID3-fake-mp3"  # body is the transcoded output, not the raw WAV


async def test_audio_speech_mlx_backend_unavailable_is_503(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An mlx-audio model when the voice runtime isn't installed (Linux / no extra) must
    # 503 with the install hint, not attempt to load and 500.
    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.available", lambda: False)
    r = await client.post("/v1/audio/speech", json={"model": "spark", "input": "hello"})
    assert r.status_code == 503
    assert "mlx-audio" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# /v1/audio/transcriptions
# ---------------------------------------------------------------------------


async def test_transcriptions_runtime_unavailable_is_503(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Transcription needs mlx-audio; when it's absent the endpoint must 503 before touching the
    # upload, not 500 on a missing import.
    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.available", lambda: False)
    r = await client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", b"RIFFfake", "audio/wav")},
        data={"model": "whisper"},
    )
    assert r.status_code == 503
    assert "mlx-audio" in r.json()["detail"].lower()


async def test_transcriptions_unknown_stt_model_is_404(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # With the runtime available but the requested STT model absent from the library, the
    # endpoint must 404 (resolve the model before transcribing), not run against nothing.
    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.available", lambda: True)
    monkeypatch.setattr("stabbur.routers.serving.voice.library_ops.find", lambda repo: [])
    r = await client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", b"RIFFfake", "audio/wav")},
        data={"model": "whisper"},
    )
    assert r.status_code == 404
    assert "not in the library" in r.json()["detail"].lower()
