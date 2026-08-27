"""Lifecycle and async-hygiene tests for the serve layer.

Four kinds of defect live here, and none of them is visible in a single-request test:

- **Atomicity.** ``/api/load`` reads the active backend, moves the pointer, loads, and frees
  what it left. Two concurrent loads that interleave those steps corrupt each other, so the
  sequence is driven concurrently rather than one call at a time.
- **Blocking work on the event loop.** A filesystem scan or a synchronous ``httpx`` call run
  inline stalls every other request, which no assertion on a *response* can see. These tests
  assert the thread the work ran on instead — the loop's thread is the failure.
- **Teardown.** What a shutdown releases is invisible unless you look at the objects afterwards.
- **The SSE contract.** A truncated reply and an empty one produce the same frames unless
  ``finish_reason`` is carried out of the stream.
"""

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from stabbur import agent, backends, mcp_catalog, mcpservers, project
from stabbur import library as library_ops
from stabbur.app import create_app
from stabbur.backends import Backends, BackendSpec
from stabbur.config import Settings
from stabbur.library import LibraryModel, _scan
from stabbur.models import ModelFormat
from stabbur.routers import serving
from stabbur.server import ServerManager, UpstreamManager, UpstreamModel
from stabbur.voice import kokoro

LOCAL = BackendSpec(name="local")
REMOTE = BackendSpec(name="gpu-box", url="http://gpu-box:8080/v1")

LOCAL_MODEL = "pub/Local-only-GGUF"
REMOTE_MODEL = "some-remote-model"


