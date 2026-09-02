"""Tests for the voice HTTP surface (``stabbur.routers.serving.voice``).

test_api.py already covers the unsupported-model 422, the unknown-model 404, and the tts-1 alias;
these cover the backend-dispatch branches those don't reach, by monkeypatching the engine
modules (``kokoro`` / ``tts`` / ``voice_runtime`` / ``audio_export``) rather than requiring
real, platform-gated engines. They pin down: engine routing (kokoro vs mlx), the
503-when-uninstalled gates, the 404 for a voice/repo not in the library, temp-WAV cleanup,
the 422 for unspeakable input, the non-WAV transcode path, the request-size caps, and the
error shapes the two TTS routes must share.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from stabbur.app import create_app
from stabbur.config import Settings
from stabbur.routers.serving import voice as voice_router


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
# Shared behaviour across the two TTS routes, and the request-size caps
# ---------------------------------------------------------------------------


async def test_unknown_voice_is_the_same_422_on_both_tts_routes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both TTS routes synthesize through one helper, so an unknown voice id is a client error
    # with the same shape on each. The /v1 route used to hand the id straight to the engine and
    # answer 500 for what /api/speak already called a clean 422.
    monkeypatch.setattr("stabbur.routers.serving.voice.kokoro.available", lambda: True)
    monkeypatch.setattr("stabbur.routers.serving.voice.kokoro.voices", lambda: [SimpleNamespace(id="af_heart")])

    def never(text: str, voice: str, out_path: Path | None = None, speed: float = 1.0) -> Path:
        raise AssertionError("synthesis must not start for an unknown voice")

    monkeypatch.setattr("stabbur.routers.serving.voice.kokoro.synthesize", never)
    speak = await client.post("/api/speak", json={"text": "hello", "voice": "kokoro:not_a_voice"})
    openai = await client.post(
        "/v1/audio/speech", json={"model": "kokoro", "input": "hello", "voice": "kokoro:not_a_voice"}
    )
    assert speak.status_code == 422
    assert openai.status_code == 422
    assert speak.json()["detail"] == openai.json()["detail"] == "unknown Kokoro voice 'not_a_voice'"


async def test_engine_unavailable_is_the_same_503_on_both_tts_routes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same for the "engine isn't installed" gate: one message, both routes.
    monkeypatch.setattr("stabbur.routers.serving.voice.kokoro.available", lambda: False)
    speak = await client.post("/api/speak", json={"text": "hello"})
    openai = await client.post("/v1/audio/speech", json={"model": "kokoro", "input": "hello"})
    assert speak.status_code == 503
    assert openai.status_code == 503
    assert speak.json()["detail"] == openai.json()["detail"]


@pytest.mark.parametrize(
    ("path", "body"),
    [("/api/speak", {"text": "x"}), ("/v1/audio/speech", {"model": "kokoro", "input": "x"})],
)
async def test_oversized_text_is_413_on_both_tts_routes(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, path: str, body: dict[str, str]
) -> None:
    # Text is synthesized wholesale, so an unbounded body is a memory fault rather than a slow
    # request. Both routes cap it, and the rejection names the limit.
    def never(text: str, voice: str, out_path: Path | None = None, speed: float = 1.0) -> Path:
        raise AssertionError("synthesis must not start for an oversized request")

    monkeypatch.setattr("stabbur.routers.serving.voice.kokoro.available", lambda: True)
    monkeypatch.setattr("stabbur.routers.serving.voice.kokoro.synthesize", never)
    key = "text" if path == "/api/speak" else "input"
    r = await client.post(path, json={**body, key: "a" * (voice_router._MAX_TEXT_CHARS + 1)})
    assert r.status_code == 413
    assert str(voice_router._MAX_TEXT_CHARS) in r.json()["detail"]


async def test_oversized_reference_clip_is_413(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # A cloning reference clip arrives base64 in the JSON body; cap the encoded string so the
    # decode (and the copy it allocates) can't be driven arbitrarily large.
    monkeypatch.setattr(voice_router, "_MAX_AUDIO_BYTES", 2 * 1024 * 1024)
    monkeypatch.setattr(voice_router, "_MAX_REF_AUDIO_B64", 1024)
    r = await client.post(
        "/v1/audio/speech",
        json={"model": "spark", "input": "hello", "ref_audio_b64": "A" * 2048, "ref_text": "hi"},
    )
    assert r.status_code == 413
    assert "ref_audio_b64" in r.json()["detail"]


async def test_malformed_reference_clip_is_422(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A reference clip that isn't valid base64 is the caller's mistake: 422, not the 500 a raw
    # decode error would produce.
    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.available", lambda: True)
    monkeypatch.setattr(
        "stabbur.routers.serving.voice.library_ops.find",
        lambda repo: [SimpleNamespace(voice_kind="tts", load_target=tmp_path / "tts-model")],
    )
    r = await client.post(
        "/v1/audio/speech",
        json={"model": "spark", "input": "hello", "ref_audio_b64": "not base64!!", "ref_text": "hi"},
    )
    assert r.status_code == 422
    assert "base64" in r.json()["detail"]


async def test_instruct_reaches_the_runtime_for_a_design_model(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Voice design is the whole point of a design model, and it travels as a plain generation
    # param — assert it actually arrives at the runtime rather than being dropped in the router.
    seen: dict[str, object] = {}
    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.available", lambda: True)
    monkeypatch.setattr(
        "stabbur.routers.serving.voice.library_ops.find",
        lambda repo: [SimpleNamespace(voice_kind="tts", load_target=tmp_path / "tts-model")],
    )

    def fake_synthesize(model: Path, text: str, **kwargs: object) -> bytes:
        seen.update(kwargs)
        return b"RIFFfake"

    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.synthesize", fake_synthesize)
    r = await client.post(
        "/v1/audio/speech",
        json={"model": "voxcpm2", "input": "hello", "instruct": "a calm older man"},
    )
    assert r.status_code == 200
    assert seen["instruct"] == "a calm older man"


async def test_speed_is_withheld_from_a_model_that_ignores_it(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # mlx-audio accepts `speed` for every model and the ones that don't implement it swallow it.
    # Sending it anyway makes a no-op look supported; the registry flag decides.
    seen: dict[str, object] = {}
    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.available", lambda: True)
    monkeypatch.setattr(
        "stabbur.routers.serving.voice.library_ops.find",
        lambda repo: [SimpleNamespace(voice_kind="tts", load_target=tmp_path / "tts-model")],
    )

    def fake_synthesize(model: Path, text: str, **kwargs: object) -> bytes:
        seen.update(kwargs)
        return b"RIFFfake"

    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.synthesize", fake_synthesize)
    r = await client.post("/v1/audio/speech", json={"model": "voxcpm2", "input": "hello", "speed": 1.5})
    assert r.status_code == 200
    assert "speed" not in seen  # VoxCPM2 renders at its own pace


async def test_instruct_on_a_non_design_model_is_422(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # The runtime hands unknown params straight to the model's generate(), where one it doesn't
    # accept is a TypeError (a 502 after a multi-second load). Reject the mismatch upfront.
    def never(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("synthesis must not start for a model that can't design a voice")

    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.available", lambda: True)
    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.synthesize", never)
    r = await client.post("/v1/audio/speech", json={"model": "spark", "input": "hello", "instruct": "a calm man"})
    assert r.status_code == 422
    assert "voice-design" in r.json()["detail"]


async def test_oversized_instruct_is_413(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # The description is prepended to what the model speaks, so an unbounded one is speakable
    # payload smuggled past the text cap.
    def never(*args: object, **kwargs: object) -> bytes:
        raise AssertionError("synthesis must not start for an oversized description")

    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.available", lambda: True)
    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.synthesize", never)
    r = await client.post(
        "/v1/audio/speech",
        json={"model": "voxcpm2", "input": "hello", "instruct": "a" * (voice_router._MAX_INSTRUCT_CHARS + 1)},
    )
    assert r.status_code == 413
    assert str(voice_router._MAX_INSTRUCT_CHARS) in r.json()["detail"]


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


async def test_transcriptions_within_the_cap_reach_the_runtime(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The upload is streamed to a temp clip in chunks; a normal-sized one must arrive intact
    # at the runtime (the chunking must not truncate or reorder it) and be cleaned up after.
    seen: dict[str, object] = {}
    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.available", lambda: True)
    monkeypatch.setattr(
        "stabbur.routers.serving.voice.library_ops.find",
        lambda repo: [SimpleNamespace(voice_kind="stt", load_target=tmp_path / "stt-model")],
    )

    def fake_transcribe(model: Path, clip: Path, language: str | None = None) -> str:
        seen["bytes"] = clip.read_bytes()
        seen["clip"] = clip
        return "transcribed"

    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.transcribe", fake_transcribe)
    payload = bytes(range(256)) * 4096  # 1 MB: more than one read chunk
    r = await client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", payload, "audio/wav")},
        data={"model": "whisper"},
    )
    assert r.status_code == 200
    assert r.json() == {"text": "transcribed"}
    assert seen["bytes"] == payload  # streamed through whole, in order
    assert not Path(str(seen["clip"])).exists()  # temp clip removed


async def test_transcriptions_oversized_upload_is_413(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An upload past the cap must be refused with 413 before it is all in memory — an
    # unbounded read of a multi-gigabyte body is an OOM, not an error response.
    monkeypatch.setattr(voice_router, "_MAX_AUDIO_BYTES", 2 * 1024 * 1024)
    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.available", lambda: True)
    monkeypatch.setattr(
        "stabbur.routers.serving.voice.library_ops.find",
        lambda repo: [SimpleNamespace(voice_kind="stt", load_target=tmp_path / "stt-model")],
    )

    def never(model: Path, clip: Path, language: str | None = None) -> str:
        raise AssertionError("transcription must not run on an oversized upload")

    monkeypatch.setattr("stabbur.routers.serving.voice.voice_runtime.transcribe", never)
    r = await client.post(
        "/v1/audio/transcriptions",
        files={"file": ("clip.wav", b"\x00" * (3 * 1024 * 1024), "audio/wav")},
        data={"model": "whisper"},
    )
    assert r.status_code == 413
    assert "2 MB" in r.json()["detail"]  # the limit is stated, not just "too large"


async def test_speak_rejects_a_speed_the_engine_cannot_honor(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The validator's range must be the engine's: it used to accept 0.25-0.5, which the engine
    # then rejected mid-synthesis — a 500 for what is plainly a bad request.
    monkeypatch.setattr("stabbur.routers.serving.voice.kokoro.available", lambda: True)
    r = await client.post("/api/speak", json={"text": "hello", "voice": "kokoro:af_heart", "speed": 0.3})
    assert r.status_code == 422
    assert "0.5" in r.json()["detail"]


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
