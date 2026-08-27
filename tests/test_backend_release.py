"""Tests for freeing the backend a qualified load switched *away* from.

The bug these pin down: ``/api/load/model@backend`` moved the pointer and left the outgoing
local ``llama-server`` running. Status then reported the new backend and ``/api/unload`` (scalar)
hit it too, so the resident process was invisible AND unreachable — freed only by a later local
load or by stabbur exiting. ROADMAP.md's "loaded stays singular" has to hold in RAM, not just in
``/api/status``, or holding several backends reintroduces exactly the OOM it was decided to avoid.
"""

import threading
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from stabbur import backends, runtime
from stabbur import library as library_ops
from stabbur.app import create_app
from stabbur.backends import BackendSpec
from stabbur.config import Settings
from stabbur.library import LibraryModel, _scan
from stabbur.models import ModelFormat
from stabbur.server import ServerManager, UpstreamManager, UpstreamModel

# A name only the library has, and one only the remote has: this file is about the *switch*,
# so nothing here should ever be ambiguous enough to need the 409 probe.
LOCAL_MODEL = "pub/Local-only-GGUF"
REMOTE_MODEL = "some-remote-model"

LOCAL = BackendSpec(name="local")
REMOTE = BackendSpec(name="gpu-box", url="http://gpu-box:8080/v1")


class Runtimes:
    """Stand-in for the local runtime process: what is resident, and every stop it was asked for."""

    def __init__(self) -> None:
        self.resident: LibraryModel | None = None
        self.stops: list[str] = []


@pytest.fixture
def runtimes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Runtimes:
    """Fake both backends: a local runtime whose residency is observable, and a reachable remote.

    ``ServerManager.current`` is derived from a live child process, so a fake load has to stand in
    for the whole thing. ``stop`` is recorded *and* clears residency, which is the property under
    test: after switching away, the runtime must read back as gone.
    """
    state = Runtimes()
    local_rows = [_library_model(tmp_path, LOCAL_MODEL)]
    remote_rows = [UpstreamModel(name=REMOTE_MODEL, loaded=True)]

    monkeypatch.setattr(library_ops, "scan", lambda *a, **k: list(local_rows))
    monkeypatch.setattr(_scan, "scan", lambda *a, **k: list(local_rows))
    monkeypatch.setattr(runtime, "runnable_error", lambda m: None)

    def _local_load(self: ServerManager, model: LibraryModel, n_ctx: int | None = None) -> None:
        state.resident = model

    def _local_stop(self: ServerManager) -> None:
        state.stops.append("local")
        state.resident = None

    async def _ready(self: Any) -> bool:
        return True

    monkeypatch.setattr(ServerManager, "load", _local_load)
    monkeypatch.setattr(ServerManager, "stop", _local_stop)
    monkeypatch.setattr(ServerManager, "ready", _ready)
    monkeypatch.setattr(ServerManager, "current", property(lambda self: state.resident))

    def _remote_load(self: UpstreamManager, name: str, *, warmup: bool = True) -> None:
        match = next((r for r in remote_rows if r.name.lower() == name.strip().lower()), None)
        if match is None:
            raise RuntimeError(f"{name!r} is not served by {self.base_url} — available: ...")
        self._selected = match  # noqa: SLF001 - stand in for the remote's own selection

    monkeypatch.setattr(UpstreamManager, "models", lambda self: list(remote_rows))
    monkeypatch.setattr(UpstreamManager, "load_by_name", _remote_load)
    monkeypatch.setattr(UpstreamManager, "ready", _ready)
    return state


def _library_model(path: Path, name: str) -> LibraryModel:
    return LibraryModel(name=name, model_format=ModelFormat.gguf, path=path, load_target=path / "w.gguf")


def _app(active: str = "local") -> FastAPI:
    app = create_app(Settings(serve_model=None))
    app.state.manager = backends.declare([LOCAL, REMOTE], active=active)
    return app


