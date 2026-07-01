"""Scan and resolve models stored in the on-drive library (``library_root``).

Finds runnable models wherever they landed under the library root:

* directory-based models (``gguf/``, ``mlx/``, ``safetensors/``, ``huggingface/``
  — any directory holding ``*.gguf`` or ``*.safetensors`` files);
* Ollama's native content-addressed store (``ollama/manifests`` + ``ollama/blobs``).

This is distinct from :mod:`kodo.catalog`, which lists the local *source*
stores that models are pulled *from*.
"""

from pathlib import Path

from pydantic import BaseModel, computed_field

from kodo import arch
from kodo.config import get_settings
from kodo.models import ModelFormat, _human_size
from kodo.sources import ollama
from kodo.sources.base import dir_stats

# Top-level directories whose name is a layout prefix, stripped from model names.
_PREFIXES = {"gguf", "mlx", "safetensors", "huggingface", "tts", "other"}

# Preferred GGUF quant when a repo ships several, most-preferred first.
_QUANT_PREFERENCE = ("Q4_K_M", "Q4_K_S", "Q5_K_M", "Q4_0", "Q8_0")


class LibraryModel(BaseModel):
    """A runnable model resolved from the on-drive library."""

    name: str
    model_format: ModelFormat
    generative: bool = True
    """Whether this is a generative chat LLM (vs an embedding/vision encoder)."""

    is_ollama: bool = False
    """True if this lives in the Ollama store — runnable only via Ollama, not kodo."""

    path: Path
    """Where the model lives (a directory, or the Ollama manifest)."""

    load_target: Path
    """What the runtime loads: the main GGUF file, or the MLX model directory."""

    mmproj: Path | None = None
    """Multimodal projector to load alongside, if any."""

    tts: bool = False
    """True if this is a text-to-speech model (served via llama-tts, not chat)."""

    vocoder: Path | None = None
    """The paired vocoder GGUF for a TTS model (e.g. WavTokenizer)."""

    languages: list[str] = []
    """Languages a TTS model supports (BCP-47-ish codes), for voice/language selection."""

    size_bytes: int = 0
    file_count: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def size_human(self) -> str:
        """Human-readable size of the model on disk."""
        return _human_size(self.size_bytes)


def _weights(model_dir: Path, suffix: str) -> list[Path]:
    """Glob ``*<suffix>`` in ``model_dir``, excluding macOS ``._`` AppleDouble files."""
    return [p for p in model_dir.glob(f"*{suffix}") if not p.name.startswith("._")]


def _clean_name(rel: Path) -> str:
    """Drop a leading layout-prefix component from a relative model path."""
    parts = rel.parts
    if parts and parts[0] in _PREFIXES:
        parts = parts[1:]
    return "/".join(parts)


def _classify_dir(model_dir: Path) -> ModelFormat:
    """Classify a directory by the weight files it contains."""
    if _weights(model_dir, ".gguf"):
        return ModelFormat.gguf
    if _weights(model_dir, ".safetensors"):
        parent = model_dir.parts
        return ModelFormat.mlx if "mlx" in parent or "mlx-community" in parent else ModelFormat.safetensors
    return ModelFormat.unknown


# Filename hints for a vocoder GGUF (paired with a TTS model, e.g. WavTokenizer).
_VOCODER_HINTS = ("wavtokenizer", "vocoder")

# TTS models supporting more than English, by name substring → language codes.
# OuteTTS 0.1 is English-only; 0.2 adds zh/ja/ko; 0.3 adds more.
_TTS_LANGUAGES = {
    "outetts-0.3": ["en", "zh", "ja", "ko", "de", "fr", "es", "it", "nl", "pt", "pl", "ar"],
    "outetts-0.2": ["en", "zh", "ja", "ko"],
    "outetts_0.3": ["en", "zh", "ja", "ko", "de", "fr", "es", "it", "nl", "pt", "pl", "ar"],
    "outetts_0.2": ["en", "zh", "ja", "ko"],
}


def _find_vocoder(ggufs: list[Path]) -> Path | None:
    """The vocoder GGUF among ``ggufs`` (by filename hint), if any."""
    return next((g for g in ggufs if any(h in g.name.lower() for h in _VOCODER_HINTS)), None)


def _tts_languages(name: str) -> list[str]:
    """Languages a TTS model supports, inferred from its name (default English)."""
    low = name.lower()
    for key, langs in _TTS_LANGUAGES.items():
        if key in low:
            return langs
    return ["en"]


def _pick_weight(weights: list[Path]) -> Path:
    """Pick the best single weight: split-shard head, else preferred quant, else largest."""
    shard = next((g for g in weights if "00001-of-" in g.name), None)
    if shard is not None:
        return shard
    for quant in _QUANT_PREFERENCE:
        match = next((g for g in weights if quant.lower() in g.name.lower()), None)
        if match is not None:
            return match
    return max(weights, key=lambda p: p.stat().st_size)


def pick_gguf(model_dir: Path) -> tuple[Path, Path | None]:
    """Pick the main GGUF (+ optional mmproj) from a directory of ``*.gguf`` files.

    Prefers a balanced quant when several are present; falls back to the first
    shard of a split model, else the largest file. Returns ``(main, mmproj)``.
    """
    ggufs = sorted(_weights(model_dir, ".gguf"))
    mmproj = next((g for g in ggufs if g.name.lower().startswith("mmproj")), None)
    weights = [g for g in ggufs if g != mmproj]
    if not weights:
        raise FileNotFoundError(f"No .gguf weights in {model_dir}")
    return _pick_weight(weights), mmproj


