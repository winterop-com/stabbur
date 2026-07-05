"""Local multi-voice text-to-speech via Kokoro-82M (ONNX).

Kokoro is a small (82M) open-weights TTS with **54 built-in named voices** across
9 languages, run through onnxruntime — one backend for macOS + Linux, no GPU and
no reference audio. It's an optional extra (``uv sync --extra tts``); kodo imports
it lazily so the rest of the app works without it. The model + combined voices
file are fetched on first use into the library (``<library_root>/tts/kokoro``), so
they travel with it — mirroring how ``llama-tts`` auto-fetches OuteTTS.

This is the multi-voice engine that complements :mod:`kodo.tts` (single-voice
OuteTTS via ``llama-tts``).
"""

import importlib.util
import tempfile
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

from kodo.config import get_settings

# Pinned kokoro-onnx "model-files" release: the fp32 model + combined voices .npz.
# fp32 is used over int8 because on CPU it is both faster and higher quality here
# (int8 quant ops aren't accelerated); it's a one-time ~310 MB fetch.
_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
_MODEL_FILE = "kokoro-v1.0.onnx"
_VOICES_FILE = "voices-v1.0.bin"

# Voice-name language prefix (first char) -> (display language, espeak lang code).
_LANGS: dict[str, tuple[str, str]] = {
    "a": ("American English", "en-us"),
    "b": ("British English", "en-gb"),
    "e": ("Spanish", "es"),
    "f": ("French", "fr-fr"),
    "h": ("Hindi", "hi"),
    "i": ("Italian", "it"),
    "j": ("Japanese", "ja"),
    "p": ("Portuguese (Brazil)", "pt-br"),
    "z": ("Mandarin Chinese", "cmn"),
}

# The fixed 54-voice set of the v1.0 release. Enumerating the picker needs no
# download; the combined voices file (which also lists them) ships with the model.
_VOICE_IDS: tuple[str, ...] = (
    "af_alloy",
    "af_aoede",
    "af_bella",
    "af_heart",
    "af_jessica",
    "af_kore",
    "af_nicole",
    "af_nova",
    "af_river",
    "af_sarah",
    "af_sky",
    "am_adam",
    "am_echo",
    "am_eric",
    "am_fenrir",
    "am_liam",
    "am_michael",
    "am_onyx",
    "am_puck",
    "am_santa",
    "bf_alice",
    "bf_emma",
    "bf_isabella",
    "bf_lily",
    "bm_daniel",
    "bm_fable",
    "bm_george",
    "bm_lewis",
    "ef_dora",
    "em_alex",
    "em_santa",
    "ff_siwis",
    "hf_alpha",
    "hf_beta",
    "hm_omega",
    "hm_psi",
    "if_sara",
    "im_nicola",
    "jf_alpha",
    "jf_gongitsune",
    "jf_nezumi",
    "jf_tebukuro",
    "jm_kumo",
    "pf_dora",
    "pm_alex",
    "pm_santa",
    "zf_xiaobei",
    "zf_xiaoni",
    "zf_xiaoxiao",
    "zf_xiaoyi",
    "zm_yunjian",
    "zm_yunxi",
    "zm_yunxia",
    "zm_yunyang",
)


class KokoroVoice(BaseModel):
    """One built-in Kokoro voice, for the UI voice picker."""

    id: str
    """The voice id, e.g. ``af_heart``."""
    name: str
    """Display name, e.g. ``Heart``."""
    language: str
    """Human-readable language, e.g. ``American English``."""
    gender: str
    """``female`` or ``male`` (from the voice-name prefix)."""


def available() -> bool:
    """Whether the Kokoro extra (``kokoro-onnx``) is installed."""
    return importlib.util.find_spec("kokoro_onnx") is not None


def lang_code(voice: str) -> str:
    """The espeak language code kokoro should phonemize this voice's text with."""
    return _LANGS.get(voice[:1], ("", "en-us"))[1]


def _voice_meta(voice: str) -> KokoroVoice:
    language = _LANGS.get(voice[:1], ("Unknown", "en-us"))[0]
    gender = "female" if voice[1:2] == "f" else "male"
    name = voice.split("_", 1)[-1].replace("_", " ").title()
    return KokoroVoice(id=voice, name=name, language=language, gender=gender)


def voices() -> list[KokoroVoice]:
    """The built-in Kokoro voices (the fixed v1.0 set), for the picker."""
    return [_voice_meta(v) for v in _VOICE_IDS]


def _assets_dir() -> Path:
    """Where the Kokoro model + voices live — inside the library, so they travel with it."""
    return get_settings().library_root / "tts" / "kokoro"


def assets_present() -> bool:
    """Whether the model + voices have already been downloaded."""
    d = _assets_dir()
    return (d / _MODEL_FILE).is_file() and (d / _VOICES_FILE).is_file()


def _download(url: str, dest: Path) -> None:
    """Stream ``url`` to ``dest`` (atomic via a ``.part`` file)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=300.0) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 16):
                fh.write(chunk)
    tmp.replace(dest)


def ensure_assets() -> tuple[Path, Path]:
    """Return ``(model, voices)`` paths, downloading them on first use (~310 MB)."""
    d = _assets_dir()
    model, vox = d / _MODEL_FILE, d / _VOICES_FILE
    if not model.is_file():
        _download(f"{_RELEASE}/{_MODEL_FILE}", model)
    if not vox.is_file():
        _download(f"{_RELEASE}/{_VOICES_FILE}", vox)
    return model, vox


# Cache the loaded engine (loading the ONNX takes ~1 s); reused across requests.
_engine: Any = None


def _get_engine() -> Any:
    global _engine
    if _engine is None:
        import espeakng_loader  # noqa: PLC0415
        from kokoro_onnx import Kokoro  # noqa: PLC0415
        from kokoro_onnx.config import EspeakConfig  # noqa: PLC0415

        model, vox = ensure_assets()
        # Point kokoro at the bundled espeak-ng (no system binary needed).
        espeak = EspeakConfig(
            lib_path=espeakng_loader.get_library_path(),
            data_path=espeakng_loader.get_data_path(),
        )
        _engine = Kokoro(str(model), str(vox), espeak_config=espeak)
    return _engine


_VOICE_IDS_SET = frozenset(_VOICE_IDS)


def synthesize(text: str, voice: str, out_path: Path | None = None) -> Path:
    """Generate a speech WAV for ``text`` in the built-in ``voice``.

    Downloads the model on first use and loads it once (cached). Blocking — call
    from a worker thread in async contexts. Returns the written WAV path.

    Raises:
        RuntimeError: If the extra isn't installed, the voice is unknown, the
            text is empty, or synthesis produces no audio.
    """
    if not available():
        raise RuntimeError("Kokoro TTS is not installed. Run `make install-tts` (uv sync --extra tts).")
    if voice not in _VOICE_IDS_SET:
        raise RuntimeError(f"unknown Kokoro voice {voice!r}")
    if not text.strip():
        raise RuntimeError("nothing to speak (empty text)")

    import soundfile as sf  # noqa: PLC0415

    if out_path:
        out = out_path
    else:  # NamedTemporaryFile closes its fd on __exit__ (mkstemp leaks it); delete=False keeps the file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            out = Path(f.name)
    samples, sample_rate = _get_engine().create(text, voice=voice, speed=1.0, lang=lang_code(voice))
    sf.write(str(out), samples, sample_rate)
    if not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError("Kokoro synthesis produced no audio")
    return out
