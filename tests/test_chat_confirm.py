"""Tests for the /api/chat per-action write-confirmation protocol (Round 3, Wave 2 Agent D).

Covers the confirm endpoint (404 paths), the full gated flow (approve / decline / timeout),
policy resolution (free-play + readonly assistant stay ungated), and the app-level guard.

The interleaving tests run /api/chat as a background task and answer the confirmation once its
future is registered in ``app.state.pending_confirmations`` — httpx's ASGITransport buffers the
whole SSE body before returning, so a "read the stream, then post the answer" client loop would
deadlock (the server blocks on the future before the client ever sees the ``confirm`` event).
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from kodo import agent
from kodo.app import create_app
from kodo.config import Settings
from kodo.routers import serving


@pytest.fixture
def app() -> FastAPI:
    """App with a clean (no model loaded) manager and an empty confirmation registry."""
    return create_app(Settings(serve_model=None))


@pytest.fixture
async def client(app: FastAPI):
    """Async client running the app's lifespan."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class FakeManager:
    """A model is 'loaded' so /api/chat proceeds into the stream."""

    current = type("M", (), {"load_target": Path("/models/x")})()
    base_url = "http://runtime"


def _events(text: str) -> list[dict[str, Any]]:
    """Parse the SSE data events from a buffered /api/chat response body."""
    return [json.loads(line[len("data: ") :]) for line in text.splitlines() if line.startswith("data: ")]


