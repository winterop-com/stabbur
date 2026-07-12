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

from heim.voice.catalog import VoicePresence, _dir_size, _library_dir, _real_files, discover, hf_hub_cache, voice_dir
from heim.voice.registry import BUILTIN


class ImportResult(BaseModel):
    """Outcome of importing one voice model."""

    model_config = ConfigDict(frozen=True)

    repo: str
    dest: Path
    copied_bytes: int
    file_count: int = 0
    already_present: bool = False
    cache_pruned: bool = False
    downloaded: bool = False  # fetched from Hugging Face (not already in the cache)
    copied_from: Path | None = None  # another library it was copied from (fast local copy, no download)


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
    return ImportResult(
        repo=presence.spec.repo, dest=dest, copied_bytes=copied, file_count=len(_real_files(dest)), cache_pruned=pruned
    )


def pull_to_library(
    voice_id: str, library_root: Path | str, *, move: bool = False, token: str | None = None
) -> ImportResult:
    """Acquire a registry voice model into a library's ``voice/`` bucket (project-aware target).

    Resolves ``voice_id`` (a registry short id like ``kokoro``) to its HF repo, then acquires it
    in the cheapest available way: if it's already in this library, no-op; if another reachable
    library (e.g. ``@shared``) has it, **copy it library-to-library** (a fast local/drive copy, no
    download); if it's in the HF cache, copy it in; otherwise **download** it from Hugging Face.
    ``move`` prunes the cache copy after a verified cache import (never touches the source library).
    This is what ``heim library pull voice <id>`` calls, so voice models land in the project-local
    library like chat models do.
    """
    spec = next((s for s in BUILTIN if s.id == voice_id), None)
    if spec is None:
        known = ", ".join(s.id for s in BUILTIN)
        raise ValueError(f"unknown voice model {voice_id!r}; known ids: {known}")

    target = Path(library_root)
    dest = voice_dir(target) / spec.repo

    def _presence() -> VoicePresence:
        return next(p for p in discover(target) if p.spec.id == voice_id)

    presence = _presence()
    if presence.in_library:
        return ImportResult(repo=spec.repo, dest=dest, copied_bytes=presence.library_bytes, already_present=True)

    # Fast path: another reachable library already has it — copy it in (no re-download).
    source = _other_library_with(spec.repo, target)
    if source is not None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, dest, symlinks=False, dirs_exist_ok=True)
        return ImportResult(
            repo=spec.repo,
            dest=dest,
            copied_bytes=_dir_size(dest),
            file_count=len(_real_files(dest)),
            copied_from=source,
        )

    downloaded = False
    if not presence.in_cache:
        from huggingface_hub import snapshot_download  # noqa: PLC0415 - only needed on the download path

        snapshot_download(spec.repo, token=token)  # into the HF cache; import copies it out below
        downloaded = True
        presence = _presence()  # re-scan now that it's cached

    result = import_to_library(presence, target, prune_cache=move)
    return result.model_copy(update={"downloaded": downloaded})


def _other_library_with(repo: str, target: Path) -> Path | None:
    """The ``voice/<repo>`` dir in another reachable library (project-local / ``@shared``), if any.

    Uses :func:`heim.library.roots` to find the libraries in scope, skipping ``target`` itself, so
    a model already on the shared drive is copied rather than re-downloaded.
    """
    from heim.library import roots  # noqa: PLC0415 - lazy: avoid importing library on every voice import

    try:
        reachable = roots()
    except Exception:  # noqa: BLE001 - no library configured -> nothing to copy from; fall back to download
        return None
    target_resolved = target.resolve()
    for root in reachable:
        if root.resolve() == target_resolved:
            continue
        found = _library_dir(root, repo)
        if found is not None:
            return found
    return None


def cached_voice_ids(library_root: Path | str) -> list[str]:
    """Registry ids that are in the HF cache but not yet in this library (for ``pull voice --all``)."""
    return [p.spec.id for p in discover(library_root) if p.in_cache and not p.in_library]


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
