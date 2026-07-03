"""Audio-format export: convert synthesized WAV bytes to other formats via ffmpeg.

The TTS backends produce WAV. Clients (the ``/v1/audio/speech`` endpoint, ``kodo voice speak``)
often want a compressed format instead — mp3 for size, opus for streaming, flac for lossless.
Rather than teach each backend a second encoder, kodo synthesizes to WAV once and transcodes
here with ffmpeg (a ubiquitous system binary). WAV is passed through untouched, so ffmpeg is
only required when a non-WAV format is actually requested.
"""

from __future__ import annotations

import shutil
import subprocess

# format -> (extra ffmpeg output args, media type). WAV needs no transcode.
_FORMATS: dict[str, tuple[list[str], str]] = {
    "wav": ([], "audio/wav"),
    "mp3": (["-f", "mp3"], "audio/mpeg"),
    "flac": (["-f", "flac"], "audio/flac"),
    "opus": (["-c:a", "libopus", "-f", "ogg"], "audio/ogg"),
    "ogg": (["-c:a", "libvorbis", "-f", "ogg"], "audio/ogg"),
    "aac": (["-c:a", "aac", "-f", "adts"], "audio/aac"),
}

# OpenAI's /v1/audio/speech names pcm/wav; keep an alias so their clients work unchanged.
_ALIASES = {"pcm": "wav"}

FORMATS: tuple[str, ...] = tuple(_FORMATS)


def normalize(fmt: str | None) -> str:
    """Canonical format name (lower-cased, aliases resolved); defaults to ``wav``."""
    name = (fmt or "wav").lower()
    return _ALIASES.get(name, name)


def is_supported(fmt: str | None) -> bool:
    """Whether ``fmt`` is a format kodo can export."""
    return normalize(fmt) in _FORMATS


def media_type(fmt: str | None) -> str:
    """The HTTP ``Content-Type`` for a format (``audio/wav`` if unknown)."""
    return _FORMATS.get(normalize(fmt), _FORMATS["wav"])[1]


def convert(wav: bytes, fmt: str | None) -> bytes:
    """Transcode WAV bytes to ``fmt`` via ffmpeg. WAV (or unknown) is returned untouched.

    Raises:
        RuntimeError: if ffmpeg is needed but missing, or the transcode fails.
    """
    name = normalize(fmt)
    if name == "wav" or name not in _FORMATS:
        return wav
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(f"ffmpeg is required to export {name!r} audio (install it: `brew install ffmpeg`).")
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "wav", "-i", "pipe:0", *_FORMATS[name][0], "pipe:1"]
    proc = subprocess.run(args, input=wav, capture_output=True)  # noqa: S603
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to encode {name!r}: {proc.stderr.decode(errors='replace')[:300]}")
    return proc.stdout
