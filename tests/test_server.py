"""Tests for the runtime process manager (no real model runtime needed)."""

import subprocess
import threading
import time
from pathlib import Path

import pytest

from kodo.library import LibraryModel
from kodo.models import ModelFormat
from kodo.server import ServerManager


def _model(path: Path) -> LibraryModel:
    return LibraryModel(name="pub/Foo", model_format=ModelFormat.gguf, path=path, load_target=path)


def test_current_reaps_dead_runtime(tmp_path: Path) -> None:
    # A runtime child that has exited (crash / OOM / killed) is not a loaded
    # model: ``current`` must report None so status and the /v1 proxy do not
    # forward to a dead (or reused) port.
    manager = ServerManager()
    proc = subprocess.Popen(["true"])
    proc.wait()  # child has exited
    manager._proc = proc
    manager._model = _model(tmp_path)

    assert manager.current is None
    assert manager._model is None
    assert manager._proc is None


def test_manager_autopicks_free_port_when_unset() -> None:
    # port=None → auto-pick a free port; an explicit port is honored verbatim.
    assert ServerManager(port=None)._port > 0
    assert ServerManager(port=8123)._port == 8123


def test_load_is_serialized_across_threads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Two threads calling load() concurrently (as happens when an /api/load request
    # is cancelled but its worker thread keeps running while a second load starts)
    # must never overlap inside the process-mutating section — the internal lock,
    # not the route's asyncio lock, is what guarantees this.
    manager = ServerManager()
    active = 0
    max_active = 0
    guard = threading.Lock()

    def slow_build(*args: object, **kwargs: object) -> list[str]:
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.1)  # hold the locked section open long enough to observe overlap
        with guard:
            active -= 1
        return ["true"]  # a real, instantly-exiting binary

    monkeypatch.setattr("kodo.server.runtime.build_command", slow_build)
    m1 = LibraryModel(name="pub/A", model_format=ModelFormat.gguf, path=tmp_path, load_target=tmp_path / "a")
    m2 = LibraryModel(name="pub/B", model_format=ModelFormat.gguf, path=tmp_path, load_target=tmp_path / "b")
    threads = [threading.Thread(target=manager.load, args=(m,)) for m in (m1, m2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    manager.stop()

    assert max_active == 1  # the two loads never mutated state at the same time
