"""Scan and resolve models stored in the on-drive library (``backup_root``).

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
_PREFIXES = {"gguf", "mlx", "safetensors", "huggingface", "other"}

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

    shard = next((g for g in weights if "00001-of-" in g.name), None)
    if shard is not None:
        return shard, mmproj
    for quant in _QUANT_PREFERENCE:
        match = next((g for g in weights if quant.lower() in g.name.lower()), None)
        if match is not None:
            return match, mmproj
    return max(weights, key=lambda p: p.stat().st_size), mmproj


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
        size_bytes, file_count = dir_stats(model_dir)
        if fmt is ModelFormat.gguf:
            load_target, mmproj = pick_gguf(model_dir)
        else:
            load_target, mmproj = model_dir, None
        models.append(
            LibraryModel(
                name=_clean_name(model_dir.relative_to(base)),
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


def scan(root: Path | None = None) -> list[LibraryModel]:
    """Scan the library root and return every runnable model found."""
    base = root or get_settings().backup_root
    if not base.is_dir():
        return []
    return _scan_dirs(base) + _scan_ollama(base)


def find(query: str, root: Path | None = None, model_format: ModelFormat | None = None) -> list[LibraryModel]:
    """Find library models matching ``query`` (full name or bare repo/tag name)."""
    q = query.lower()
    return [
        m
        for m in scan(root)
        if q in (m.name.lower(), m.name.rsplit("/", 1)[-1].lower())
        and (model_format is None or m.model_format is model_format)
    ]
