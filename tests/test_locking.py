"""Tests for the per-library inter-process lock (A5)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from heim import locking, tags


def test_library_lock_is_mutually_exclusive(tmp_path: Path) -> None:
    # A second acquirer (here a thread with its own file descriptor, standing in for a second
    # process) must wait until the first releases — proving the flock actually serializes.
    order: list[str] = []
    with locking.library_lock(tmp_path):
        order.append("outer-acquired")

        def worker() -> None:
            with locking.library_lock(tmp_path):
                order.append("inner-acquired")

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.2)  # give the worker time to block on the lock
        order.append("outer-still-held")
    t.join(timeout=2)
    assert order == ["outer-acquired", "outer-still-held", "inner-acquired"]


def test_library_lock_creates_lockfile_and_is_best_effort(tmp_path: Path) -> None:
    with locking.library_lock(tmp_path):
        assert (tmp_path / ".heim" / "lock").exists()
    # A path whose parent can't be created (a file where a dir is expected) degrades to a no-op
    # rather than raising — mutations still run, just unlocked.
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("x")
    with locking.library_lock(not_a_dir):
        pass  # no exception


def test_concurrent_tag_edits_do_not_lose_updates(tmp_path: Path) -> None:
    # Two overlapping read-modify-write tag edits on the same model must both survive (S-M4).
    # Slow the save to force the second edit to start while the first holds the lock.
    real_save = tags.save

    def slow_save(library_root: Path, mapping: dict[str, list[str]]) -> None:
        time.sleep(0.15)
        real_save(library_root, mapping)

    tags.save = slow_save
    try:
        threads = [
            threading.Thread(target=tags.edit_tags, args=(tmp_path, "pub/model", [tag], []))
            for tag in ("alpha", "beta")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3)
    finally:
        tags.save = real_save
    assert sorted(tags.tags_for(tmp_path, "pub/model")) == ["alpha", "beta"]
