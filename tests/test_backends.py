"""Tests for the backend facade: one active backend, several declared, one merged listing."""

import inspect
import threading
import time
from pathlib import Path
from typing import cast

import pytest

from stabbur import backends
from stabbur import library as library_ops
from stabbur.backends import Backends, BackendSpec
from stabbur.library import LibraryModel
from stabbur.models import ModelFormat
from stabbur.server import ServerManager, ServerState, UpstreamManager, UpstreamModel

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


def test_build_selects_the_remote_when_an_upstream_is_configured() -> None:
    # The factory is the one place that turns configuration into a backend. It had two copies
    # (the app factory and `stabbur serve`'s locked-model pre-flight) and step 2 adds a third
    # caller, so the choice is pinned here rather than trusted to stay in step.
    built = backends.build("http://remote:1234/v1")
    assert built.is_upstream
    assert built.base_url == "http://remote:1234"


def test_build_selects_a_local_runtime_without_an_upstream() -> None:
    built = backends.build(None, runtime_port=8123)
    assert not built.is_upstream
    assert built.base_url.endswith(":8123")


def test_local_only_narrowing_names_the_member_that_was_called() -> None:
    # The message used to hardcode "load()", so it would have named the wrong member the day a
    # second local-only one landed. Pin that it reports its caller.
    remote = backends.build("http://remote:1234/v1")
    with pytest.raises(AttributeError, match=r"load\(\) is local-only"):
        remote.load(cast(LibraryModel, object()))


# --- several declared, exactly one active -------------------------------------------------

_LOCAL_ONLY = BackendSpec(name="local")
_MSAI = BackendSpec(name="gpu-box", url="http://gpu-box:8080/v1")
_BOX = BackendSpec(name="box", url="http://box:9000")


def test_declare_holds_every_spec_with_the_first_one_active() -> None:
    held = backends.declare([_LOCAL_ONLY, _MSAI, _BOX])

    assert held.names == ("local", "gpu-box", "box")  # declaration order is priority order
    assert held.specs == (_LOCAL_ONLY, _MSAI, _BOX)
    assert held.name == "local"
    assert not held.is_upstream  # the scalar surface follows the ACTIVE backend, not the set


def test_declare_rejects_a_declaration_it_cannot_honour() -> None:
    with pytest.raises(ValueError, match="at least one"):
        backends.declare([])
    with pytest.raises(ValueError, match="duplicate backend name"):
        backends.declare([_MSAI, BackendSpec(name="gpu-box", url="http://other:1234")])
    # Two local backends would race for one runtime port and one library; several library
    # ROOTS are a thing, several local BACKENDS are not.
    with pytest.raises(ValueError, match="only one local backend"):
        backends.declare([_LOCAL_ONLY, BackendSpec(name="local2")])


def test_activate_moves_the_scalar_surface_and_nothing_else() -> None:
    held = backends.declare([_LOCAL_ONLY, _MSAI])

    held.activate("gpu-box")
    assert held.name == "gpu-box"
    assert held.is_upstream
    assert held.base_url == "http://gpu-box:8080"
    assert held.names == ("local", "gpu-box")  # the declared set is unchanged by activating

    held.activate("local")
    assert not held.is_upstream

    with pytest.raises(KeyError, match="no backend named 'nope'"):
        held.activate("nope")


def test_build_names_the_single_backend_after_its_host() -> None:
    # The name is the qualifier in model@backend, and --upstream carries none — so it is
    # derived rather than left blank, or every row from a flag-declared backend would be
    # unqualifiable.
    assert backends.build("http://gpu-box:8080/v1").names == ("gpu-box",)
    assert backends.build(None).names == ("local",)


# --- the merged listing: concurrent, per-backend timeout, failures as data -----------------


def _remote_rows(*names: str) -> list[UpstreamModel]:
    return [UpstreamModel(name=n) for n in names]


def _stub_upstreams(monkeypatch: pytest.MonkeyPatch, behaviour: dict[str, object]) -> None:
    """Make ``UpstreamManager.models`` answer per host: rows, an exception, a sleep, or a hang.

    An Event hangs until the test sets it — a probe that is still running when the listing
    comes back, which is precisely the case the timeout exists for, and which a fixed sleep
    would instead make the test pay for at teardown.
    """

    def _models(self: UpstreamManager) -> list[UpstreamModel]:
        outcome = behaviour[self.base_url]
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, threading.Event):
            assert outcome.wait(30), "the hung probe was never released"
            return []
        if isinstance(outcome, float):
            time.sleep(outcome)
            return []
        return cast(list[UpstreamModel], outcome)

    monkeypatch.setattr(UpstreamManager, "models", _models)