async def _post(app: FastAPI, path: str) -> tuple[int, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(path)
    return response.status_code, response.json()


async def _status(app: FastAPI) -> dict[str, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/status")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


async def test_switching_from_local_to_remote_stops_the_local_runtime(runtimes: Runtimes) -> None:
    # THE REGRESSION. The old route moved the pointer and nothing else, so the local llama-server
    # kept its RAM while status reported the remote and /api/unload could no longer reach it.
    app = _app(active="local")
    assert (await _post(app, f"/api/load/{LOCAL_MODEL}@local"))[0] == 200
    assert runtimes.resident is not None

    code, body = await _post(app, f"/api/load/{REMOTE_MODEL}@gpu-box")
    assert code == 200, body
    assert body["backend"] == "gpu-box"
    assert runtimes.stops == ["local"]
    assert runtimes.resident is None  # the memory is actually back, not just unreported


async def test_a_failed_switch_leaves_the_local_runtime_alone(runtimes: Runtimes) -> None:
    # Load first, release second: a load that fails unwinds the pointer, and the model the caller
    # still had must survive that. Releasing before (or in a finally) would kill it to reach a
    # backend the request never got to.
    app = _app(active="local")
    assert (await _post(app, f"/api/load/{LOCAL_MODEL}@local"))[0] == 200

    code, _ = await _post(app, "/api/load/not-served-anywhere@gpu-box")
    assert code == 404
    assert runtimes.stops == []
    status = await _status(app)
    assert status["backend"] == "local"
    assert status["model"] == LOCAL_MODEL


async def test_switching_away_from_a_remote_evicts_nothing(runtimes: Runtimes) -> None:
    # A remote holds what it holds for every client talking to it, so there is nothing here that
    # is ours to stop — and its selection is kept, which is what makes switching back free.
    app = _app(active="gpu-box")
    assert (await _post(app, f"/api/load/{REMOTE_MODEL}@gpu-box"))[0] == 200

    code, body = await _post(app, f"/api/load/{LOCAL_MODEL}@local")
    assert code == 200, body
    assert runtimes.stops == []  # the local load's own swap is ServerManager.load's business
    assert (await _status(app))["backend"] == "local"

    code, body = await _post(app, f"/api/load/{REMOTE_MODEL}@gpu-box")
    assert code == 200, body
    assert runtimes.stops == ["local"]  # switching back frees the library runtime, once


async def test_reloading_on_the_active_backend_stops_nothing_extra(runtimes: Runtimes) -> None:
    # Qualifying an id the active backend already serves is not a switch. Releasing "the previous
    # backend" there would stop the runtime the load just spawned.
    app = _app(active="local")
    assert (await _post(app, f"/api/load/{LOCAL_MODEL}@local"))[0] == 200
    assert (await _post(app, f"/api/load/{LOCAL_MODEL}@local"))[0] == 200
    assert runtimes.stops == []
    assert runtimes.resident is not None


async def test_a_live_generation_blocks_the_switch_before_anything_is_stopped(runtimes: Runtimes) -> None:
    # The runtime a stream is reading from must not be killed under it. The load is refused first
    # (409, by _reject_if_generating), so the release never runs — the guard covers both halves.
    app = _app(active="local")
    assert (await _post(app, f"/api/load/{LOCAL_MODEL}@local"))[0] == 200
    app.state.active_generations = 1

    code, body = await _post(app, f"/api/load/{REMOTE_MODEL}@gpu-box")
    assert code == 409, body
    assert runtimes.stops == []
    assert runtimes.resident is not None
    assert (await _status(app))["backend"] == "local"  # the failed switch also unwound the pointer


def test_release_frees_a_local_runtime_and_leaves_a_remote_untouched(runtimes: Runtimes) -> None:
    held = backends.declare([LOCAL, REMOTE], active="local")
    held.load(_library_model(Path("/nowhere"), LOCAL_MODEL))

    assert held.release("local") is True
    assert runtimes.stops == ["local"]
    assert held.name == "local"  # releasing frees; it does not move the pointer
    assert held.release("local") is False  # nothing running: reported, and still a no-op stop

    held.activate("gpu-box")
    held.load_by_name(REMOTE_MODEL)
    assert held.release("gpu-box") is False
    assert held.current is not None  # the remote keeps its selection, and its model

    with pytest.raises(KeyError, match="no backend named 'nope'"):
        held.release("nope")


async def test_the_release_runs_off_the_event_loop(runtimes: Runtimes, monkeypatch: pytest.MonkeyPatch) -> None:
    # Stopping a process group waits on it for up to 10s. Done inline it would freeze status
    # polling and every other request for that whole window, so it must reach a worker thread.
    app = _app(active="local")
    assert (await _post(app, f"/api/load/{LOCAL_MODEL}@local"))[0] == 200

    loop_thread = threading.get_ident()  # this coroutine runs ON the event loop thread
    seen: list[int] = []

    def _recording_stop(self: ServerManager) -> None:
        seen.append(threading.get_ident())
        runtimes.stops.append("local")
        runtimes.resident = None

    monkeypatch.setattr(ServerManager, "stop", _recording_stop)
    assert (await _post(app, f"/api/load/{REMOTE_MODEL}@gpu-box"))[0] == 200
    assert seen and seen[0] != loop_thread
