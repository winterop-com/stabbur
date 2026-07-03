"""Serve voice models via mlx-audio (Apple Silicon): spawn its server, synth + transcribe.

mlx-audio ships a FastAPI server (``mlx_audio.server``) exposing OpenAI-shaped
``/v1/audio/*`` that loads models per request (cached). kodo spawns it like it spawns
llama-server, then posts a library model *path* (so it loads from the drive, not a fresh
HF download). Apple-Silicon only — it's an ``mlx`` runtime; on Linux, Kokoro-ONNX
(:mod:`kodo.kokoro`) covers TTS. Voice cloning uses ``ref_audio`` + ``ref_text``.
"""

from __future__ import annotations

import contextlib
import socket
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path

import httpx

_STARTUP_TIMEOUT = 120.0  # mlx-audio server import + first readiness


def available() -> bool:
    """Whether the mlx-audio runtime is importable (Apple-Silicon ``mlx`` extra installed)."""
    try:
        import mlx_audio.server  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.contextmanager
def serve() -> Generator[str, None, None]:
    """Spawn ``mlx_audio.server`` on a free port and yield its base URL; stop it on exit."""
    if not available():
        raise RuntimeError("mlx-audio not installed — run `uv sync --extra mlx` (Apple Silicon).")
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "mlx_audio.server", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_ready(base, proc)
        yield base
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
        if proc.poll() is None:
            proc.kill()


def _wait_ready(base: str, proc: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + _STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("mlx-audio server exited during startup")
        try:
            if httpx.get(f"{base}/v1/models", timeout=2).status_code < 500:
                return
        except httpx.HTTPError:
            time.sleep(0.5)
    raise RuntimeError("mlx-audio server did not become ready in time")


def synthesize(
    base: str,
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
    body: dict[str, object] = {"model": str(model), "input": text, "response_format": audio_format}
    if voice is not None:
        body["voice"] = voice
    if ref_audio is not None:
        body["ref_audio"] = str(ref_audio)
        body["ref_text"] = ref_text or ""
    body.update(params)
    resp = httpx.post(f"{base}/v1/audio/speech", json=body, timeout=600)
    resp.raise_for_status()
    return resp.content


def transcribe(base: str, model: Path | str, audio: Path | str, *, language: str | None = None) -> str:
    """Audio file -> transcript text (Whisper). ``model`` is a library path."""
    data: dict[str, str] = {"model": str(model)}
    if language:
        data["language"] = language
    with Path(audio).open("rb") as fh:
        resp = httpx.post(f"{base}/v1/audio/transcriptions", data=data, files={"file": fh}, timeout=600)
    resp.raise_for_status()
    payload = resp.json()
    return str(payload.get("text", "")) if isinstance(payload, dict) else str(payload)
