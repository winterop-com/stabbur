"""Move voice models from the HF cache into the library's portable ``voice/`` bucket.

``hf download`` drops models in ``~/.cache/huggingface`` — machine-local and unorganized.
Importing copies a model's snapshot into ``<library_root>/voice/<repo>`` so it travels with
the drive, then (optionally) prunes the cache copy to reclaim space on the Mac. A copy is
verified (byte total) before its cache source is deleted — never delete before a good copy.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from kodo.voice.catalog import VoicePresence, _dir_size, hf_hub_cache, voice_dir


class ImportResult(BaseModel):
    """Outcome of importing one voice model."""

    model_config = ConfigDict(frozen=True)

    repo: str
    dest: Path
    copied_bytes: int
    already_present: bool = False
    cache_pruned: bool = False


def import_to_library(presence: VoicePresence, library_root: Path | str, prune_cache: bool = False) -> ImportResult:
    """Copy a cached voice model into the library; optionally delete the cache copy after.

    Uses ``huggingface_hub.snapshot_download`` with ``local_files_only`` so it only copies
    from the existing cache (no network). Skips work if the model is already in the library.
    """
    dest = voice_dir(library_root) / presence.spec.repo
    if presence.in_library:
        return ImportResult(
            repo=presence.spec.repo, dest=dest, copied_bytes=presence.library_bytes, already_present=True
        )
    if not presence.in_cache:
        raise FileNotFoundError(f"{presence.spec.repo} is not in the HF cache ({hf_hub_cache()}); nothing to import")

    from huggingface_hub import snapshot_download  # noqa: PLC0415 - heavy import, only when importing

    dest.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=presence.spec.repo, local_dir=str(dest), local_files_only=True)
    copied = _dir_size(dest)

    pruned = False
    # Only prune the cache once the library copy is at least as large as the cache source.
    if prune_cache and presence.cache_path and presence.cache_bytes and copied >= presence.cache_bytes:
        shutil.rmtree(presence.cache_path, ignore_errors=True)
        pruned = True
    return ImportResult(repo=presence.spec.repo, dest=dest, copied_bytes=copied, cache_pruned=pruned)


def prune_cache_repo(repo: str) -> int:
    """Delete a repo's HF-cache directory (e.g. a redundant duplicate). Returns bytes freed."""
    directory = hf_hub_cache() / f"models--{repo.replace('/', '--')}"
    if not directory.is_dir():
        return 0
    freed = _dir_size(directory)
    shutil.rmtree(directory, ignore_errors=True)
    return freed
