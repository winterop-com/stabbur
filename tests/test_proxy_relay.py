"""Tests for the streaming ``/v1`` proxy (``kodo.routers.serving.proxy``).

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

from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.types import Receive, Scope, Send

from kodo.app import create_app
from kodo.config import Settings
from kodo.routers import serving
from kodo.routers.serving import proxy
from kodo.server import ServerManager

# NOTE: httpx 0.28's ASGITransport fully buffers a response — it runs the app to completion
# before returning — so a stream cannot be observed mid-flight through the outer test client.
# Mid-stream reservation state is therefore observed from inside the upstream body generator
# (which runs *during* relay), and the disconnect path drives relay()'s generator directly.


class _FakeManager:
    """A manager reporting a loaded model so ``_acquire_runtime`` / proxy pass their guards."""

    current = type("M", (), {"load_target": Path("/models/x")})()
    base_url = "http://runtime"


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
            manager=cast(ServerManager, _FakeManager()),
            client=upstream,
        )
        assert app.state.active_generations == 1  # acquired before streaming
        relay = cast(AsyncGenerator[bytes, None], resp.body_iterator)
        first = await relay.__anext__()
        assert first == b"data: first\n\n"
        assert app.state.active_generations == 1  # still reserved mid-stream
        await relay.aclose()  # Starlette closes the generator on disconnect
        assert app.state.active_generations == 0  # finally released it on disconnect
    finally:
        await upstream.aclose()
