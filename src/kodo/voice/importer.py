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
    if not presence.in_cache or presence.cache_path is None:
        raise FileNotFoundError(f"{presence.spec.repo} is not in the HF cache ({hf_hub_cache()}); nothing to import")
    snapshot = _cache_snapshot(presence.cache_path)
    if snapshot is None:
        raise FileNotFoundError(f"no snapshot found in the HF cache for {presence.spec.repo}")

    # Copy the cached snapshot, resolving its symlinks-into-blobs to real files, so the
    # library holds a self-contained portable copy (symlinks=False follows the links).
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(snapshot, dest, symlinks=False, dirs_exist_ok=True)
    copied = _dir_size(dest)

    pruned = False
    # Prune the cache only once the library copy holds essentially all the bytes (a verified
    # copy). The 0.95 slack covers tiny cache-only metadata (refs/) the snapshot copy omits.
    if prune_cache and presence.cache_bytes and copied >= presence.cache_bytes * 0.95:
        shutil.rmtree(presence.cache_path, ignore_errors=True)
        pruned = True
    return ImportResult(repo=presence.spec.repo, dest=dest, copied_bytes=copied, cache_pruned=pruned)


def _cache_snapshot(cache_repo_dir: Path) -> Path | None:
    """The current snapshot dir for a cached repo (``snapshots/<commit>``), or None."""
    snapshots = cache_repo_dir / "snapshots"
    if not snapshots.is_dir():
        return None
    ref = cache_repo_dir / "refs" / "main"
    if ref.is_file():
        pinned = snapshots / ref.read_text().strip()
        if pinned.is_dir():
            return pinned
    dirs = [d for d in snapshots.iterdir() if d.is_dir()]
    return max(dirs, key=lambda d: d.stat().st_mtime) if dirs else None


def prune_cache_repo(repo: str) -> int:
    """Delete a repo's HF-cache directory (e.g. a redundant duplicate). Returns bytes freed."""
    directory = hf_hub_cache() / f"models--{repo.replace('/', '--')}"
    if not directory.is_dir():
        return 0
    freed = _dir_size(directory)
    shutil.rmtree(directory, ignore_errors=True)
    return freed
