"""Tests for the runtime process manager (no real model runtime needed)."""

import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from stabbur.library import LibraryModel
from stabbur.models import ModelFormat
from stabbur.runtime import supervisor
from stabbur.server import ServerManager, UpstreamModel


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

    monkeypatch.setattr("stabbur.server.runtime.build_command", slow_build)
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


def _recording_post(sink: list[dict[str, object]]) -> Callable[..., _FakeResponse]:
    """A stub ``httpx.post`` that records each warmup body it is handed."""

    def _post(url: str, json: dict[str, object] | None = None, timeout: object = None) -> _FakeResponse:
        sink.append(json or {})
        return _FakeResponse({})

    return _post


def test_upstream_manager_models_and_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur import server as server_mod

    manager = server_mod.UpstreamManager("http://up:1234/v1/")
    assert manager.base_url == "http://up:1234"  # trailing /v1 normalized away
    monkeypatch.setattr(server_mod.httpx, "get", lambda url, timeout=None: _FakeResponse(_ROUTER_LISTING))

    rows = manager.models()
    assert [r.name for r in rows] == ["gemma-4-12b-qat", "qwen3-coder"]
    assert rows[0].vision and rows[0].audio and not rows[0].loaded
    assert rows[1].loaded and not rows[1].vision

    manager.select_loaded()  # startup default: what the remote has resident
    assert manager.current is not None and manager.current.name == "qwen3-coder"

    posted: list[dict[str, object]] = []
    monkeypatch.setattr(server_mod.httpx, "post", _recording_post(posted))
    manager.load_by_name("GEMMA-4-12B-QAT")  # case-insensitive match
    assert manager.current is not None and manager.current.name == "gemma-4-12b-qat"
    # The router has no load endpoint, so the switch must send a request naming the model —
    # otherwise stabbur reports it ready while the remote is still serving the old one.
    assert posted and posted[0]["model"] == "gemma-4-12b-qat"
    assert posted[0]["max_tokens"] == 1

    with pytest.raises(RuntimeError, match="available: gemma-4-12b-qat, qwen3-coder"):
        manager.load_by_name("not-served")
    assert manager.current is not None  # a failed switch keeps the selection

    manager.stop()  # clears the selection only; nothing to kill
    assert manager.current is None


def test_upstream_switch_to_an_already_loaded_model_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # qwen3-coder is already resident; re-selecting it must not cost a reload.
    from stabbur import server as server_mod

    manager = server_mod.UpstreamManager("http://up:1234")
    monkeypatch.setattr(server_mod.httpx, "get", lambda url, timeout=None: _FakeResponse(_ROUTER_LISTING))
    posted: list[dict[str, object]] = []
    monkeypatch.setattr(server_mod.httpx, "post", _recording_post(posted))

    manager.load_by_name("qwen3-coder")
    assert manager.current is not None and manager.current.name == "qwen3-coder"
    assert posted == []


def test_upstream_failed_warmup_keeps_the_previous_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    # If the remote cannot load the model, stabbur must not claim it is serving it.
    import httpx

    from stabbur import server as server_mod

    manager = server_mod.UpstreamManager("http://up:1234")
    monkeypatch.setattr(server_mod.httpx, "get", lambda url, timeout=None: _FakeResponse(_ROUTER_LISTING))
    manager.select_loaded()
    assert manager.current is not None and manager.current.name == "qwen3-coder"

    def _boom(url: str, json: object = None, timeout: object = None) -> object:
        raise httpx.ConnectError("out of memory")

    monkeypatch.setattr(server_mod.httpx, "post", _boom)
    with pytest.raises(RuntimeError, match="could not be loaded"):
        manager.load_by_name("gemma-4-12b-qat")
    assert manager.current.name == "qwen3-coder"  # unchanged, and not left mid-switch
    assert manager._loading is None


def test_upstream_warmup_is_skipped_for_the_name_only_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    # `stabbur serve --upstream --model X` validates the name before the server exists; loading
    # there would evict the remote's resident model for a process that is about to exit.
    from stabbur import server as server_mod

    manager = server_mod.UpstreamManager("http://up:1234")
    monkeypatch.setattr(server_mod.httpx, "get", lambda url, timeout=None: _FakeResponse(_ROUTER_LISTING))
    posted: list[dict[str, object]] = []
    monkeypatch.setattr(server_mod.httpx, "post", _recording_post(posted))

    manager.load_by_name("gemma-4-12b-qat", warmup=False)
    assert posted == []
    with pytest.raises(RuntimeError, match="not served by"):
        manager.load_by_name("nope", warmup=False)


