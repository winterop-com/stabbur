"""Discover which registered voice models are present, and where.

Voice models often arrive via a raw ``hf download`` and land in the Hugging Face cache
(``~/.cache/huggingface/hub``), *not* in kodo's portable library — so they don't travel with
the drive and aren't organized. This module reports, per known model, whether it's in the HF
cache and/or the library's ``voice/`` bucket, and its on-disk size. It's the basis for
``kodo voice list`` and for importing cache -> library.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from kodo.models import _human_size
from kodo.voice.registry import BUILTIN, VoiceModel


def hf_hub_cache() -> Path:
    """The Hugging Face hub cache directory (respects ``HF_HUB_CACHE`` / ``HF_HOME``)."""
    direct = os.environ.get("HF_HUB_CACHE")
    if direct:
        return Path(direct)
    home = os.environ.get("HF_HOME")
    base = Path(home) if home else Path.home() / ".cache" / "huggingface"
    return base / "hub"


def voice_dir(library_root: Path | str) -> Path:
    """The library's voice bucket (``<library_root>/voice``)."""
    return Path(library_root) / "voice"


def _cache_dir(repo: str) -> Path | None:
    directory = hf_hub_cache() / f"models--{repo.replace('/', '--')}"
    return directory if directory.is_dir() else None


def _real_files(path: Path) -> list[Path]:
    """Real (non-symlink, non-macOS-``._``) files under ``path``."""
    return [f for f in path.rglob("*") if f.is_file() and not f.is_symlink() and not f.name.startswith("._")]


def _library_dir(library_root: Path | str, repo: str) -> Path | None:
    """The library dir for ``repo`` only if it actually holds model files (not empty / ``._`` only)."""
    directory = voice_dir(library_root) / repo
    return directory if directory.is_dir() and _real_files(directory) else None


def _dir_size(path: Path) -> int:
    """Real bytes under ``path``.

    Skips symlinks so the HF cache's snapshots/ (symlinks into blobs/) aren't double-counted,
    and macOS ``._`` AppleDouble files; a plain library copy on exFAT has neither issue.
    """
    return sum(f.stat().st_size for f in _real_files(path))


class VoicePresence(BaseModel):
    """Where a known voice model currently lives, if anywhere."""

    model_config = ConfigDict(frozen=True)

    spec: VoiceModel
    in_cache: bool = False
    cache_path: Path | None = None
    cache_bytes: int = 0
    in_library: bool = False
    library_path: Path | None = None
    library_bytes: int = 0

    @property
    def available(self) -> bool:
        """Whether the model is present anywhere kodo can reach it."""
        return self.in_cache or self.in_library

    @property
    def location(self) -> str:
        """A short human label of where it lives."""
        if self.in_library:
            return "library"
        if self.in_cache:
            return "hf-cache"
        return "not downloaded"

    @property
    def size_human(self) -> str:
        """Human on-disk size (library copy preferred, else cache)."""
        return _human_size(self.library_bytes or self.cache_bytes)


def discover(library_root: Path | str) -> list[VoicePresence]:
    """Locate every known voice model in the HF cache and the library's ``voice/`` bucket."""
    presences: list[VoicePresence] = []
    for spec in BUILTIN:
        cache = _cache_dir(spec.repo)
        library = _library_dir(library_root, spec.repo)
        presences.append(
            VoicePresence(
                spec=spec,
                in_cache=cache is not None,
                cache_path=cache,
                cache_bytes=_dir_size(cache) if cache else 0,
                in_library=library is not None,
                library_path=library,
                library_bytes=_dir_size(library) if library else 0,
            )
        )
    return presences
