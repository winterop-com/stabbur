"""Filesystem helpers shared by the source-store adapters."""

import shutil
import tempfile
from collections.abc import Iterator
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

# Read size for the byte-for-byte compare. Model weights run to many gigabytes on a slow
# external drive, so the comparison streams a chunk at a time and never holds a file in memory.
_COMPARE_CHUNK = 1 << 20  # 1 MiB


def _is_bookkeeping(child: Path, root: Path) -> bool:
    """Whether a walked path is bookkeeping rather than model content.

    The skip-dir match is made against the path **relative to** ``root``, never its absolute
    parts: a library whose own root sits under a ``.cache/`` or ``.stabbur/`` ancestor (e.g. a
    project with ``libraries = [".stabbur/library"]``) would otherwise have every one of its
    models measured as zero files — which in turn makes :func:`copy_verified` vacuously true.
    """
    if child.name.startswith("._"):
        return True
    try:
        relative = child.relative_to(root)
    except ValueError:  # vanished/renamed mid-walk; not something we can attribute to root
        return True
    return bool(_STATS_SKIP_DIRS.intersection(relative.parts))


def _walk_real_files(path: Path) -> Iterator[Path]:
    """Yield the model-content files under ``path`` (skipping ``.cache``/``.stabbur``/``._``).

    Symlinks are followed when they resolve (the HF cache stores real blobs behind symlinked
    snapshots); broken links and files that vanish mid-walk are skipped. This is the single
    walker behind both :func:`dir_stats` and :func:`copy_verified`, so the side that *measures*
    a tree and the side that *verifies* it can never disagree about which files count.
    """
    for child in path.rglob("*"):
        if _is_bookkeeping(child, path):
            continue
        try:
            if child.is_file():
                yield child
        except OSError:
            continue


def _real_file_sizes(path: Path) -> dict[str, int]:
    """``{relative_posix_path: size}`` of a tree's model-content files."""
    out: dict[str, int] = {}
    for child in _walk_real_files(path):
        try:
            out[child.relative_to(path).as_posix()] = child.stat().st_size
        except OSError:
            continue
    return out


def _same_bytes(left: Path, right: Path) -> bool:
    """Whether two files hold identical bytes, compared a chunk at a time."""
    try:
        with left.open("rb") as lhs, right.open("rb") as rhs:
            while True:
                chunk = lhs.read(_COMPARE_CHUNK)
                if chunk != rhs.read(_COMPARE_CHUNK):
                    return False
                if not chunk:
                    return True
    except OSError:
        return False


def copy_verified(src: Path, dest: Path) -> bool:
    """Whether ``dest`` is a byte-for-byte copy of every model-content file under ``src``.

    This is the gate that licenses deleting the *only other* copy of a model (``pull --move``,
    the migration's dedup branch), so it is deliberately unforgiving:

    * both sides must be existing, distinct directories — a dest that is the src, or isn't
      there, verifies nothing;
    * ``src`` must actually hold content. An empty tree, or one whose files are all zero bytes,
      returns False rather than "everything matched" — otherwise nothing-vs-nothing would read
      as a successful copy and license the delete;
    * the relative-path/size maps must match exactly (a missing, extra or truncated file fails);
    * every pair is then compared byte for byte, streamed in chunks — same-size-different-bytes
      (a corrupt copy on the no-journaling exFAT target) fails too.
    """
    if not src.is_dir() or not dest.is_dir():
        return False
    try:
        if src.resolve() == dest.resolve():
            return False
    except OSError:
        return False
    src_files = _real_file_sizes(src)
    if not src_files or not any(src_files.values()):
        return False
    if src_files != _real_file_sizes(dest):
        return False
    return all(_same_bytes(src / relative, dest / relative) for relative in src_files)


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
    sizes = _real_file_sizes(path)
    return (sum(sizes.values()), len(sizes))


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
