"""Tests for the streaming ``/v1`` proxy (``stabbur.routers.serving.proxy``).

Only the no-model-loaded 409 is covered elsewhere (tests/test_api.py). These exercise the
happy relay (status/content-type/body forwarded verbatim) and — critically — the
runtime-reservation accounting: ``active_generations`` must return to 0 after a normal
stream, an upstream connect failure (V-10 / 502), and a mid-stream client disconnect, or
every later load/unload 409s permanently.

The upstream runtime is a tiny ASGI app mounted into an ``httpx.AsyncClient`` via
``ASGITransport``; that client replaces ``app.state.http`` (through the ``get_http``
dependency) so ``proxy_v1`` streams from it. See ``_base.py`` for
``_acquire_runtime``/``_release_runtime`` semantics.
"""

import json
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.types import Receive, Scope, Send

from stabbur.app import create_app
from stabbur.backends import Backends
from stabbur.config import Settings
from stabbur.routers import serving
from stabbur.routers.serving import proxy

# NOTE: httpx 0.28's ASGITransport fully buffers a response — it runs the app to completion
# before returning — so a stream cannot be observed mid-flight through the outer test client.
# Mid-stream reservation state is therefore observed from inside the upstream body generator
# (which runs *during* relay), and the disconnect path drives relay()'s generator directly.


class _FakeManager:
    """A backend reporting a loaded model so ``_acquire_runtime`` / proxy pass their guards."""

    current = type("M", (), {"load_target": Path("/models/x")})()
    base_url = "http://runtime"
    is_upstream = False  # read by the /v1/models discovery branch these tests never take


@pytest.fixture
def app() -> FastAPI:
    """App with a clean manager; a loaded model is faked per-test via dependency_overrides."""
    return create_app(Settings(serve_model=None))