def _install_confirming_run(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    """Patch agent.run with a stub that consults on_confirm once, then reports the outcome."""

    async def fake_run(
        base: str,
        messages: list[dict[str, Any]],
        toolset: Any,
        max_tokens: int | None,
        on_event: Any,
        on_token: Any,
        **kw: Any,
    ) -> str:
        captured["on_confirm"] = kw.get("on_confirm")
        captured["policy"] = kw.get("confirm_policy")
        on_confirm = kw.get("on_confirm")
        if on_confirm is None:
            await on_event("result", "ran ungated")
            await on_token("ok")
            return "ok"
        approved = await on_confirm("dhis2__write_thing", {"secret": "z" * 500})
        if approved:
            await on_event("result", "did the write")
        else:
            await on_event("result", "error: user declined this action")
        await on_token("done")
        return "done"

    monkeypatch.setattr(agent, "run", fake_run)


async def _run_gated(
    app: FastAPI, client: AsyncClient, request_json: dict[str, Any], approve: bool
) -> list[dict[str, Any]]:
    """Drive one gated /api/chat turn: start it, answer the confirmation, return its SSE events."""
    holder: dict[str, Any] = {}

    async def collect() -> None:
        holder["resp"] = await client.post("/api/chat", json=request_json)

    task = asyncio.create_task(collect())
    try:
        async with asyncio.timeout(10):
            while not app.state.pending_confirmations:
                await asyncio.sleep(0.01)  # let the agent-loop task reach on_confirm + register the future
            cid = next(iter(app.state.pending_confirmations))
            r = await client.post("/api/chat/confirm", json={"id": cid, "approve": approve})
            assert r.status_code == 200
            assert r.json() == {"ok": True}
            await task
    finally:
        task.cancel()
    return _events(holder["resp"].text)


# --- confirm endpoint: 404 paths + set_result -------------------------------------------------


async def test_confirm_unknown_id_is_404(client: AsyncClient) -> None:
    r = await client.post("/api/chat/confirm", json={"id": "does-not-exist", "approve": True})
    assert r.status_code == 404


async def test_confirm_resolving_twice_second_is_404(app: FastAPI, client: AsyncClient) -> None:
    # Register a future directly (as on_confirm does) and resolve it once via the endpoint.
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[bool] = loop.create_future()
    app.state.pending_confirmations["cid1"] = fut

    r1 = await client.post("/api/chat/confirm", json={"id": "cid1", "approve": True})
    assert r1.status_code == 200
    assert r1.json() == {"ok": True}
    assert fut.result() is True

    # The future is now done() → a second resolution finds nothing to answer.
    r2 = await client.post("/api/chat/confirm", json={"id": "cid1", "approve": False})
    assert r2.status_code == 404


# --- app-level guard inheritance --------------------------------------------------------------


async def test_confirm_cross_site_is_blocked(client: AsyncClient) -> None:
    # The route inherits the app-level cross-site guard on /api (no per-route auth needed):
    # a drive-by browser POST is rejected before the handler runs.
    r = await client.post(
        "/api/chat/confirm",
        json={"id": "x", "approve": True},
        headers={"sec-fetch-site": "cross-site"},
    )
    assert r.status_code == 403


# --- full gated flow: approve / decline -------------------------------------------------------


async def test_gated_flow_approve_runs_tool(app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    captured: dict[str, Any] = {}
    _install_confirming_run(monkeypatch, captured)
    try:
        events = await _run_gated(
            app, client, {"messages": [{"role": "user", "content": "hi"}], "confirm_tools": "all"}, approve=True
        )
    finally:
        app.dependency_overrides.clear()

    assert captured["policy"] == "all"
    assert captured["on_confirm"] is not None
    confirm = next(e for e in events if e["type"] == "confirm")
    assert confirm["tool"] == "dhis2__write_thing"
    assert len(confirm["args"]["secret"]) < 500  # args are capped (the 500-char string is trimmed)
    resolved = next(e for e in events if e["type"] == "confirm_resolved")
    assert resolved["id"] == confirm["id"]
    assert resolved["approved"] is True
    assert resolved["reason"] == "user"
    assert any(e["type"] == "tool" and e.get("detail") == "did the write" for e in events)
    # The registry is emptied once the confirmation resolves (no leak).
    assert app.state.pending_confirmations == {}


async def test_gated_flow_decline_skips_tool(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    captured: dict[str, Any] = {}
    _install_confirming_run(monkeypatch, captured)
    try:
        events = await _run_gated(
            app, client, {"messages": [{"role": "user", "content": "hi"}], "confirm_tools": "all"}, approve=False
        )
    finally:
        app.dependency_overrides.clear()

    resolved = next(e for e in events if e["type"] == "confirm_resolved")
    assert resolved["approved"] is False
    assert resolved["reason"] == "user"
    assert any(e["type"] == "tool" and e.get("detail") == "error: user declined this action" for e in events)
    assert app.state.pending_confirmations == {}


# --- timeout path -----------------------------------------------------------------------------


async def test_gated_flow_timeout_denies(app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    # A tiny timeout so a never-answered confirm auto-denies fast (no client resolution here).
    monkeypatch.setattr(app.state.settings, "confirm_timeout", 0.05)
    captured: dict[str, Any] = {}
    _install_confirming_run(monkeypatch, captured)
    try:
        r = await client.post(
            "/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "confirm_tools": "all"}
        )
        assert r.status_code == 200
        events = _events(r.text)
    finally:
        app.dependency_overrides.clear()

    resolved = next(e for e in events if e["type"] == "confirm_resolved")
    assert resolved["approved"] is False
    assert resolved["reason"] == "timeout"
    assert any(e["type"] == "tool" and e.get("detail") == "error: user declined this action" for e in events)
    assert app.state.pending_confirmations == {}


# --- policy resolution: free-play + readonly assistant stay ungated ---------------------------


async def test_freeplay_no_assistant_resolves_to_none(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No assistant (free-play) → policy "none": no confirm channel, tools run as before.
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    app.state.assistant = None
    captured: dict[str, Any] = {}
    _install_confirming_run(monkeypatch, captured)
    try:
        r = await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        assert '"type": "confirm"' not in r.text
    finally:
        app.dependency_overrides.clear()
    assert captured["policy"] == "none"
    assert captured["on_confirm"] is None


async def test_readonly_assistant_resolves_to_none(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A readonly assistant also stays ungated (no behavior change for read-only targets).
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    app.state.assistant = type("A", (), {"readonly": True})()
    captured: dict[str, Any] = {}
    _install_confirming_run(monkeypatch, captured)
    try:
        r = await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        assert '"type": "confirm"' not in r.text
    finally:
        app.dependency_overrides.clear()
    assert captured["policy"] == "none"
    assert captured["on_confirm"] is None


async def test_non_readonly_assistant_resolves_to_writes(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A non-readonly assistant defaults to gating writes (no explicit confirm_tools needed).
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    app.state.assistant = type("A", (), {"readonly": False})()
    captured: dict[str, Any] = {}
    _install_confirming_run(monkeypatch, captured)
    try:
        events = await _run_gated(app, client, {"messages": [{"role": "user", "content": "hi"}]}, approve=True)
    finally:
        app.dependency_overrides.clear()
    assert captured["policy"] == "writes"
    assert captured["on_confirm"] is not None
    assert any(e["type"] == "confirm" for e in events)
