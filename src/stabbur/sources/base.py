"""Filesystem helpers shared by the source-store adapters."""

import shutil
import tempfile
from pathlib import Path


def safe_join(root: Path, name: str) -> Path:
    """Join ``name`` under ``root``, rejecting anything that escapes ``root``.

    ``name`` comes from an untrusted caller (an HTTP query, a CLI arg). An absolute
    path or ``..`` segments would otherwise let a pull read/copy/delete directories
    outside the intended root — so resolve the result and require it stays under
    (or equal to) ``root``.

    Raises:
        ValueError: If ``name`` is empty/absolute or resolves outside ``root``.
    """
    if not name or Path(name).is_absolute():
        raise ValueError(f"invalid model name {name!r}: must be a relative path")
    resolved = (root / name).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
        raise ValueError(f"invalid model name {name!r}: escapes {root}")
    return resolved


# Not model content: huggingface_hub's per-download bookkeeping (`.cache/huggingface/`), stabbur's
# own sidecar (`.stabbur/`), and macOS AppleDouble noise (`._*`) on exFAT. Excluding them keeps
# recorded sizes accurate — otherwise the transient `.cache/` blobs+metadata inflate the count.
_STATS_SKIP_DIRS = frozenset({".cache", ".stabbur"})


def _real_file_sizes(path: Path) -> dict[str, int]:
    """``{relative_posix_path: size}`` of a tree's real files (skipping ``.cache``/``.stabbur``/``._``)."""
    out: dict[str, int] = {}
    for child in path.rglob("*"):
        if child.name.startswith("._") or _STATS_SKIP_DIRS.intersection(child.parts):
            continue
        try:
            if child.is_file() and not child.is_symlink():
                out[child.relative_to(path).as_posix()] = child.stat().st_size
        except OSError:
            continue
    return out


def copy_verified(src: Path, dest: Path) -> bool:
    """Whether ``dest`` holds exactly ``src``'s real files (same relative paths and sizes).

    Stronger than an aggregate-byte total — it catches a missing/extra file and a per-file
    truncation, not just a different grand total. Used to gate ``--move`` before deleting the
    source on the no-journaling exFAT target (a same-total but different file set won't pass).
    """
    return _real_file_sizes(src) == _real_file_sizes(dest)


def dir_stats(path: Path) -> tuple[int, int]:
    """Return ``(total_bytes, file_count)`` for a model's real files under ``path``.

    Skips huggingface_hub's ``.cache/`` bookkeeping, stabbur's ``.stabbur/`` sidecar, and macOS
    ``._`` files. Symlinks are followed when they resolve (the HF cache stores real blobs behind
    symlinked snapshots); broken links are skipped.

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
        if child.name.startswith("._") or _STATS_SKIP_DIRS.intersection(child.parts):
            continue
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
    # Stage in a unique hidden temp dir (a sibling of dest, so the final rename is
    # on the same filesystem). Being random and dot-prefixed, it can never collide
    # with or clobber a real model dir (e.g. a repo literally named "Foo.partial"),
    # and it's removed on any failure — no half-copied .partial left on disk.
    tmp = Path(tempfile.mkdtemp(prefix=".stabbur-stage-", dir=dest.parent))
    try:
        staging = tmp / dest.name
        shutil.copytree(src, staging, symlinks=False)  # dest untouched if this fails

        # Publish: move any existing dest aside, swap in the new copy, then drop
        # the old one. dest is never absent for more than a rename.
        previous = tmp / f"{dest.name}.prev"
        if dest.exists():
            dest.rename(previous)
        try:
            staging.rename(dest)
        except OSError:
            if previous.exists() and not dest.exists():
                previous.rename(dest)  # roll back to the previous good backup
            raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dir_stats(dest)