@pytest.fixture
async def client(app: FastAPI):
    """Async client running the app's lifespan (so app.state is fully initialized)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _use_runtime(app: FastAPI, runtime_app: Any) -> AsyncClient:
    """Point the proxy at ``runtime_app`` (an ASGI app) and report a loaded model.

    Returns the upstream client so the test can close it; the proxy reads it via get_http.
    """
    upstream = AsyncClient(transport=ASGITransport(app=runtime_app), base_url="http://runtime")
    app.dependency_overrides[serving.get_manager] = lambda: _FakeManager()
    app.dependency_overrides[serving.get_http] = lambda: upstream
    return upstream


async def test_happy_path_forwards_status_content_type_and_body(app: FastAPI, client: AsyncClient) -> None:
    # The proxy is transparent: it forwards the upstream status code, content-type, and body
    # bytes verbatim (SSE deltas stream through unchanged).
    async def runtime_app(scope: Scope, receive: Receive, send: Send) -> None:
        assert scope["type"] == "http"
        # A non-200 status proves the code is forwarded (not defaulted to 200 by StreamingResponse).
        await send(
            {
                "type": "http.response.start",
                "status": 203,
                "headers": [(b"content-type", b"text/event-stream"), (b"x-runtime", b"yes")],
            }
        )
        for chunk in (b"data: hello\n\n", b"data: world\n\n", b"data: [DONE]\n\n"):
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    upstream = _use_runtime(app, runtime_app)
    try:
        r = await client.post("/v1/chat/completions", json={"messages": []})
        assert r.status_code == 203  # upstream status forwarded
        assert r.headers["content-type"] == "text/event-stream"  # content-type forwarded
        assert r.headers["x-runtime"] == "yes"  # arbitrary upstream headers pass through
        assert r.text == "data: hello\n\ndata: world\n\ndata: [DONE]\n\n"  # body verbatim
        assert app.state.active_generations == 0  # reservation released after the stream
    finally:
        app.dependency_overrides.clear()
        await upstream.aclose()


async def test_reservation_incremented_during_stream_and_released_after(app: FastAPI, client: AsyncClient) -> None:
    # active_generations must be 1 while the proxied stream is in flight (so a load/unload is
    # refused) and return to 0 once the response is consumed. The upstream body generator runs
    # *during* relay, so it samples the live reservation count at each yield.
    observed: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            observed.append(app.state.active_generations)
            yield b"data: first\n\n"
            observed.append(app.state.active_generations)
            yield b"data: [DONE]\n\n"

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())

    upstream = AsyncClient(transport=httpx.MockTransport(handler), base_url="http://runtime")
    app.dependency_overrides[serving.get_manager] = lambda: _FakeManager()
    app.dependency_overrides[serving.get_http] = lambda: upstream
    try:
        r = await client.post("/v1/chat/completions", json={"messages": []})
        assert r.status_code == 200
        assert r.text == "data: first\n\ndata: [DONE]\n\n"
        assert observed == [1, 1]  # reserved throughout the stream
        assert app.state.active_generations == 0  # released after the stream ends
    finally:
        app.dependency_overrides.clear()
        await upstream.aclose()


async def test_upstream_connect_failure_is_502_and_releases_reservation(app: FastAPI, client: AsyncClient) -> None:
    # If the runtime is unreachable, the proxy must 502 and release the reservation it took —
    # a leak here would 409 every subsequent load/unload permanently.
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    upstream = AsyncClient(transport=httpx.MockTransport(handler), base_url="http://runtime")
    app.dependency_overrides[serving.get_manager] = lambda: _FakeManager()
    app.dependency_overrides[serving.get_http] = lambda: upstream
    try:
        r = await client.post("/v1/chat/completions", json={"messages": []})
        assert r.status_code == 502
        assert "unreachable" in r.json()["detail"].lower()
        assert app.state.active_generations == 0  # no leak on the failure path
    finally:
        app.dependency_overrides.clear()
        await upstream.aclose()


async def test_client_disconnect_midstream_releases_reservation(app: FastAPI) -> None:
    # V-10: on a mid-stream client disconnect, Starlette closes the relay generator; its finally
    # must still release the reservation and close upstream. ASGITransport buffers the response
    # (so it can't reproduce a genuine mid-stream abort), so drive proxy_v1's StreamingResponse
    # generator directly and aclose() it after one chunk to simulate the disconnect.
    def handler(request: httpx.Request) -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            yield b"data: first\n\n"
            yield b"data: second\n\n"  # never reached — the client disconnects after the first

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())

    upstream = AsyncClient(transport=httpx.MockTransport(handler), base_url="http://runtime")

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [],
        "query_string": b"",
        "app": app,
    }
    request = Request(scope, cast(Receive, receive), cast(Send, lambda _m: None))

    try:
        resp = await proxy.proxy_v1(
            path="chat/completions",
            request=request,
            manager=cast(Backends, _FakeManager()),
            client=upstream,
            settings=Settings(serve_model=None),
        )
        assert app.state.active_generations == 1  # acquired before streaming
        assert isinstance(resp, StreamingResponse)  # the proxying branch, not discovery
        relay = cast(AsyncGenerator[bytes, None], resp.body_iterator)
        first = await relay.__anext__()
        assert first == b"data: first\n\n"
        assert app.state.active_generations == 1  # still reserved mid-stream
        await relay.aclose()  # Starlette closes the generator on disconnect
        assert app.state.active_generations == 0  # finally released it on disconnect
    finally:
        await upstream.aclose()


async def test_credentials_are_not_forwarded_to_the_runtime(app: FastAPI, client: AsyncClient) -> None:
    # The runtime we proxy to is a different trust domain — with --upstream it is another box
    # entirely — so this server's credentials must stop here. Authorization (stabbur's own bearer
    # token) and Cookie (the browser's cookies for the stabbur origin) are stripped from the
    # outbound request; everything else the caller sent still passes through.
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({k.lower(): v for k, v in request.headers.items()})

        async def body() -> AsyncIterator[bytes]:
            yield b'{"ok": true}'

        return httpx.Response(200, headers={"content-type": "application/json"}, content=body())

    upstream = AsyncClient(transport=httpx.MockTransport(handler), base_url="http://runtime")
    app.dependency_overrides[serving.get_manager] = lambda: _FakeManager()
    app.dependency_overrides[serving.get_http] = lambda: upstream
    try:
        r = await client.post(
            "/v1/chat/completions",
            json={"messages": []},
            headers={
                "authorization": "Bearer stabbur-token",
                "cookie": "session=abc",
                "proxy-authorization": "Basic Zm9vOmJhcg==",
                "x-keep-me": "yes",
            },
        )
        assert r.status_code == 200
        assert "authorization" not in seen
        assert "cookie" not in seen
        assert "proxy-authorization" not in seen
        assert seen["x-keep-me"] == "yes"  # only credentials are dropped, not arbitrary headers
    finally:
        app.dependency_overrides.clear()
        await upstream.aclose()


# --- mid-stream upstream death ----------------------------------------------------------------
# A runtime that dies mid-reply used to end the proxied stream CLEANLY: the client saw two chunks,
# no [DONE], and a successful close — which a tolerant SSE parser reads as a finished answer, so a
# truncated reply was shown as the model's whole reply. The only trace was a server-side traceback.


def _json_response(payload: dict[str, Any]) -> httpx.Response:
    """A STREAMING JSON response (the proxy reads its upstream with ``aiter_raw``)."""

    async def body() -> AsyncIterator[bytes]:
        yield json.dumps(payload).encode()

    return httpx.Response(200, headers={"content-type": "application/json"}, content=body())


def _dying_upstream(content_type: str) -> AsyncClient:
    """An upstream that sends one chunk and then drops the connection."""

    def handler(request: httpx.Request) -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            yield b'data: {"choices": [{"delta": {"content": "hel"}}]}\n\n'
            raise httpx.RemoteProtocolError("peer closed connection unexpectedly", request=request)

        return httpx.Response(200, headers={"content-type": content_type}, content=body())

    return AsyncClient(transport=httpx.MockTransport(handler), base_url="http://runtime")


async def _drive(app: FastAPI, upstream: AsyncClient, settings: Settings) -> Any:
    """Call the proxy directly (ASGITransport buffers, so a mid-flight stream needs the raw op)."""

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": [],
        "query_string": b"",
        "app": app,
    }
    request = Request(scope, cast(Receive, receive), cast(Send, lambda _m: None))
    return await proxy.proxy_v1(
        path="chat/completions",
        request=request,
        manager=cast(Backends, _FakeManager()),
        client=upstream,
        settings=settings,
    )


async def test_upstream_death_midstream_emits_an_error_frame(app: FastAPI) -> None:
    # SSE: the truncation must be VISIBLE. An OpenAI-shaped error frame goes out instead of a
    # silent clean close, and no [DONE] follows it — a parser can't read this as completion.
    upstream = _dying_upstream("text/event-stream")
    try:
        resp = await _drive(app, upstream, Settings(serve_model=None))
        assert isinstance(resp, StreamingResponse)
        chunks = [c async for c in cast(AsyncGenerator[bytes, None], resp.body_iterator)]
        assert b"hel" in chunks[0]  # the partial reply still reached the client
        error = json.loads(chunks[-1].removeprefix(b"data: "))["error"]
        assert error["type"] == "upstream_error"
        assert "upstream stream failed" in error["message"]
        assert not any(b"[DONE]" in c for c in chunks)  # never a completion marker
        assert app.state.active_generations == 0  # reservation released on the failure path
    finally:
        await upstream.aclose()


async def test_upstream_death_midstream_on_a_json_body_closes_abnormally(app: FastAPI) -> None:
    # Non-SSE: half a JSON document is unparseable anyway and an appended error object would only
    # make it invalid in a new way, so the response aborts. The reservation is still released.
    upstream = _dying_upstream("application/json")
    try:
        resp = await _drive(app, upstream, Settings(serve_model=None))
        assert isinstance(resp, StreamingResponse)
        body = cast(AsyncGenerator[bytes, None], resp.body_iterator)
        with pytest.raises(httpx.HTTPError):
            async for _ in body:
                pass
        assert app.state.active_generations == 0
    finally:
        await upstream.aclose()


# --- locked mode (serve --model) --------------------------------------------------------------
# /v1 enforces the lock too. Without it a client could list every id the backend serves and name
# another one, and an upstream router would hot-swap to it — evicting the locked model.

_REMOTE_ID = "/srv/models/some-model/some-model-Q4_K_M.gguf"  # what llama.cpp calls it: an absolute path


class _LockedUpstream:
    """A locked upstream backend whose own model id is a path on the serving machine."""

    current = type("M", (), {"name": _REMOTE_ID})()
    base_url = "http://runtime"
    is_upstream = True


class _LockedLocal:
    """A locked local backend: the loaded library model's name IS the stabbur name."""

    current = type("M", (), {"name": "some-model"})()
    base_url = "http://runtime"
    is_upstream = False


