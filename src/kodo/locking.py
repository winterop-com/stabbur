"""Inter-process locking for library mutations.

kodo is multi-process in practice: the CLI and ``kodo serve`` are expected to run at the same
time against the same library (e.g. you tag a model in the terminal while the web UI's
``/api/tags`` writes the same file). Nothing else in the tree does inter-process locking, so
concurrent read-modify-write mutations can lose updates (S-M4) and destructive ops can race.

A single advisory file lock per library — ``<root>/.kodo/lock`` — serializes those short
critical sections across processes. It is **best-effort**: a platform without ``fcntl`` or a
filesystem that rejects the lock degrades to a no-op rather than blocking work (kodo already
uses atomic + durable writes, so the worst case without the lock is the pre-existing
lost-update, not a corrupt file). Long-running work (a multi-GB pull) is deliberately *not*
held under this lock — pulls stage into distinct model dirs with an atomic final move, so they
don't corrupt each other, and serializing a download behind a quick tag edit would be worse.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX (Windows); lock degrades to a no-op
    fcntl = None  # type: ignore[assignment]

_LOCK_FILENAME = "lock"


@contextmanager
def library_lock(root: Path) -> Generator[None]:
    """Hold an exclusive inter-process lock on ``root`` for the duration of the block.

    Wrap a read-modify-write (tags) or a destructive op (remove/migrate) so a second kodo
    process on the same library waits rather than interleaving. Best-effort: if the lock file
    can't be created or ``fcntl`` is unavailable, the block still runs (unlocked).
    """
    lock_path = root / ".kodo" / _LOCK_FILENAME
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+")
    except OSError:
        yield  # can't create the lock file (read-only/unmounted) — proceed unlocked
        return
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
