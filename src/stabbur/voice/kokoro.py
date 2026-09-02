"""Local multi-voice text-to-speech via Kokoro-82M (ONNX).

Kokoro is a small (82M) open-weights TTS with **54 built-in named voices** across
9 languages, run through onnxruntime — one backend for macOS + Linux, no GPU and
no reference audio. It's a base dependency (the always-available in-chat voice); stabbur
still imports it lazily so a broken install degrades gracefully. The model + combined voices
file are fetched on first use into the library (``<library_root>/tts/kokoro``), so
they travel with it — mirroring how ``llama-tts`` auto-fetches OuteTTS.

This is the multi-voice engine that complements :mod:`stabbur.tts` (single-voice
OuteTTS via ``llama-tts``).
"""

import importlib.util
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel

# Pinned kokoro-onnx "model-files" release: the fp32 model + combined voices .npz.
# fp32 is used over int8 because on CPU it is both faster and higher quality here
# (int8 quant ops aren't accelerated); it's a one-time ~310 MB fetch.
_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
_MODEL_FILE = "kokoro-v1.0.onnx"
_VOICES_FILE = "voices-v1.0.bin"

# The speed multipliers the engine actually accepts. kokoro-onnx enforces this range itself and
# raises a bare ValueError outside it, which every caller has to anticipate — so the bound lives
# here, next to the engine it belongs to, and callers validate against it before synthesizing
# rather than each inventing a range of its own (they disagreed: 0.25 was documented, 0.5 works).
SPEED_MIN = 0.5
SPEED_MAX = 2.0

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
    """Where the Kokoro model + voices live — inside the library in play, so they travel with it.

    The *first resolved* library root, not the machine default: inside a project that is the
    project's own store, which is what makes a self-contained project able to speak at all. Sent
    to the machine library instead, a project could be moved to a machine with no library and its
    Listen button would have nothing to synthesize with.

    Raises ``LibraryNotConfigured`` when no library is set (rather than using ``./data``).
    """
    from stabbur import library  # noqa: PLC0415 - lazy to avoid an import cycle

    return library.roots()[0] / "tts" / "kokoro"


def assets_present() -> bool:
    """Whether the model + voices have already been downloaded."""
    d = _assets_dir()
    return (d / _MODEL_FILE).is_file() and (d / _VOICES_FILE).is_file()


