"""Scanning the library roots into LibraryModel records, and finding models by name."""

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

from stabbur import arch
from stabbur.library._model import (
    LibraryModel,
    ModelRef,
    _classify_dir,
    _clean_name,
    _find_vocoder,
    _pick_weight,
    _tts_languages,
    _voice_spec,
    _weights,
    pick_gguf,
)
from stabbur.library._roots import roots
from stabbur.models import ModelFormat
from stabbur.sources import ollama
from stabbur.sources.base import dir_stats

_T = TypeVar("_T")


def _isolated(build: Callable[[_T], LibraryModel | None], items: Iterable[_T]) -> list[LibraryModel]:
    """Build a :class:`LibraryModel` per item, isolating failures so one bad model can't crash a scan.

    The three bucket scanners (``voice/``, format dirs, ``ollama/``) all funnel their per-item
    construction through here (A3): a model whose files are corrupt/half-written/unreadable is
    **skipped** (a per-item exception or a ``None`` return), never propagated — so ``scan()`` always
    returns the *healthy* models rather than raising and taking down the whole library listing.
    """
    out: list[LibraryModel] = []
    for item in items:
        try:
            model = build(item)
        except Exception:  # noqa: BLE001 - fault isolation: a single unreadable model must not crash scan()
            continue
        if model is not None:
            out.append(model)
    return out


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

    repos = [repo for publisher in _dirs(voice_bucket) for repo in _dirs(publisher)]
    return _isolated(_voice_model, repos)


def _voice_model(repo: Path) -> LibraryModel:
    """Build a voice-bucket model from its ``voice/<publisher>/<repo>`` dir."""
    name = f"{repo.parent.name}/{repo.name}"
    spec = _voice_spec(name)
    kind = spec.kind.value if spec else "tts"
    size_bytes, file_count = dir_stats(repo)
    return LibraryModel(
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


def _scan_dirs(base: Path) -> list[LibraryModel]:
    """Scan directory-based models anywhere under ``base`` (excluding ollama/ and voice/)."""
    ollama_dir = base / "ollama"
    voice_dir = base / "voice"
    excluded = (ollama_dir, voice_dir)
    dirs: set[Path] = set()
    for pattern in ("*.gguf", "*.safetensors"):
        for weights in base.rglob(pattern):
            if weights.name.startswith("._"):
                continue  # macOS AppleDouble junk on exFAT
            parent = weights.parent
            if parent in excluded or any(d in parent.parents for d in excluded):
                continue  # ollama blobs scanned natively below; voice/ scanned by _scan_voice
            rel = parent.relative_to(base)
            # Skip anything under a dot-dir: the redirected HF cache (.cache), stabbur sidecars
            # (.stabbur), and interrupted-pull staging (.stabbur-stage-*) are not library models.
            if any(part.startswith(".") for part in rel.parts):
                continue
            dirs.add(parent)

    return _isolated(lambda d: _model_from_dir(d, base), sorted(dirs))


def _model_from_dir(model_dir: Path, base: Path) -> LibraryModel | None:
    """Build a directory-based model (GGUF / MLX / safetensors), or ``None`` to skip the dir."""
    name = _clean_name(model_dir.relative_to(base))
    if not name:
        return None  # a loose weight at a bucket/library root — no model identity; skip (never rm-able)
    fmt = _classify_dir(model_dir)
    size_bytes, file_count = dir_stats(model_dir)

    if fmt is ModelFormat.gguf:
        ggufs = sorted(_weights(model_dir, ".gguf"))
        vocoder = _find_vocoder(ggufs)
        if vocoder is not None:
            # TTS setup: a model GGUF paired with a vocoder. The vocoder alone
            # isn't a runnable model, so a dir with only a vocoder is skipped.
            mains = [g for g in ggufs if g != vocoder and not g.name.lower().startswith("mmproj")]
            if not mains:
                return None
            return LibraryModel(
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
        try:
            load_target, mmproj = pick_gguf(model_dir)
        except FileNotFoundError:
            # No usable weight yet — an in-progress download (only an mmproj or
            # .incomplete files present) or a broken dir. Skip until it's whole.
            return None
    else:
        load_target, mmproj = model_dir, None

    return LibraryModel(
        name=name,
        model_format=fmt,
        generative=arch.is_generative(fmt, model_dir),
        path=model_dir,
        load_target=load_target,
        mmproj=mmproj,
        size_bytes=size_bytes,
        file_count=file_count,
    )


def _scan_ollama(base: Path) -> list[LibraryModel]:
    """Surface Ollama models from their native store as runnable GGUF entries."""
    ollama_dir = base / "ollama"
    return _isolated(lambda nm: _ollama_model(nm, ollama_dir), ollama.manifest_names(ollama_dir))


def _ollama_model(name_manifest: tuple[str, Path], ollama_dir: Path) -> LibraryModel | None:
    """Build a runnable GGUF entry for one Ollama manifest, or ``None`` if its weight is missing."""
    name, manifest = name_manifest
    model_blob, mmproj_blob = ollama.weight_blobs(manifest, ollama_dir)
    if model_blob is None or not model_blob.is_file():
        return None
    # A referenced-but-missing projector blob (partial backup / corruption) shouldn't crash the
    # scan — drop it and surface the model text-only rather than raising FileNotFoundError.
    if mmproj_blob is not None and not mmproj_blob.is_file():
        mmproj_blob = None
    size_bytes = model_blob.stat().st_size + (mmproj_blob.stat().st_size if mmproj_blob else 0)
    return LibraryModel(
        name=name,
        model_format=ModelFormat.gguf,
        is_ollama=True,
        path=manifest,
        load_target=model_blob,
        mmproj=mmproj_blob,
        size_bytes=size_bytes,
        file_count=1 + (1 if mmproj_blob else 0),
    )


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


def scan(root: Path | None = None) -> list[LibraryModel]:
    """Return every runnable model across the resolved libraries (see :func:`roots`).

    Scanned in priority order and deduped by :class:`ModelRef` (name + format): a project-local
    copy wins over the shared one on a tie (so loads come from the closer library), while a GGUF
    in one library and an MLX build in another both survive (distinct artifacts). Per-model faults
    are isolated (:func:`_isolated`), so a corrupt model on disk is skipped, never raised. A single
    ``root`` is honored as-is (used by tests).
    """
    bases = [root] if root is not None else roots()
    models: list[LibraryModel] = []
    seen: set[ModelRef] = set()
    for base in bases:
        for m in _scan_root(base):
            # Key on the model's ModelRef (name + format): the same model+format in two libraries
            # is one entry (first/closer wins), but a GGUF vs an MLX copy are distinct runnable
            # artifacts and must both survive for --format to disambiguate.
            if m.ref not in seen:
                seen.add(m.ref)
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


def find(query: str, root: Path | None = None, model_format: ModelFormat | None = None) -> list[LibraryModel]:
    """Find library models matching ``query`` (full name or bare repo/tag name)."""
    q = query.lower()
    return [
        m
        for m in scan(root)
        if q in (m.name.lower(), m.name.rsplit("/", 1)[-1].lower())
        and (model_format is None or m.model_format is model_format)
    ]
