"""Filesystem helpers shared by the source-store adapters."""

import shutil
from pathlib import Path


def dir_stats(path: Path) -> tuple[int, int]:
    """Return ``(total_bytes, file_count)`` for everything under ``path``.

    Symlinks are followed when they resolve (the Hugging Face cache stores
    real blobs behind symlinked snapshots); broken links are skipped.

    Args:
        path: Directory to walk. A non-directory returns ``(0, 0)``.

    Returns:
        A tuple of the cumulative byte size and number of regular files.
    """
    if not path.is_dir():
        return (0, 0)

    total_bytes = 0
    file_count = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total_bytes += child.stat().st_size
                file_count += 1
        except OSError:
            # Broken symlink or vanished file mid-walk; ignore it.
            continue
    return (total_bytes, file_count)


def copy_tree(src: Path, dest: Path) -> tuple[int, int]:
    """Copy ``src`` into ``dest`` (replacing it) and return its stats.

    The copy is staged into a sibling ``.partial`` directory and only swapped
    into place once complete, so a mid-copy failure leaves any existing backup at
    ``dest`` intact rather than destroying it.

    Args:
        src: Source directory to copy.
        dest: Destination directory; replaced atomically if it already exists.

    Returns:
        A tuple of the copied byte size and number of files.

    Raises:
        FileNotFoundError: If ``src`` does not exist.
    """
    if not src.exists():
        raise FileNotFoundError(src)

    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.parent / f"{dest.name}.partial"
    previous = dest.parent / f"{dest.name}.old"
    for leftover in (staging, previous):
        if leftover.exists():
            shutil.rmtree(leftover)

    # Copy first; the existing dest is untouched if this fails.
    shutil.copytree(src, staging, symlinks=False)

    # Publish: move the old copy aside (atomic rename), swap in the new one, then
    # drop the old copy. dest is never absent for more than a rename.
    if dest.exists():
        dest.rename(previous)
    try:
        staging.rename(dest)
    except OSError:
        if previous.exists() and not dest.exists():
            previous.rename(dest)  # roll back to the previous good backup
        raise
    finally:
        if previous.exists():
            shutil.rmtree(previous)
    return dir_stats(dest)
