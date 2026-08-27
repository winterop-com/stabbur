"""Tests for the single-backend facade (step 1: a no-op seam in front of the managers)."""

import inspect
from pathlib import Path

import pytest

from stabbur.backends import Backends
from stabbur.library import LibraryModel
from stabbur.models import ModelFormat
from stabbur.server import ServerManager, ServerState, UpstreamManager

# The surface ROADMAP.md names as "the whole surface" the serving routes consume. Spelled
# out here rather than derived, so adding a member to a manager without teaching the facade
# about it fails a test instead of silently bypassing the seam later.
ROUTE_SURFACE = (
    "current",
    "base_url",
    "n_ctx",
    "last_error",
    "state",
    "models",
    "load_by_name",
    "load",
    "stop",
    "touch",
)

_LISTING: dict[str, object] = {
    "data": [
        {"id": "gemma-4-12b", "architecture": {"input_modalities": ["text", "image"]}},
        {"id": "qwen3-coder", "status": {"value": "loaded"}},
    ]
}


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


def _model(path: Path) -> LibraryModel:
    return LibraryModel(name="pub/Foo", model_format=ModelFormat.gguf, path=path, load_target=path)


def _upstream(monkeypatch: pytest.MonkeyPatch) -> Backends:
    from stabbur import server as server_mod

    monkeypatch.setattr(server_mod.httpx, "get", lambda url, timeout=None: _FakeResponse(_LISTING))
    return Backends(UpstreamManager("http://up:1234/v1"))


def test_surface_is_declared_not_forwarded() -> None:
    # The point of the facade is that a missing member is a *type* error, which only holds
    # if every member is defined on the class. __getattr__ forwarding would pass an
    # attribute check while defeating both type checkers.
    assert not hasattr(Backends, "__getattr__")
    for name in ROUTE_SURFACE:
        assert name in vars(Backends), f"{name} is not declared on Backends"


def test_signatures_match_the_wrapped_managers() -> None:
    # A facade that quietly drops a parameter (e.g. load's n_ctx, load_by_name's warmup)
    # would type-check and then change behaviour, which is the one thing step 1 forbids.
    assert inspect.signature(Backends.load) == inspect.signature(ServerManager.load)
    assert inspect.signature(Backends.load_by_name) == inspect.signature(UpstreamManager.load_by_name)
    assert inspect.signature(Backends.models) == inspect.signature(UpstreamManager.models)


async def test_local_backend_read_surface_is_delegated() -> None:
    manager = ServerManager(port=8123)
    backends = Backends(manager)

    assert backends.backend is manager
    assert not backends.is_upstream
    assert backends.base_url == "http://127.0.0.1:8123" == manager.base_url
    assert backends.current is None
    assert backends.n_ctx is None
    assert backends.last_error is None
    assert await backends.state() is ServerState.stopped


def test_local_load_and_stop_reach_the_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = ServerManager()
    calls: list[tuple[str, object, object]] = []
    monkeypatch.setattr(manager, "load", lambda model, n_ctx=None: calls.append(("load", model.name, n_ctx)))
    monkeypatch.setattr(manager, "stop", lambda: calls.append(("stop", None, None)))
    backends = Backends(manager)

    backends.load(_model(tmp_path), 4096)
    backends.load(_model(tmp_path))  # the default n_ctx must survive the hop
    backends.stop()

    assert calls == [("load", "pub/Foo", 4096), ("load", "pub/Foo", None), ("stop", None, None)]


def test_local_backend_rejects_the_upstream_only_members(tmp_path: Path) -> None:
    # Absent-on-one-backend stays absent: the routes guard these with isinstance today, so
    # an unguarded call must fail exactly as it does against the bare manager (AttributeError).
    backends = Backends(ServerManager())

    for call in (backends.models, backends.touch, backends.select_loaded):
        with pytest.raises(AttributeError, match="upstream-only"):
            call()
    with pytest.raises(AttributeError, match="upstream-only"):
        backends.load_by_name("anything")


def test_upstream_backend_read_surface_is_delegated(monkeypatch: pytest.MonkeyPatch) -> None:
    backends = _upstream(monkeypatch)

    assert backends.is_upstream
    assert backends.base_url == "http://up:1234"  # trailing /v1 normalized by the manager
    assert backends.n_ctx is None  # the remote's presets decide; unknowable here
    assert backends.current is None
    assert [r.name for r in backends.models()] == ["gemma-4-12b", "qwen3-coder"]
    assert backends.models()[0].vision


async def test_upstream_selection_and_state(monkeypatch: pytest.MonkeyPatch) -> None:
    backends = _upstream(monkeypatch)

    backends.select_loaded()  # startup default: whatever the remote already has resident
    assert backends.current is not None and backends.current.name == "qwen3-coder"

    backends.touch()  # marks the upstream just-seen-alive, so state() needs no probe
    assert await backends.state() is ServerState.ready

    backends.stop()  # clears the selection; the remote keeps running
    assert backends.current is None
    assert await backends.state() is ServerState.stopped


def test_upstream_load_by_name_is_delegated(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur import server as server_mod

    backends = _upstream(monkeypatch)
    posted: list[dict[str, object]] = []

    def _post(url: str, json: dict[str, object] | None = None, timeout: object = None) -> _FakeResponse:
        posted.append(json or {})
        return _FakeResponse({})

    monkeypatch.setattr(server_mod.httpx, "post", _post)

    backends.load_by_name("GEMMA-4-12B")  # case-insensitive, matched against the remote's ids
    assert backends.current is not None and backends.current.name == "gemma-4-12b"
    assert posted and posted[0]["model"] == "gemma-4-12b"

    backends.load_by_name("qwen3-coder", warmup=False)  # pre-flight check must not evict
    assert len(posted) == 1

    with pytest.raises(RuntimeError, match="available: gemma-4-12b, qwen3-coder"):
        backends.load_by_name("nope")


def test_upstream_backend_rejects_the_local_only_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backends = _upstream(monkeypatch)

    with pytest.raises(AttributeError, match="local-only"):
        backends.load(_model(tmp_path))
