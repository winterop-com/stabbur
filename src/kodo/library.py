"""Scan and resolve models stored in the on-drive library (``library_root``).

Finds runnable models wherever they landed under the library root:

* directory-based models (``gguf/``, ``mlx/``, ``safetensors/``, ``huggingface/``
  — any directory holding ``*.gguf`` or ``*.safetensors`` files);
* Ollama's native content-addressed store (``ollama/manifests`` + ``ollama/blobs``).

This is distinct from :mod:`kodo.catalog`, which lists the local *source*
stores that models are pulled *from*.
"""

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, computed_field

from kodo import arch, project
from kodo.config import Settings, get_settings
from kodo.models import ModelFormat, _human_size
from kodo.sources import ollama
from kodo.sources.base import dir_stats

if TYPE_CHECKING:
    from kodo.voice.registry import VoiceModel

# Top-level directories whose name is a layout prefix, stripped from model names.
_PREFIXES = {"gguf", "mlx", "safetensors", "huggingface", "tts", "voice", "other"}

# In a project's ``libraries`` list, this token means "the machine's default
# (shared) library" — so a committed kodo.toml stays portable (no hard-coded path).
SHARED_TOKEN = "@shared"


class LibraryNotConfigured(RuntimeError):
    """No library location is configured — kodo can't do anything useful without one.

    The shared/default library must be set explicitly (``KODO_LIBRARY_ROOT``) rather than
    silently falling back to a CWD-relative ``./data`` (which is meaningless for a globally
    installed CLI). A project (``kodo.toml``) that lists its own ``libraries`` counts as
    configured. Carries a ready-to-print, actionable message.
    """

    HINT = (
        "No library configured. Point kodo at your model library — set KODO_LIBRARY_ROOT, e.g.:\n"
        "  export KODO_LIBRARY_ROOT=/Volumes/LLM/Library\n"
        "or run `kodo project init` to scaffold a project with its own library."
    )

    def __init__(self, message: str = HINT) -> None:
        super().__init__(message)


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

    library_root: Path = Path(".")
    """The library this model was resolved from (owns its tags/metadata)."""

    mmproj: Path | None = None
    """Multimodal projector to load alongside, if any."""

    tts: bool = False
    """True if this is a text-to-speech model (served via llama-tts, not chat)."""

    voice_kind: str = ""
    """For models in the ``voice/`` bucket: ``tts`` or ``stt`` (empty = not a voice model)."""

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


def _is_mlx_quantized(model_dir: Path) -> bool:
    """Whether ``config.json`` carries MLX's affine-quantization marker.

    MLX-quantized repos record ``quantization.mode == "affine"`` (with group_size/bits),
    which HF safetensors quants (gptq/awq/bnb, under ``quantization_config``) don't. This
    identifies MLX weights even outside an ``mlx/`` bucket (e.g. an ``lmstudio-community``
    MLX repo), where the path gives no hint.
    """
    try:
        quant = json.loads((model_dir / "config.json").read_text()).get("quantization")
    except (OSError, ValueError):
        return False
    return isinstance(quant, dict) and quant.get("mode") == "affine"


def _classify_dir(model_dir: Path) -> ModelFormat:
    """Classify a directory by the weight files it contains."""
    if _weights(model_dir, ".gguf"):
        return ModelFormat.gguf
    if _weights(model_dir, ".safetensors"):
        parts = model_dir.parts
        is_mlx = "mlx" in parts or "mlx-community" in parts or _is_mlx_quantized(model_dir)
        return ModelFormat.mlx if is_mlx else ModelFormat.safetensors
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


def _voice_spec(name: str) -> "VoiceModel | None":
    """The registry entry for a voice model by its repo name, if known (lazy import)."""
    from kodo.voice import registry  # noqa: PLC0415 - avoid importing voice on every library import

    return registry.by_repo(name)


def _scan_voice(base: Path) -> list[LibraryModel]:
    """Scan the ``voice/`` bucket at the repo level (``voice/<publisher>/<repo>``).

    A voice model is one repo directory, not split by its nested weight folders (Kokoro's
    ``voices/``, Qwen3-TTS's ``speech_tokenizer/``). Each is a non-generative voice model;
    its kind (tts/stt) comes from the registry when the repo is known, else defaults to tts.
    """
    voice_bucket = base / "voice"
    if not voice_bucket.is_dir():
        return []

    def _dirs(parent: Path) -> list[Path]:
        return sorted(p for p in parent.iterdir() if p.is_dir() and not p.name.startswith("._"))

    models: list[LibraryModel] = []
    for publisher in _dirs(voice_bucket):
        for repo in _dirs(publisher):
            name = f"{publisher.name}/{repo.name}"
            spec = _voice_spec(name)
            kind = spec.kind.value if spec else "tts"
            size_bytes, file_count = dir_stats(repo)
            models.append(
                LibraryModel(
                    name=name,
                    model_format=_classify_dir(repo),
                    generative=False,
                    tts=kind == "tts",
                    voice_kind=kind,
                    path=repo,
                    load_target=repo,
                    size_bytes=size_bytes,
                    file_count=file_count,
                )
            )
    return models