def _download(url: str, dest: Path) -> None:
    """Stream ``url`` to ``dest``, staged through a **per-download unique** temp file.

    The staging name must be unique (the same reason :mod:`stabbur.fsatomic` uses one): a fixed
    ``<name>.part`` is a shared mutable file, so two downloads running at once — two first-use
    requests, the startup pre-warm racing the first Listen click, or a second stabbur process on
    the same library — interleave their chunks into it and leave a truncated model behind, or
    trip over each other's rename. With a unique temp each writer owns its own bytes and the
    final :meth:`Path.replace` is atomic, so whoever lands last wins and every reader sees a
    complete file.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, staged = tempfile.mkstemp(dir=dest.parent, prefix=f"{dest.name}.", suffix=".part")
    tmp = Path(staged)
    try:
        with os.fdopen(fd, "wb") as fh, httpx.stream("GET", url, follow_redirects=True, timeout=300.0) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes(1 << 16):
                fh.write(chunk)
        tmp.replace(dest)
    finally:
        # A no-op after a successful replace; on any failure it clears the partial download
        # instead of leaving a stray .part next to the model.
        tmp.unlink(missing_ok=True)


# Serializes the (~310 MB) first-use fetch. Two threads that both find the assets missing would
# otherwise each start a full download: twice the bytes over the wire for one usable result.
_assets_lock = threading.Lock()


def ensure_assets(root: Path | None = None) -> tuple[Path, Path]:
    """Return ``(model, voices)`` paths, downloading them on first use (~310 MB).

    ``root`` overrides the library they land in — used when scaffolding a project, where the
    destination is the project's own store and the project is not the one being run yet.

    Thread-safe: the presence check and the fetch happen under one lock, so a caller that
    arrives while another thread is downloading waits and then finds the finished files
    rather than starting a second download of its own.
    """
    d = (root / "tts" / "kokoro") if root is not None else _assets_dir()
    model, vox = d / _MODEL_FILE, d / _VOICES_FILE
    with _assets_lock:
        if not model.is_file():
            _download(f"{_RELEASE}/{_MODEL_FILE}", model)
        if not vox.is_file():
            _download(f"{_RELEASE}/{_VOICES_FILE}", vox)
    return model, vox


# Cache the loaded engine (loading the ONNX takes ~1 s); reused across requests.
# A plain ``threading.Lock`` (not an asyncio one): every caller reaches this from a worker
# thread, since synthesis is blocking and the HTTP layer dispatches it with ``to_thread``.
_engine: Any = None
_engine_lock = threading.Lock()


def _build_engine() -> Any:
    """Fetch the assets if needed and construct the ONNX engine (the body of the cached init)."""
    import espeakng_loader  # noqa: PLC0415
    from kokoro_onnx import Kokoro  # noqa: PLC0415
    from kokoro_onnx.config import EspeakConfig  # noqa: PLC0415

    model, vox = ensure_assets()
    # Point kokoro at the bundled espeak-ng (no system binary needed).
    espeak = EspeakConfig(
        lib_path=espeakng_loader.get_library_path(),
        data_path=espeakng_loader.get_data_path(),
    )
    return Kokoro(str(model), str(vox), espeak_config=espeak)


def _get_engine() -> Any:
    """The cached Kokoro engine, created once on first use (downloading its assets if needed).

    Thread-safe by double-checked init: the fast path is a plain read of the already-built
    engine, and only the build runs under the lock. Without it, concurrent first uses each
    run the asset download and each construct an ONNX session — two ~310 MB fetches racing
    into one destination and two copies of the model resident in RAM.
    """
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            _engine = _build_engine()
    return _engine


_VOICE_IDS_SET = frozenset(_VOICE_IDS)


def synthesize(text: str, voice: str, out_path: Path | None = None, *, speed: float = 1.0) -> Path:
    """Generate a speech WAV for ``text`` in the built-in ``voice`` at ``speed`` (1.0 = normal).

    Downloads the model on first use and loads it once (cached). Blocking — call
    from a worker thread in async contexts. Returns the written WAV path.

    Raises:
        RuntimeError: If the extra isn't installed, the voice is unknown, the text is empty,
            the speed is out of range, or synthesis produces no audio.
    """
    if not available():
        raise RuntimeError("Kokoro TTS is unavailable — reinstall stabbur's dependencies (`uv sync`).")
    if voice not in _VOICE_IDS_SET:
        raise RuntimeError(f"unknown Kokoro voice {voice!r}")
    if not text.strip():
        raise RuntimeError("nothing to speak (empty text)")
    # Check the range here too, not only in each caller: out of range the engine raises ValueError,
    # which is not what this function documents and reached the CLI as a Rich traceback.
    if not SPEED_MIN <= speed <= SPEED_MAX:
        raise RuntimeError(f"speed must be between {SPEED_MIN} and {SPEED_MAX} (got {speed:g})")

    # Generate *before* creating the output file: engine load, the first-use download and
    # generation itself can all fail, and a temp created up front would be orphaned by every
    # one of those failures — one leaked WAV per failed request, for the life of the process.
    samples, sample_rate = _get_engine().create(text, voice=voice, speed=speed, lang=lang_code(voice))
    if out_path is not None:
        return _write_wav(out_path, samples, sample_rate)
    # NamedTemporaryFile closes its fd on __exit__ (mkstemp leaks it); delete=False keeps the file.
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        out = Path(f.name)
    written = False
    try:
        result = _write_wav(out, samples, sample_rate)
        written = True
        return result
    finally:
        if not written:  # a failed write leaves no temp behind either; a caller's own path is theirs
            out.unlink(missing_ok=True)


def _write_wav(out: Path, samples: Any, sample_rate: int) -> Path:
    """Write generated samples to ``out`` as WAV, raising if nothing landed there."""
    import soundfile as sf  # noqa: PLC0415

    sf.write(str(out), samples, sample_rate)
    if not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError("Kokoro synthesis produced no audio")
    return out
