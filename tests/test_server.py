"""Tests for the runtime process manager (no real model runtime needed)."""

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from heim.library import LibraryModel
from heim.models import ModelFormat
from heim.runtime import supervisor
from heim.server import ServerManager


def _model(path: Path) -> LibraryModel:
    return LibraryModel(name="pub/Foo", model_format=ModelFormat.gguf, path=path, load_target=path)


def test_current_reaps_dead_runtime(tmp_path: Path) -> None:
    # A runtime child that has exited (crash / OOM / killed) is not a loaded
    # model: ``current`` must report None so status and the /v1 proxy do not
    # forward to a dead (or reused) port.
    manager = ServerManager()
    proc = subprocess.Popen(["true"])
    proc.wait()  # child has exited
    manager._handle = supervisor.RuntimeHandle(proc, "http://127.0.0.1:1", 1, ["true"], tmp_path / "state", None)
    manager._model = _model(tmp_path)

    assert manager.current is None
    assert manager._model is None
    assert manager._handle is None


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
        return [sys.executable, "-c", "import time; time.sleep(30)"]  # long-lived so spawn succeeds

    monkeypatch.setattr("heim.server.runtime.build_command", slow_build)
    m1 = LibraryModel(name="pub/A", model_format=ModelFormat.gguf, path=tmp_path, load_target=tmp_path / "a")
    m2 = LibraryModel(name="pub/B", model_format=ModelFormat.gguf, path=tmp_path, load_target=tmp_path / "b")
    threads = [threading.Thread(target=manager.load, args=(m,)) for m in (m1, m2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    manager.stop()

    assert max_active == 1  # the two loads never mutated state at the same time


_ROUTER_LISTING = {
    "data": [
        {
            "id": "gemma-4-12b-qat",
            "status": {"value": "unloaded"},
            "architecture": {"input_modalities": ["text", "image", "audio"]},
        },
        {"id": "qwen3-coder", "status": {"value": "loaded"}, "architecture": {"input_modalities": ["text"]}},
    ]
}


class _FakeResponse:
    status_code = 200

    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> object:
        return self._payload


def test_upstream_manager_models_and_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    from heim import server as server_mod

    manager = server_mod.UpstreamManager("http://up:1234/v1/")
    assert manager.base_url == "http://up:1234"  # trailing /v1 normalized away
    monkeypatch.setattr(server_mod.httpx, "get", lambda url, timeout=None: _FakeResponse(_ROUTER_LISTING))

    rows = manager.models()
    assert [r.name for r in rows] == ["gemma-4-12b-qat", "qwen3-coder"]
    assert rows[0].vision and rows[0].audio and not rows[0].loaded
    assert rows[1].loaded and not rows[1].vision

    manager.select_loaded()  # startup default: what the remote has resident
    assert manager.current is not None and manager.current.name == "qwen3-coder"

    manager.load_by_name("GEMMA-4-12B-QAT")  # case-insensitive match
    assert manager.current is not None and manager.current.name == "gemma-4-12b-qat"
    with pytest.raises(RuntimeError, match="available: gemma-4-12b-qat, qwen3-coder"):
        manager.load_by_name("not-served")
    assert manager.current is not None  # a failed switch keeps the selection

    manager.stop()  # clears the selection only; nothing to kill
    assert manager.current is None


def test_upstream_manager_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from heim import server as server_mod

    def _boom(url: str, timeout: object = None) -> object:
        raise httpx.ConnectError("no route to host")

    manager = server_mod.UpstreamManager("http://down:9")
    monkeypatch.setattr(server_mod.httpx, "get", _boom)
    with pytest.raises(RuntimeError, match="unreachable"):
        manager.models()
    manager.select_loaded()  # best-effort: swallows the failure, records why
    assert manager.current is None
    assert manager.last_error is not None and "unreachable" in manager.last_error