def _scan_dirs(base: Path) -> list[LibraryModel]:
    """Scan directory-based models anywhere under ``base`` (excluding ollama/ and voice/)."""
    ollama_dir = base / "ollama"
    voice_dir = base / "voice"
    dirs: set[Path] = set()
    for pattern in ("*.gguf", "*.safetensors"):
        for weights in base.rglob(pattern):
            if weights.name.startswith("._"):
                continue  # macOS AppleDouble junk on exFAT
            parent = weights.parent
            if parent in (ollama_dir, voice_dir) or ollama_dir in parent.parents or voice_dir in parent.parents:
                continue  # ollama blobs scanned natively below; voice/ scanned by _scan_voice
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
    # Voice first so a model present in both voice/ (new) and the legacy tts/ bucket resolves
    # to its voice-category entry on the (name, format) dedup, not the old tts/ copy.
    models = _scan_voice(base) + _scan_dirs(base) + _scan_ollama(base)
    for m in models:  # record which library each model came from (owns its tags)
        m.library_root = base
    return models


def roots(settings: Settings | None = None) -> list[Path]:
    """The library roots to use, in priority order (first match wins).

    A project (``kodo.toml``) may list ``libraries`` — paths relative to the project
    dir, plus the ``@shared`` token for the machine's default library — which lets a
    project keep its own models *and* use the shared archive. Outside a project (no
    ``libraries``), the single default library (``library_root``) is used. Deduped by
    resolved path, so listing ``@shared`` and the default path doesn't double-scan.
    """
    settings = settings or get_settings()
    proj = project.load()
    entries = proj.libraries if proj and proj.libraries else [SHARED_TOKEN]
    # Strict: the shared/default library must be set explicitly, not the ./data fallback. A
    # project using only its own (project-relative) libraries doesn't need it.
    if SHARED_TOKEN in entries and "library_root" not in settings.model_fields_set:
        raise LibraryNotConfigured
    out: list[Path] = []
    seen: set[Path] = set()
    for entry in entries:
        base = settings.library_root if entry == SHARED_TOKEN else (Path.cwd() / entry).expanduser()
        resolved = base.resolve()
        if resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


def configured(settings: Settings | None = None) -> bool:
    """Whether a usable library location is configured (see :func:`roots`)."""
    try:
        roots(settings)
        return True
    except LibraryNotConfigured:
        return False


def scan(root: Path | None = None) -> list[LibraryModel]:
    """Return every runnable model across the resolved libraries (see :func:`roots`).

    Scanned in priority order and deduped by (name, format): a project-local copy
    wins over the shared one on a tie (so loads come from the closer library), while
    a GGUF in one library and an MLX build in another both survive (distinct
    artifacts). A single ``root`` is honored as-is (used by tests).
    """
    bases = [root] if root is not None else roots()
    models: list[LibraryModel] = []
    seen: set[tuple[str, ModelFormat]] = set()
    for base in bases:
        for m in _scan_root(base):
            # Key on (name, format): the same model+format in two libraries is one
            # entry (first/closer wins), but a GGUF vs an MLX copy are distinct
            # runnable artifacts and must both survive for --format to disambiguate.
            key = (m.name, m.model_format)
            if key not in seen:
                seen.add(key)
                models.append(m)
    return models


def tts_models(root: Path | None = None) -> list[LibraryModel]:
    """Every text-to-speech model in the library (model + paired vocoder)."""
    return [m for m in scan(root) if m.tts]


def find_copies(query: str, model_format: ModelFormat | None = None) -> list[LibraryModel]:
    """Every *physical* copy of a model matching ``query``, across all libraries.

    Unlike :func:`find` (which dedupes to one entry per model), this returns each
    copy on disk — so a model kept in two libraries yields two entries. Used by
    removal, which must delete them all.
    """
    q = query.lower()
    copies: list[LibraryModel] = []
    for base in roots():
        for m in _scan_root(base):
            if q in (m.name.lower(), m.name.rsplit("/", 1)[-1].lower()) and (
                model_format is None or m.model_format is model_format
            ):
                copies.append(m)
    return copies


def remove(model: LibraryModel) -> tuple[int, int]:
    """Delete a library model's files from disk. Returns ``(files_removed, bytes_freed)``.

    Directory-based models (gguf/mlx/…) are removed by deleting their directory.
    Ollama models delegate to :func:`kodo.sources.ollama.remove`, which preserves
    blobs still shared with other installed Ollama models.
    """
    freed, count = model.size_bytes, model.file_count
    if model.is_ollama:
        # The Ollama store root is the manifest's ancestor before ``manifests/``;
        # ollama.remove handles shared-blob safety (it may keep shared layers, so
        # ``freed`` is the model's own weight size — a close upper bound).
        parts = model.path.parts
        models_dir = Path(*parts[: parts.index("manifests")])
        ollama.remove(model.name, models_dir)
        return count, freed

    shutil.rmtree(model.path, ignore_errors=True)
    return count, freed


def find(query: str, root: Path | None = None, model_format: ModelFormat | None = None) -> list[LibraryModel]:
    """Find library models matching ``query`` (full name or bare repo/tag name)."""
    q = query.lower()
    return [
        m
        for m in scan(root)
        if q in (m.name.lower(), m.name.rsplit("/", 1)[-1].lower())
        and (model_format is None or m.model_format is model_format)
    ]