async def test_upstream_state_reports_loading_during_a_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    # The whole point of the eager load: the UI shows the swap rather than a stale "ready".
    from stabbur import server as server_mod

    manager = server_mod.UpstreamManager("http://up:1234")
    monkeypatch.setattr(server_mod.httpx, "get", lambda url, timeout=None: _FakeResponse(_ROUTER_LISTING))
    manager.select_loaded()
    manager._ready_at = time.monotonic()  # count the upstream as just-probed; no real socket here
    assert await manager.state() is server_mod.ServerState.ready

    # What a status poll sees while the remote is loading the weights.
    mid_switch: list[tuple[UpstreamModel | None, UpstreamModel | None]] = []

    def _post(url: str, json: object = None, timeout: object = None) -> object:
        mid_switch.append((manager._loading, manager.current))
        return _FakeResponse({})

    monkeypatch.setattr(server_mod.httpx, "post", _post)
    manager.load_by_name("gemma-4-12b-qat")

    ((loading, current),) = mid_switch
    assert loading is not None and loading.name == "gemma-4-12b-qat"
    assert current is not None and current.name == "gemma-4-12b-qat"  # the UI names the incoming model
    # state() reports `loading` purely from that flag — no upstream probe involved.
    manager._loading = loading
    assert await manager.state() is server_mod.ServerState.loading
    manager._loading = None

    assert manager.current is not None and manager.current.name == "gemma-4-12b-qat"
    assert await manager.state() is server_mod.ServerState.ready


def test_upstream_manager_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from stabbur import server as server_mod

    def _boom(url: str, timeout: object = None) -> object:
        raise httpx.ConnectError("no route to host")

    manager = server_mod.UpstreamManager("http://down:9")
    monkeypatch.setattr(server_mod.httpx, "get", _boom)
    with pytest.raises(RuntimeError, match="unreachable"):
        manager.models()
    manager.select_loaded()  # best-effort: swallows the failure, records why
    assert manager.current is None
    assert manager.last_error is not None and "unreachable" in manager.last_error


async def test_upstream_ready_paces_probes_and_forgives_jitter() -> None:
    import time as _time

    import httpx

    from stabbur import server as server_mod

    manager = server_mod.UpstreamManager("http://up:1234")

    class _FailingClient:
        async def get(self, url: str) -> object:
            raise httpx.ConnectError("probe lost")

    manager._http = _FailingClient()  # type: ignore[assignment]
    assert not await manager.ready()  # never succeeded -> genuinely down

    now = _time.monotonic()
    manager._ready_at = now  # a probe just succeeded -> trusted without re-probing
    assert await manager.ready()
    manager._ready_at = now - 15  # past the TTL, probe fails, but within the grace window
    assert await manager.ready()
    manager._ready_at = now - 60  # grace exhausted -> report the outage
    assert not await manager.ready()


def test_resolve_binary_prefers_heims_own_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The MLX runtimes are stabbur extras, so they land in stabbur's own environment where a
    # `uv tool install` exposes nothing on PATH. Look beside the interpreter first, so
    # installing the extra "into stabbur" works without a global install.
    from stabbur import runtime as runtime_mod

    envbin = tmp_path / "bin"
    envbin.mkdir()
    (envbin / "python").write_text("")
    tool = envbin / "mlx_lm.server"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.setattr(runtime_mod.sys, "executable", str(envbin / "python"))
    monkeypatch.setattr(runtime_mod.shutil, "which", lambda _n: None)  # nothing on PATH
    assert runtime_mod.resolve_binary("mlx_lm.server") == str(tool)
    assert runtime_mod.resolve_binary("absent-binary") is None

    # A binary only on PATH is still found (the normal llama.cpp case).
    monkeypatch.setattr(runtime_mod.shutil, "which", lambda _n: "/opt/homebrew/bin/llama-server")
    assert runtime_mod.resolve_binary("llama-server") == "/opt/homebrew/bin/llama-server"