async def test_listings_merges_every_backend_in_declaration_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library_ops, "scan", lambda: [_model(tmp_path)])
    _stub_upstreams(
        monkeypatch,
        {"http://gpu-box:8080": _remote_rows("gemma-4-12b"), "http://box:9000": _remote_rows("qwen3-coder")},
    )

    listings = await backends.declare([_LOCAL_ONLY, _MSAI, _BOX]).listings()

    assert [listing.backend for listing in listings] == ["local", "gpu-box", "box"]
    assert [listing.error for listing in listings] == [None, None, None]
    assert [[m.name for m in listing.models] for listing in listings] == [
        ["pub/Foo"],
        ["gemma-4-12b"],
        ["qwen3-coder"],
    ]
    # The local backend's rows stay library models (path, size, format) and a remote's stay
    # ids — the union is the point, and flattening would have to invent the missing halves.
    assert isinstance(listings[0].models[0], LibraryModel)
    assert isinstance(listings[1].models[0], UpstreamModel)
    assert listings[1].url == "http://gpu-box:8080/v1"  # as declared, so a row maps back to config


async def test_a_dead_backend_degrades_to_a_row_and_the_healthy_ones_still_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library_ops, "scan", lambda: [_model(tmp_path)])
    _stub_upstreams(
        monkeypatch,
        {
            "http://gpu-box:8080": RuntimeError("upstream http://gpu-box:8080 unreachable: [Errno 61] refused"),
            "http://box:9000": _remote_rows("qwen3-coder"),
        },
    )

    listings = await backends.declare([_LOCAL_ONLY, _MSAI, _BOX]).listings()

    assert [listing.backend for listing in listings] == ["local", "gpu-box", "box"]
    assert listings[1].models == [] and "refused" in (listings[1].error or "")
    # The requirement that matters: the dead one costs its own rows and nothing else.
    assert [m.name for m in listings[0].models] == ["pub/Foo"]
    assert [m.name for m in listings[2].models] == ["qwen3-coder"]


async def test_an_unreachable_backend_is_bounded_by_its_own_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # A powered-off host black-holes the connection rather than refusing it, so nothing fails
    # fast and only the deadline ends the wait. The probe here is still running when the
    # assertions below hold — which is the point: the listing does not wait for it.
    hung = threading.Event()
    _stub_upstreams(monkeypatch, {"http://gpu-box:8080": hung, "http://box:9000": _remote_rows("qwen3-coder")})
    try:
        started = time.monotonic()
        listings = await backends.declare([_MSAI, _BOX]).listings(timeout=0.2)
        elapsed = time.monotonic() - started

        assert elapsed < 1.0, f"a black-holed backend stalled the whole listing ({elapsed:.2f}s)"
        assert listings[0].error == "did not answer within 0.2s"
        assert [m.name for m in listings[1].models] == ["qwen3-coder"]
    finally:
        hung.set()  # let the worker thread finish; wait_for cancelled the await, not the thread


async def test_backends_are_probed_concurrently_not_one_after_another(monkeypatch: pytest.MonkeyPatch) -> None:
    # Serially these three cost 0.9s and every extra host adds its own latency to every
    # listing; concurrently they cost the slowest one. Measured rather than asserted about,
    # since "concurrent" is invisible in the returned value.
    _stub_upstreams(monkeypatch, {"http://gpu-box:8080": 0.3, "http://box:9000": 0.3, "http://third:9000": 0.3})

    started = time.monotonic()
    await backends.declare([_MSAI, _BOX, BackendSpec(name="third", url="http://third:9000")]).listings()
    elapsed = time.monotonic() - started

    assert elapsed < 0.75, f"probes look serial ({elapsed:.2f}s for 3 x 0.3s)"


async def test_an_unconfigured_library_is_not_degraded_into_a_row(monkeypatch: pytest.MonkeyPatch) -> None:
    # LibraryNotConfigured is a RuntimeError like an unreachable upstream, and must NOT be
    # caught with one: it is missing setup, not an outage, and its message is the hint naming
    # the variable to set. The route turns it into a 503 carrying that hint.
    def _unconfigured() -> list[LibraryModel]:
        raise library_ops.LibraryNotConfigured

    monkeypatch.setattr(library_ops, "scan", _unconfigured)

    with pytest.raises(library_ops.LibraryNotConfigured):
        await backends.declare([_LOCAL_ONLY]).listings()
