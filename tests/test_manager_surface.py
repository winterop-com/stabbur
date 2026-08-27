"""Characterization tests: pin the model-manager read surface exactly as it is today.

``ServerManager`` (spawns a local runtime) and ``UpstreamManager`` (fronts a remote ``/v1``)
are held interchangeably by the serving routers through ``ManagerDep`` — a duck-typed union,
not a declared protocol. Nothing in the codebase states what that shared surface *is*, so a
refactor toward more backends (a facade, an ABC, a registry) can drop a property, turn one
into a method, or move an ``isinstance``-gated member without a single test going red: the
routers would still import, and the breakage would surface as a 500 at runtime.

These tests exist to make that impossible. They assert the surface itself — which names each
manager exposes, whether each is a property or a (coroutine) method, and what each reads as
when nothing is loaded — so the refactor can be proven behaviour-preserving rather than
argued to be. They deliberately duplicate no route logic; ``test_api.py`` covers the HTTP
contract, this file covers the object the routes are handed.

Nothing here spawns a runtime or touches the network: ``ServerManager`` is inspected while
stopped, and ``UpstreamManager``'s outbound calls (``models``/``ready``) are stubbed, matching
how ``test_api.py``'s ``upstream_app`` fixture fakes a remote.
"""

import inspect

import pytest

from stabbur.server import ServerManager, ServerState, UpstreamManager, UpstreamModel

# The union of members the serving routers actually reach for (measured across
# routers/serving/{proxy,chat,core,_base}.py). Some are reached only behind an
# ``isinstance(manager, UpstreamManager)`` branch — see _UPSTREAM_ONLY / _LOCAL_ONLY.
_ROUTE_SURFACE = {
    "base_url",
    "current",
    "last_error",
    "load",
    "load_by_name",
    "models",
    "n_ctx",
    "state",
    "stop",
    "touch",
}

# The members both managers expose, mapped to how a caller must reach each one. The *kind*
# is load-bearing, not decoration: the routers read ``manager.current`` bare and ``await
# manager.state()``, so a facade that exposes ``current`` as a method (or ``state`` as a
# plain attribute) type-checks at the import site and fails at request time.
_SHARED_SURFACE = {
    "base_url": "property",
    "current": "property",
    "last_error": "property",
    "n_ctx": "property",
    "ready": "async method",
    "state": "async method",
    "stop": "method",
}

# Members that exist on exactly one manager. Every call site for these sits behind an
# isinstance check, so moving one onto the other class silently changes which branch runs.
_UPSTREAM_ONLY = {"load_by_name", "models", "select_loaded", "touch"}
_LOCAL_ONLY = {"load"}


def _public_members(cls: type) -> set[str]:
    """Public (non-underscore) names a caller can reach on ``cls``."""
    return {name for name in dir(cls) if not name.startswith("_")}


def _access_kind(cls: type, name: str) -> str:
    """How ``name`` must be reached on ``cls``: bare attribute, call, or await.

    Uses ``getattr_static`` so a property is reported as a property instead of being
    evaluated (evaluating ``UpstreamManager.current`` on an instance would be harmless,
    but ``ServerManager.current`` has reaping side effects).
    """
    attr = inspect.getattr_static(cls, name)
    if isinstance(attr, property):
        return "property"
    if inspect.iscoroutinefunction(attr):
        return "async method"
    return "method"


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> UpstreamManager:
    """An UpstreamManager whose outbound calls are stubbed — no network, ever."""
    rows = [UpstreamModel(name="alpha-7b"), UpstreamModel(name="beta-30b", loaded=True)]
    monkeypatch.setattr(UpstreamManager, "models", lambda self: list(rows))

    async def _ready(self: UpstreamManager) -> bool:
        return True

    monkeypatch.setattr(UpstreamManager, "ready", _ready)
    return UpstreamManager("http://remote:1234")


# --- the shared surface -------------------------------------------------------


def test_shared_public_surface_is_exactly_the_pinned_set() -> None:
    # Derived by introspection rather than listed per class, so the pin cannot drift into
    # agreeing with only one of them. A future facade/ABC can be checked against the same
    # intersection: if it does not carry all seven, the routers cannot hold it.
    shared = _public_members(ServerManager) & _public_members(UpstreamManager)
    assert shared == set(_SHARED_SURFACE)


@pytest.mark.parametrize("cls", [ServerManager, UpstreamManager])
def test_shared_members_have_the_same_access_kind_on_both(cls: type) -> None:
    assert {name: _access_kind(cls, name) for name in _SHARED_SURFACE} == _SHARED_SURFACE


def test_members_exist_on_exactly_one_manager() -> None:
    # The asymmetric half of the surface. Each of these is only ever called inside an
    # isinstance branch, so a refactor that "harmonizes" them onto both classes changes
    # which code path the routers take even though every test on the shared half passes.
    local, remote = _public_members(ServerManager), _public_members(UpstreamManager)
    assert local - remote == _LOCAL_ONLY
    assert remote - local == _UPSTREAM_ONLY


def test_every_member_the_routes_use_is_resolvable() -> None:
    # The routes reach for the union of both surfaces; nothing they call may be missing
    # from the manager the app actually builds for that mode.
    assert _ROUTE_SURFACE <= _public_members(ServerManager) | _public_members(UpstreamManager)


# --- ServerManager, nothing loaded --------------------------------------------


def test_server_manager_reads_empty_when_nothing_is_loaded() -> None:
    manager = ServerManager()
    assert manager.current is None
    assert manager.last_error is None
    assert manager.n_ctx is None


async def test_server_manager_state_is_stopped_when_nothing_is_loaded() -> None:
    assert await ServerManager().state() is ServerState.stopped


