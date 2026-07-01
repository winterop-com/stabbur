"""Tests for the runtime process manager (no real model runtime needed)."""

import subprocess
from pathlib import Path

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