def _scan_dirs(base: Path) -> list[LibraryModel]:
    """Scan directory-based models anywhere under ``base`` (excluding ollama/)."""
    ollama_dir = base / "ollama"
    dirs: set[Path] = set()
    for pattern in ("*.gguf", "*.safetensors"):
        for weights in base.rglob(pattern):
            if weights.name.startswith("._"):
                continue  # macOS AppleDouble junk on exFAT
            parent = weights.parent
            if parent == ollama_dir or ollama_dir in parent.parents:
                continue  # ollama blobs are scanned natively below
            dirs.add(parent)

    models: list[LibraryModel] = []
    for model_dir in sorted(dirs):
        fmt = _classify_dir(model_dir)
        name = _clean_name(model_dir.relative_to(base))
        size_bytes, file_count = dir_stats(model_dir)

        if fmt is ModelFormat.gguf:
            ggufs = sorted(_weights(model_dir, ".gguf"))
            vocoder = _find_vocoder(ggufs)
            if vocoder is not None:
                # TTS setup: a model GGUF paired with a vocoder. The vocoder alone
                # isn't a runnable model, so a dir with only a vocoder is skipped.
                mains = [g for g in ggufs if g != vocoder and not g.name.lower().startswith("mmproj")]
                if not mains:
                    continue
                models.append(
                    LibraryModel(
                        name=name,
                        model_format=fmt,
                        generative=False,  # not a chat model — served via llama-tts
                        tts=True,
                        path=model_dir,
                        load_target=_pick_weight(mains),
                        vocoder=vocoder,
                        languages=_tts_languages(name),
                        size_bytes=size_bytes,
                        file_count=file_count,
                    )
                )
                continue
            try:
                load_target, mmproj = pick_gguf(model_dir)
            except FileNotFoundError:
                # No usable weight yet — an in-progress download (only an mmproj or
                # .incomplete files present) or a broken dir. Skip until it's whole.
                continue
        else:
            load_target, mmproj = model_dir, None

        models.append(
            LibraryModel(
                name=name,
                model_format=fmt,
                generative=arch.is_generative(fmt, model_dir),
                path=model_dir,
                load_target=load_target,
                mmproj=mmproj,
                size_bytes=size_bytes,
                file_count=file_count,
            )
        )
    return models


def _scan_ollama(base: Path) -> list[LibraryModel]:
    """Surface Ollama models from their native store as runnable GGUF entries."""
    ollama_dir = base / "ollama"
    models: list[LibraryModel] = []
    for name, manifest in ollama.manifest_names(ollama_dir):
        model_blob, mmproj_blob = ollama.weight_blobs(manifest, ollama_dir)
        if model_blob is None or not model_blob.is_file():
            continue
        size_bytes = model_blob.stat().st_size + (mmproj_blob.stat().st_size if mmproj_blob else 0)
        models.append(
            LibraryModel(
                name=name,
                model_format=ModelFormat.gguf,
                is_ollama=True,
                path=manifest,
                load_target=model_blob,
                mmproj=mmproj_blob,
                size_bytes=size_bytes,
                file_count=1 + (1 if mmproj_blob else 0),
            )
        )
    return models


def _scan_root(base: Path) -> list[LibraryModel]:
    """Scan a single library root (no-op if it doesn't exist / drive unplugged)."""
    if not base.is_dir():
        return []
    return _scan_dirs(base) + _scan_ollama(base)


def scan(root: Path | None = None) -> list[LibraryModel]:
    """Return every runnable model in the library.

    With no ``root``, the library spans the main ``library_root`` (often an
    external drive) **plus** the always-local ``local_root`` — so locally-kept
    models still appear when the drive is unplugged. Deduped by (name, format)
    so format variants coexist (drive wins on a tie). A single ``root`` is
    honored as-is (used by tests).
    """
    if root is not None:
        return _scan_root(root)

    settings = get_settings()
    models: list[LibraryModel] = []
    seen: set[tuple[str, ModelFormat]] = set()
    for base in (settings.library_root, settings.local_root):
        for m in _scan_root(base):
            # Key on (name, format): the same model in the same format on both
            # roots is one entry (drive wins), but a GGUF on the drive and an MLX
            # copy locally are distinct runnable artifacts and must both survive
            # so ``find(..., model_format=...)`` can disambiguate them.
            key = (m.name, m.model_format)
            if key not in seen:
                seen.add(key)
                models.append(m)
    return models


def tts_models(root: Path | None = None) -> list[LibraryModel]:
    """Every text-to-speech model in the library (model + paired vocoder)."""
    return [m for m in scan(root) if m.tts]


def find(query: str, root: Path | None = None, model_format: ModelFormat | None = None) -> list[LibraryModel]:
    """Find library models matching ``query`` (full name or bare repo/tag name)."""
    q = query.lower()
    return [
        m
        for m in scan(root)
        if q in (m.name.lower(), m.name.rsplit("/", 1)[-1].lower())
        and (model_format is None or m.model_format is model_format)
    ]