def _quiet_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize the lifespan's side effects so a test can run it for its own sake.

    No project, no MCP servers spawned, no seeded global config, and no Kokoro pre-warm — that
    last one starts a background thread that DOWNLOADS ~310 MB on a machine without the assets.
    """
    monkeypatch.setattr(project, "load", lambda *a, **k: None)
    monkeypatch.setattr(mcpservers, "resolve", lambda *a, **k: {})
    monkeypatch.setattr(mcp_catalog, "seed_global_defaults", lambda *a, **k: None)
    monkeypatch.setattr(kokoro, "available", lambda: False)


def _events(text: str) -> list[dict[str, Any]]:
    """Parse the SSE data events from a buffered /api/chat response body."""
    return [json.loads(line[len("data: ") :]) for line in text.splitlines() if line.startswith("data: ")]


class FakeManager:
    """A model is 'loaded' so /api/chat proceeds into the stream."""

    current = type("M", (), {"load_target": Path("/models/x")})()
    base_url = "http://runtime"


# --- 1. concurrent qualified loads ---------------------------------------------------------


@pytest.fixture
def two_backends(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, list[str]]:
    """Two declared backends whose loads are slow enough to overlap, with every call recorded.

    The local load sleeps *in its worker thread* (``/api/load`` runs it through ``to_thread``),
    which is what gives a second request a real window to interleave — the interleaving is the
    subject, so it must not depend on a lucky scheduling order.
    """
    log: dict[str, list[str]] = {"loads": [], "releases": []}
    local_rows = [LibraryModel(name=LOCAL_MODEL, model_format=ModelFormat.gguf, path=tmp_path, load_target=tmp_path)]
    remote_rows = [UpstreamModel(name=REMOTE_MODEL, loaded=False)]

    monkeypatch.setattr(library_ops, "scan", lambda *a, **k: list(local_rows))
    monkeypatch.setattr(_scan, "scan", lambda *a, **k: list(local_rows))
    monkeypatch.setattr("stabbur.runtime.runnable_error", lambda m: None)

    resident: dict[str, LibraryModel | None] = {"local": None}

    def _local_load(self: ServerManager, model: LibraryModel, n_ctx: int | None = None) -> None:
        time.sleep(0.05)  # a real (threaded) spawn takes time; the race needs that window
        log["loads"].append(f"local:{model.name}")
        resident["local"] = model

    def _local_stop(self: ServerManager) -> None:
        resident["local"] = None

    async def _ready(self: Any) -> bool:
        return True

    monkeypatch.setattr(ServerManager, "load", _local_load)
    monkeypatch.setattr(ServerManager, "stop", _local_stop)
    monkeypatch.setattr(ServerManager, "ready", _ready)
    monkeypatch.setattr(ServerManager, "current", property(lambda self: resident["local"]))

    def _remote_load(self: UpstreamManager, name: str, *, warmup: bool = True) -> None:
        match = next((r for r in remote_rows if r.name.lower() == name.strip().lower()), None)
        if match is None:
            raise RuntimeError(f"{name!r} is not served by {self.base_url}")
        time.sleep(0.05)
        log["loads"].append(f"gpu-box:{match.name}")
        self._selected = match  # noqa: SLF001 - stand in for the remote's own selection

    monkeypatch.setattr(UpstreamManager, "load_by_name", _remote_load)
    monkeypatch.setattr(UpstreamManager, "models", lambda self: list(remote_rows))
    monkeypatch.setattr(UpstreamManager, "ready", _ready)

    real_release = Backends.release

    def _release(self: Backends, name: str) -> bool:
        log["releases"].append(name)
        return real_release(self, name)

    monkeypatch.setattr(Backends, "release", _release)
    return log


def _two_backend_app(active: str = "local") -> FastAPI:
    app = create_app(Settings(serve_model=None))
    app.state.manager = backends.declare([LOCAL, REMOTE], active=active)
    return app


async def test_concurrent_qualified_loads_do_not_interleave(two_backends: dict[str, list[str]]) -> None:
    # THE RACE. Two qualified loads aimed at different backends, in flight at once. Before the
    # lifecycle lock covered the whole read-activate-load-release sequence, only the load's
    # mutation held it: both requests read the same `previous`, the second moved the pointer
    # while the first was still resolving against it, and BOTH then released the local backend —
    # so one request reported the other's backend and the runtime it had just spawned was killed.
    app = _two_backend_app(active="local")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        local, remote = await asyncio.gather(
            client.post(f"/api/load/{LOCAL_MODEL}@local"),
            client.post(f"/api/load/{REMOTE_MODEL}@gpu-box"),
        )

    assert local.status_code == 200, local.text
    assert remote.status_code == 200, remote.text
    # Each load answers for the backend it NAMED, not for whichever one won the pointer.
    assert local.json()["backend"] == "local"
    assert local.json()["model"] == LOCAL_MODEL
    assert remote.json()["backend"] == "gpu-box"
    assert remote.json()["model"] == REMOTE_MODEL
    # Both loads really ran, each on its own backend.
    assert sorted(two_backends["loads"]) == sorted([f"local:{LOCAL_MODEL}", f"gpu-box:{REMOTE_MODEL}"])
    # No backend is freed twice. Which order the two loads land in is up to the scheduler (and
    # both orders are correct — a load that switches away frees what it left), but a backend
    # released a SECOND time is always the bug: that release kills a runtime the other request
    # had just spawned, because both requests read the same outgoing name.
    releases = two_backends["releases"]
    assert len(releases) == len(set(releases)), releases


async def test_a_load_that_stays_on_its_backend_releases_nothing(two_backends: dict[str, list[str]]) -> None:
    # The other half of the sequence's atomicity: a qualified load naming the ALREADY-active
    # backend must not free it. (Under the old lock scope this held only when nothing else ran.)
    app = _two_backend_app(active="local")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/load/{LOCAL_MODEL}@local")

    assert response.status_code == 200, response.text
    assert two_backends["releases"] == []


# --- 2 + 3. blocking work must not run on the event loop -----------------------------------


def _loop_thread_id() -> int:
    """The thread the event loop is running on — the one no blocking work may run in."""
    return threading.get_ident()


async def test_library_scan_during_a_load_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `library.find` is a full multi-root filesystem scan. Run inline it stalled the loop for its
    # whole duration — including the /api/status polls the UI uses to render the very load that
    # is blocking them.
    loop_thread = _loop_thread_id()
    seen: dict[str, int] = {}
    model = LibraryModel(name=LOCAL_MODEL, model_format=ModelFormat.gguf, path=tmp_path, load_target=tmp_path)

    def _find(name: str, *a: Any, **k: Any) -> list[LibraryModel]:
        seen["find"] = threading.get_ident()
        return [model]

    monkeypatch.setattr(library_ops, "find", _find)
    monkeypatch.setattr("stabbur.routers.serving.chat.library_ops.find", _find)
    monkeypatch.setattr("stabbur.runtime.runnable_error", lambda m: None)
    monkeypatch.setattr(ServerManager, "load", lambda self, m, n=None: None)

    app = create_app(Settings(serve_model=None))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(f"/api/load/{LOCAL_MODEL}")

    assert seen["find"] != loop_thread


async def test_capability_detection_during_a_chat_turn_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Capability detection parses a GGUF header (and writes the sidecar it caches into) and the
    # sampling recommendation reads a JSON file next to the weights. Both ran on the loop at the
    # top of every /api/chat turn.
    loop_thread = _loop_thread_id()
    seen: dict[str, int] = {}

    def _capabilities(model: Any) -> Any:
        seen["capabilities"] = threading.get_ident()
        return type("C", (), {"vision": False})()

    monkeypatch.setattr("stabbur.routers.serving.chat.capabilities.capabilities", _capabilities)

    async def _run(*a: Any, **k: Any) -> str:
        return ""

    monkeypatch.setattr(agent, "run", _run)

    app = create_app(Settings(serve_model=None))
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    finally:
        app.dependency_overrides.clear()

    assert seen["capabilities"] != loop_thread


async def test_startup_model_selection_runs_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    # The upstream startup paths reach a synchronous httpx.get (15s), a warmup httpx.post that
    # waits out a cold model load (up to 600s) and time.sleep retries. Run on the loop, that is a
    # startup during which no signal handler can run: `serve --upstream <url> --model <name>`
    # against a host that accepts and never answers ignored Ctrl-C for ten minutes.
    loop_thread = _loop_thread_id()
    seen: dict[str, int] = {}
    ticks = {"n": 0}

    def _load_by_name(self: UpstreamManager, name: str, *, warmup: bool = True) -> None:
        seen["load"] = threading.get_ident()
        time.sleep(0.2)  # stands in for the blocking warmup
        self._selected = UpstreamModel(name=name, loaded=True)  # noqa: SLF001

    monkeypatch.setattr(UpstreamManager, "load_by_name", _load_by_name)
    _quiet_lifespan(monkeypatch)

    app = create_app(Settings(upstream="http://gpu-box:8080/v1", serve_model="a-remote-model"))

    async def heartbeat() -> None:
        while True:  # counts only if the loop is actually free to run it
            await asyncio.sleep(0.01)
            ticks["n"] += 1

    beat = asyncio.create_task(heartbeat())
    try:
        async with app.router.lifespan_context(app):
            pass
    finally:
        beat.cancel()

    assert seen["load"] != loop_thread
    assert ticks["n"] > 0  # the loop stayed responsive while the remote was being warmed up


# --- 4. shutdown releases every backend, and cannot be skipped -------------------------------


async def test_aclose_closes_every_declared_backends_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # UpstreamManager.ready lazily opens a keep-alive AsyncClient and nothing ever closed it:
    # `stop` only clears the selection, and the lifespan called the SCALAR stop, which reaches
    # the active backend alone. So every upstream that had merely been status-polled held an
    # open connection pool for the life of the process.
    held = backends.declare([LOCAL, REMOTE], active="local")
    inactive = cast(UpstreamManager, held._backends["gpu-box"])  # noqa: SLF001 - the point is the INACTIVE one

    monkeypatch.setattr(
        httpx.AsyncClient, "get", lambda self, url, **k: (_ for _ in ()).throw(httpx.ConnectError("down"))
    )
    await inactive.ready()  # a status poll: opens the client
    client = inactive._http  # noqa: SLF001
    assert client is not None

    await held.aclose()

    assert client.is_closed
    assert inactive._http is None  # noqa: SLF001 - and a later ready() may open a fresh one


async def test_aclose_keeps_going_when_one_backend_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Terminating a process group can raise. One backend failing to let go must not leave the
    # rest of them — or the caller's own cleanup — unrun.
    held = backends.declare([LOCAL, REMOTE], active="local")
    closed: list[str] = []

    async def _boom(self: ServerManager) -> None:
        raise OSError("killpg failed")

    async def _record(self: UpstreamManager) -> None:
        closed.append("gpu-box")

    monkeypatch.setattr(ServerManager, "aclose", _boom)
    monkeypatch.setattr(UpstreamManager, "aclose", _record)

    await held.aclose()

    assert closed == ["gpu-box"]


async def test_lifespan_closes_the_shared_client_even_if_the_backends_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Shutdown ordering: the teardown released the backends and THEN closed the shared HTTP
    # client, unguarded — so a raising release leaked the one connection pool every request in
    # the process went through.
    _quiet_lifespan(monkeypatch)

    app = create_app(Settings(serve_model=None))

    class ExplodingManager:
        is_upstream = False

        async def aclose(self) -> None:
            raise RuntimeError("teardown blew up")

    app.state.manager = ExplodingManager()
    with pytest.raises(RuntimeError, match="teardown blew up"):
        async with app.router.lifespan_context(app):
            pass

    assert app.state.http.is_closed


# --- 5. the upstream listing cache is guarded and single-flight -----------------------------


async def test_concurrent_listings_hit_the_upstream_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # `models()` is called from worker threads (the picker fans every backend out with
    # to_thread + gather). Unguarded, N concurrent callers that all found the cache stale each
    # sent their own request — a stampede at a remote already slow enough to make the cache
    # worth having — and stored (rows, timestamp) as two separate writes, so another thread
    # could observe a fresh timestamp against the previous rows.
    manager = UpstreamManager("http://gpu-box:8080/v1")
    hits = {"n": 0}

    def _get(url: str, **kwargs: Any) -> httpx.Response:
        hits["n"] += 1
        time.sleep(0.05)  # a busy llama-server answers /v1/models slowly; that is the window
        return httpx.Response(200, json={"data": [{"id": REMOTE_MODEL}]}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", _get)

    results = await asyncio.gather(*(asyncio.to_thread(manager.models) for _ in range(8)))

    assert hits["n"] == 1  # one fetch, shared by all eight callers
    assert all([r.name for r in rows] == [REMOTE_MODEL] for rows in results)


async def test_a_failed_refresh_does_not_poison_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    # The single-flight gate must not swallow the error for the callers waiting behind it, and a
    # failure must leave the cache empty rather than stamped fresh.
    manager = UpstreamManager("http://gpu-box:8080/v1")

    def _get(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", _get)
    with pytest.raises(RuntimeError, match="unreachable"):
        manager.models()
    with pytest.raises(RuntimeError, match="unreachable"):
        manager.models()  # still asks, rather than serving a "fresh" empty listing


# --- 6. the SSE contract reports truncation --------------------------------------------------


def _install_finish(monkeypatch: pytest.MonkeyPatch, reason: str, content: str = "") -> None:
    """Stub one runtime round that ends with ``reason``."""

    async def fake_stream_turn(
        http: Any, base_url: str, body: dict[str, Any], on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[dict[str, str]], dict[str, Any] | None, str | None]:
        if content:
            await agent._emit(on_token, content)  # noqa: SLF001
        return content, [], None, reason

    monkeypatch.setattr(agent, "_stream_turn", fake_stream_turn)


async def _chat_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    app = create_app(Settings(serve_model=None))
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    return _events(response.text)


async def test_a_length_capped_reply_is_reported_as_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    # The reported case: the whole max_tokens budget went to reasoning_content, so the stream
    # carried ZERO token frames and then a clean `done` — indistinguishable from a model that
    # answered nothing. finish_reason is the only thing that tells them apart.
    _install_finish(monkeypatch, "length")
    events = await _chat_events(monkeypatch)

    assert events[-1] == {"type": "done", "finish_reason": "length"}
    notice = next(e for e in events if e["type"] == "error")
    assert "cut off" in notice["detail"]  # a client that renders errors already shows it
    assert events.index(notice) < len(events) - 1  # before `done`, so it lands on the message


async def test_a_complete_reply_carries_its_finish_reason_and_no_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    # The additive half of the contract: `done` grows a field, and nothing else changes. A parser
    # that ignores unknown keys reads exactly what it read before.
    _install_finish(monkeypatch, "stop", content="hello")
    events = await _chat_events(monkeypatch)

    assert events[-1] == {"type": "done", "finish_reason": "stop"}
    assert not [e for e in events if e["type"] == "error"]
    assert [e["text"] for e in events if e["type"] == "token"] == ["hello"]


# --- 7. the agent loop reuses the caller's HTTP client ---------------------------------------


async def test_agent_run_uses_a_supplied_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_stream_turn(
        http: Any, base_url: str, body: dict[str, Any], on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[dict[str, str]], dict[str, Any] | None, str | None]:
        seen["http"] = http
        return "done", [], None, "stop"

    monkeypatch.setattr(agent, "_stream_turn", fake_stream_turn)
    from stabbur.tools import MCPToolset  # noqa: PLC0415 - only this test needs the empty toolset

    async with httpx.AsyncClient() as mine:
        await agent.run("http://runtime", [{"role": "user", "content": "hi"}], MCPToolset(), http=mine)
        assert seen["http"] is mine
        assert not mine.is_closed  # a client the caller owns is never closed by the loop

    # And with none supplied (the CLI / TUI path) the loop still opens its own.
    seen.clear()
    await agent.run("http://runtime", [{"role": "user", "content": "hi"}], MCPToolset())
    assert isinstance(seen["http"], httpx.AsyncClient)


async def test_chat_turns_reuse_the_apps_shared_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # One pool for the process, not one per message: the loop used to open (and tear down) its
    # own client per turn, paying a fresh handshake to the same runtime the /v1 proxy already
    # holds a keep-alive connection to.
    seen: dict[str, Any] = {}

    async def fake_run(*a: Any, **kwargs: Any) -> str:
        seen["http"] = kwargs.get("http")
        return ""

    monkeypatch.setattr(agent, "run", fake_run)

    app = create_app(Settings(serve_model=None))
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    finally:
        app.dependency_overrides.clear()

    assert seen["http"] is app.state.http