def _locked(app: FastAPI, manager: Any, runtime_app: Any) -> AsyncClient:
    """Point a locked app at ``runtime_app`` (an httpx handler) with ``manager`` as the backend."""
    upstream = AsyncClient(transport=httpx.MockTransport(runtime_app), base_url="http://runtime")
    app.dependency_overrides[serving.get_manager] = lambda: manager
    app.dependency_overrides[serving.get_http] = lambda: upstream
    app.dependency_overrides[serving.get_conf] = lambda: Settings(serve_model="some-model")
    return upstream


async def test_locked_models_lists_only_the_locked_model(app: FastAPI, client: AsyncClient) -> None:
    # The backend's own listing (every model on the remote) is not a locked server's answer, and
    # the id it presents is the stabbur name — not the absolute path llama.cpp reports.
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a locked /v1/models must not be forwarded to the backend")

    upstream = _locked(app, _LockedUpstream(), handler)
    try:
        body = (await client.get("/v1/models")).json()
        assert [m["id"] for m in body["data"]] == ["some-model"]
        assert "models" not in body  # llama.cpp's non-OpenAI extra key never rides through
    finally:
        app.dependency_overrides.clear()
        await upstream.aclose()


async def test_locked_completion_is_pinned_to_the_locked_model(app: FastAPI, client: AsyncClient) -> None:
    # A client naming another model must NOT reach the router with that name (it would hot-swap
    # and evict the lock): the forwarded body says the locked model, under the backend's own id.
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _json_response({"model": _REMOTE_ID})

    upstream = _locked(app, _LockedUpstream(), handler)
    try:
        r = await client.post("/v1/chat/completions", json={"model": "some-other-model", "messages": []})
        assert seen["model"] == _REMOTE_ID  # translated to what the backend knows it as
        assert r.json()["model"] == "some-model"  # and echoed back under the stabbur name
    finally:
        app.dependency_overrides.clear()
        await upstream.aclose()


