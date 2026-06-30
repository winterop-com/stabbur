"""Scan and resolve models stored in the on-drive library (``backup_root``).

The library is organized by format: ``<root>/<format>/<publisher>/<repo>/``.
This module reads *that* tree — i.e. the drive — as opposed to
:mod:`local_llm.catalog`, which lists the local source stores (HF cache, Ollama,
LM Studio) that models are imported *from*.
"""

from pathlib import Path

from pydantic import BaseModel, computed_field

from local_llm.config import get_settings
from local_llm.models import ModelFormat, _human_size
from local_llm.sources.base import dir_stats

_FORMAT_DIRS = {fmt.value for fmt in ModelFormat if fmt is not ModelFormat.unknown}


class LibraryModel(BaseModel):
    """A model resolved from the on-drive library."""

    name: str
    """``<publisher>/<repo>`` path within the format directory."""

    model_format: ModelFormat
    path: Path
    """The model's directory in the library."""

    size_bytes: int = 0
    file_count: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def size_human(self) -> str:
        """Human-readable size of the model on disk."""
        return _human_size(self.size_bytes)


def _is_model_dir(path: Path) -> bool:
    """True if ``path`` directly contains weight files."""
    return any(path.glob("*.gguf")) or any(path.glob("*.safetensors"))


def _model_dirs(format_dir: Path) -> list[Path]:
    """Return directories holding weights anywhere under a format directory."""
    found: set[Path] = set()
    for pattern in ("*.gguf", "*.safetensors"):
        found |= {weights.parent for weights in format_dir.rglob(pattern)}
    return sorted(found)


def scan(root: Path | None = None) -> list[LibraryModel]:
    """Scan the library root and return every model found.

    Args:
        root: Override for the library root; defaults to the configured
            ``backup_root``.

    Returns:
        One :class:`LibraryModel` per model directory, across all format dirs.
    """
    base = root or get_settings().backup_root
    if not base.is_dir():
        return []

    models: list[LibraryModel] = []
    for format_dir in sorted(base.iterdir()):
        if not format_dir.is_dir() or format_dir.name not in _FORMAT_DIRS:
            continue
        model_format = ModelFormat(format_dir.name)
        for model_dir in _model_dirs(format_dir):
            size_bytes, file_count = dir_stats(model_dir)
            models.append(
                LibraryModel(
                    name=model_dir.relative_to(format_dir).as_posix(),
                    model_format=model_format,
                    path=model_dir,
                    size_bytes=size_bytes,
                    file_count=file_count,
                )
            )
    return models


def find(query: str, root: Path | None = None, model_format: ModelFormat | None = None) -> list[LibraryModel]:
    """Find library models matching ``query``.

    A model matches if ``query`` equals its full ``<publisher>/<repo>`` name or
    just the final ``<repo>`` component (case-insensitive).

    Args:
        query: Full name or bare repo name to look up.
        root: Override for the library root.
        model_format: Restrict to a single format when set.

    Returns:
        All matching models (may be more than one if the same repo exists in
        multiple formats).
    """
    q = query.lower()
    matches = [
        m
        for m in scan(root)
        if q in (m.name.lower(), m.name.rsplit("/", 1)[-1].lower())
        and (model_format is None or m.model_format is model_format)
    ]
    return matches