def test_server_manager_n_ctx_is_none_without_a_live_model() -> None:
    # n_ctx is gated on ``current``, not on the stored field: a runtime that died leaves
    # _n_ctx set, and status must not report a context window for a model that is gone.
    manager = ServerManager()
    manager._n_ctx = 4096
    assert manager.n_ctx is None


def test_server_manager_base_url_is_host_port_with_no_path() -> None:
    # The /v1 proxy appends "/v1/<path>" to this, so the shape (scheme://host:port, no
    # trailing slash, no path) is part of the contract, not an implementation detail.
    assert ServerManager(host="127.0.0.1", port=9999).base_url == "http://127.0.0.1:9999"
    assert ServerManager(host="0.0.0.0", port=1).base_url == "http://0.0.0.0:1"


def test_server_manager_base_url_is_stable_before_any_load() -> None:
    # The port is chosen once in __init__ (auto-picked when unset), so the proxy target
    # does not move when a model is loaded, swapped, or stopped.
    manager = ServerManager()
    assert manager.base_url.endswith(f":{manager._port}")
    assert manager.base_url == ServerManager(port=manager._port).base_url


# --- UpstreamManager, nothing selected ----------------------------------------


def test_upstream_manager_reads_empty_when_nothing_is_selected(upstream: UpstreamManager) -> None:
    assert upstream.current is None
    assert upstream.last_error is None
    assert upstream.n_ctx is None


def test_upstream_base_url_is_available_without_a_selected_model(upstream: UpstreamManager) -> None:
    # Load-bearing: GET /v1/models is exempt from the "no model loaded" refusal in upstream
    # mode precisely because the proxy can reach the remote before anything is selected. A
    # facade that derived base_url from the current model would break discovery — the client
    # could not list what it is allowed to ask for.
    assert upstream.current is None
    assert upstream.base_url == "http://remote:1234"


@pytest.mark.parametrize(
    "given",
    [
        "http://remote:1234",
        "http://remote:1234/",
        "http://remote:1234/v1",
        "http://remote:1234/v1/",
        "  http://remote:1234  ",
    ],
)
def test_upstream_base_url_normalizes_to_no_trailing_v1(given: str) -> None:
    # Routes append their own "/v1/..." paths, so the stored form must never carry one —
    # /api/status also reports this string verbatim as the remote's identity.
    assert UpstreamManager(given).base_url == "http://remote:1234"


def test_upstream_n_ctx_is_always_none_even_with_a_model_selected(upstream: UpstreamManager) -> None:
    # The remote decides its own context window from its presets; stabbur cannot know it.
    # Pinned because "None" here means "unknown", not "nothing loaded".
    upstream.load_by_name("beta-30b", warmup=False)
    assert upstream.current is not None
    assert upstream.n_ctx is None


def test_upstream_current_prefers_the_model_being_loaded(upstream: UpstreamManager) -> None:
    # While a switch is in flight the status poll must name the incoming model, not the
    # outgoing one, or the UI shows a stale "ready" for a model the remote is not serving.
    upstream._selected = UpstreamModel(name="alpha-7b")
    upstream._loading = UpstreamModel(name="beta-30b")
    assert upstream.current is not None
    assert upstream.current.name == "beta-30b"


async def test_upstream_state_is_loading_while_a_switch_is_in_flight(upstream: UpstreamManager) -> None:
    upstream._loading = UpstreamModel(name="beta-30b")
    assert await upstream.state() is ServerState.loading


async def test_upstream_state_is_stopped_with_nothing_selected(upstream: UpstreamManager) -> None:
    # A reachable remote with no selection still reads as stopped — that is what makes the
    # UI offer the picker instead of a chat box.
    assert await upstream.state() is ServerState.stopped


async def test_upstream_state_is_ready_once_a_model_is_selected(upstream: UpstreamManager) -> None:
    upstream.load_by_name("beta-30b", warmup=False)
    assert await upstream.state() is ServerState.ready


def test_upstream_stop_clears_the_selection_and_keeps_base_url(upstream: UpstreamManager) -> None:
    # stop() is the shared member with the least shared meaning: locally it kills a process,
    # remotely it only forgets the selection. The remote's address survives, so /v1/models
    # discovery keeps working after an unload.
    upstream.load_by_name("beta-30b", warmup=False)
    upstream.stop()
    assert upstream.current is None
    assert upstream.base_url == "http://remote:1234"


def test_upstream_load_by_name_matches_case_insensitively_and_by_basename(upstream: UpstreamManager) -> None:
    upstream.load_by_name("BETA-30B", warmup=False)
    assert upstream.current is not None and upstream.current.name == "beta-30b"
    upstream.load_by_name("org/alpha-7b", warmup=False)
    assert upstream.current is not None and upstream.current.name == "alpha-7b"


def test_upstream_load_by_name_rejects_an_unserved_name_without_losing_the_selection(
    upstream: UpstreamManager,
) -> None:
    # The routers turn this RuntimeError into a 404 whose detail lists what the remote
    # serves, and a failed switch must leave the working selection in place.
    upstream.load_by_name("alpha-7b", warmup=False)
    with pytest.raises(RuntimeError, match="available"):
        upstream.load_by_name("not-served", warmup=False)
    assert upstream.current is not None and upstream.current.name == "alpha-7b"


async def test_upstream_touch_suppresses_the_next_liveness_probe() -> None:
    # touch() is called from /api/status while tokens are streaming: a busy llama-server
    # answers /v1/models slowly, and probing it right then reads as an outage. The probe
    # client is created lazily on first use, so it staying None is the proof that ready()
    # answered from the touch alone and opened no connection to the (unroutable) remote.
    manager = UpstreamManager("http://remote:1234")
    manager.touch()
    assert await manager.ready() is True
    assert manager._http is None