async def test_locked_completion_pins_a_body_that_names_no_model(app: FastAPI, client: AsyncClient) -> None:
    # A router with nothing named serves its own default, which need not be the locked model.
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _json_response({"ok": True})

    upstream = _locked(app, _LockedUpstream(), handler)
    try:
        await client.post("/v1/chat/completions", json={"messages": []})
        assert seen["model"] == _REMOTE_ID
    finally:
        app.dependency_overrides.clear()
        await upstream.aclose()


async def test_locked_stream_reports_the_stabbur_name(app: FastAPI, client: AsyncClient) -> None:
    # Every SSE delta carries the model id; in locked mode it must read as the stabbur name so the
    # extension / `stabbur chat --server` can match it, and the library path never leaves the host.
    def handler(request: httpx.Request) -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            frame = json.dumps({"model": _REMOTE_ID, "choices": [{"delta": {"content": "hi"}}]}).encode()
            yield b"data: " + frame[:20]  # split mid-field: the rewrite is line-buffered
            yield frame[20:] + b"\n\ndata: [DONE]\n\n"

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body())

    upstream = _locked(app, _LockedUpstream(), handler)
    try:
        text = (await client.post("/v1/chat/completions", json={"messages": []})).text
        assert _REMOTE_ID not in text
        assert json.loads(text.splitlines()[0].removeprefix("data: "))["model"] == "some-model"
        assert text.endswith("data: [DONE]\n\n")  # the rest of the stream is untouched
    finally:
        app.dependency_overrides.clear()
        await upstream.aclose()


async def test_locked_local_backend_pins_to_the_library_name(app: FastAPI, client: AsyncClient) -> None:
    # Local mode has no second id to translate to: the single-model runtime ignores the field, so
    # the pin is the stabbur name itself. The lock is still enforced.
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _json_response({"model": "whatever"})

    upstream = _locked(app, _LockedLocal(), handler)
    try:
        r = await client.post("/v1/chat/completions", json={"model": "some-other-model", "messages": []})
        assert seen["model"] == "some-model"
        assert r.json()["model"] == "some-model"
        assert [m["id"] for m in (await client.get("/v1/models")).json()["data"]] == ["some-model"]
    finally:
        app.dependency_overrides.clear()
        await upstream.aclose()


async def test_unlocked_proxy_is_still_transparent(app: FastAPI, client: AsyncClient) -> None:
    # The rewrites are locked-mode only: an unlocked server forwards the caller's model verbatim
    # and hands back the backend's own listing, exactly as before.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return _json_response({"object": "list", "data": [{"id": "a"}, {"id": "b"}]})
        return _json_response({"model": json.loads(request.content)["model"]})

    upstream = AsyncClient(transport=httpx.MockTransport(handler), base_url="http://runtime")
    app.dependency_overrides[serving.get_manager] = lambda: _LockedUpstream()  # upstream, but NOT locked
    app.dependency_overrides[serving.get_http] = lambda: upstream
    try:
        r = await client.post("/v1/chat/completions", json={"model": "anything", "messages": []})
        assert r.json()["model"] == "anything"
        assert [m["id"] for m in (await client.get("/v1/models")).json()["data"]] == ["a", "b"]
    finally:
        app.dependency_overrides.clear()
        await upstream.aclose()


async def test_v1_models_discovery_scan_runs_off_the_event_loop(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With nothing loaded, discovery answers from library.scan() — a filesystem walk over every
    # library root. On the loop it stalls every other request (a status poll, a live stream), so it
    # must run in a worker thread like every other scan in the serving layer.
    import threading

    seen: list[str] = []

    def _scan() -> list[Any]:
        seen.append(threading.current_thread().name)
        return []

    monkeypatch.setattr("stabbur.library.scan", _scan)
    r = await client.get("/v1/models")
    assert r.status_code == 200 and r.json() == {"object": "list", "data": []}
    assert seen and seen[0] != threading.main_thread().name  # not the event loop's thread
