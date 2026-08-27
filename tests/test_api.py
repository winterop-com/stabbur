"""Tests for the serving API (status + proxy guards), no real runtime needed."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from stabbur import agent
from stabbur import library as library_ops
from stabbur.app import create_app
from stabbur.config import Settings
from stabbur.library import LibraryModel
from stabbur.models import ModelFormat
from stabbur.routers import serving
from stabbur.routers.serving import proxy
from stabbur.runtime import sampling


@pytest.fixture
def app() -> FastAPI:
    """App with a clean (no model loaded) manager."""
    return create_app(Settings(serve_model=None))


@pytest.fixture
async def client(app: FastAPI):
    """Async client running the app's lifespan."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_status_reports_stopped_when_no_model(client: AsyncClient) -> None:
    response = await client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "stopped"
    assert body["model"] is None
    assert body["locked"] is False
    assert body["upstream"] is None  # this stabbur spawns its own runtimes; there is no remote to name


async def test_status_exposes_project_model(app: FastAPI, client: AsyncClient) -> None:
    # /api/status surfaces the project's bound model (stabbur.toml [project].model),
    # which the web UI auto-loads on open. The lifespan (which sets this from
    # project.load()) doesn't run under ASGITransport, so set app.state directly.
    app.state.project_model = "acme/widget-3b"
    body = (await client.get("/api/status")).json()
    assert body["project_model"] == "acme/widget-3b"


async def test_cross_site_mutating_request_blocked(client: AsyncClient) -> None:
    # A drive-by page's browser marks the request cross-site; mutating calls are
    # rejected before reaching the handler (so no model load / tool run).
    r = await client.post("/api/load/whatever", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    r = await client.post("/api/chat", json={"messages": []}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    # POST /models/{source}/pull downloads/copies files to disk — also guarded (a
    # drive-by page must not be able to trigger a background pull side effect).
    r = await client.post("/models/huggingface/pull?name=whatever", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


async def test_same_origin_and_non_browser_not_blocked(client: AsyncClient) -> None:
    # The served SPA (same-origin) and non-browser clients (no Sec-Fetch-Site, e.g.
    # curl/CLI) pass the guard — they get the handler's own status, never 403.
    assert (await client.post("/api/load/ghost", headers={"sec-fetch-site": "same-origin"})).status_code != 403
    assert (await client.post("/api/load/ghost")).status_code != 403
    # Safe (read-only) methods are never guarded, even cross-site.
    assert (await client.get("/api/status", headers={"sec-fetch-site": "cross-site"})).status_code == 200


async def test_allowlisted_origin_not_blocked() -> None:
    # An explicitly-configured origin (extension/dev) is allowed cross-site.
    app = create_app(Settings(serve_model=None, cors_origins=["http://ext.local"]))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as inner:
        r = await inner.post(
            "/api/load/ghost",
            headers={"sec-fetch-site": "cross-site", "origin": "http://ext.local"},
        )
        assert r.status_code != 403


async def test_wildcard_cors_does_not_bypass_cross_site_guard() -> None:
    # cors_origins=["*"] enables CORS reads but must NOT exempt mutating calls from
    # the cross-site guard, or a wildcard config re-opens the tool-execution hole.
    app = create_app(Settings(serve_model=None, cors_origins=["*"]))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as inner:
        r = await inner.post(
            "/api/load/ghost",
            headers={"sec-fetch-site": "cross-site", "origin": "https://evil.example"},
        )
        assert r.status_code == 403


async def test_missing_sec_fetch_falls_back_to_origin(client: AsyncClient) -> None:
    # Old Safari / embedded WebViews omit Sec-Fetch-Site. Fall back to Origin so a cross-host
    # POST is still blocked, while genuine non-browser clients (no Origin) pass (V-13). The test
    # client's Host is "test".
    blocked = await client.post("/api/load/ghost", headers={"origin": "http://evil.example"})
    assert blocked.status_code == 403  # cross-host Origin, no Sec-Fetch-Site
    same = await client.post("/api/load/ghost", headers={"origin": "http://test"})
    assert same.status_code != 403  # same-host Origin (the served SPA on old Safari)
    assert (await client.post("/api/load/ghost")).status_code != 403  # no Origin → non-browser client


async def test_auth_token_required_when_set() -> None:
    # With auth_token set, every guarded route (any method) needs Authorization: Bearer <token>,
    # so a LAN-exposed server isn't unauthenticated (V-14).
    app = create_app(Settings(serve_model=None, auth_token="s3cret"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as inner:
        assert (await inner.get("/api/status")).status_code == 401  # no token
        assert (await inner.get("/api/status", headers={"authorization": "Bearer nope"})).status_code == 401  # wrong
        assert (await inner.get("/api/status", headers={"authorization": "Bearer s3cret"})).status_code != 401  # right
        assert (await inner.get("/health")).status_code != 401  # health is unauthenticated (readiness probes)
        assert (await inner.options("/api/status")).status_code != 401  # CORS preflight carries no Authorization


async def test_no_auth_required_by_default(client: AsyncClient) -> None:
    # Empty auth_token (the loopback default) → no bearer required; the guard is a no-op.
    assert (await client.get("/api/status")).status_code != 401


async def test_non_ascii_authorization_header_is_a_clean_401() -> None:
    # An Authorization header is attacker-controlled, and secrets.compare_digest raises TypeError
    # on a str holding any non-ASCII character — so a byte of UTF-8 in the header used to be an
    # unhandled 500 (an auth check that crashes instead of denying). Sent as raw bytes because
    # that is what a client can put on the wire; Starlette decodes headers as latin-1.
    app = create_app(Settings(serve_model=None, auth_token="s3cret"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as inner:
        r = await inner.get("/api/status", headers={b"authorization": "Bearer tökén".encode()})
        assert r.status_code == 401
        assert r.headers["www-authenticate"] == "Bearer"

    # A non-ASCII token in the config is the same trap from the other side: it must still reject
    # a wrong token and accept the right one, not 500 on every guarded request.
    app = create_app(Settings(serve_model=None, auth_token="pässord"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as inner:
        assert (await inner.get("/api/status", headers={b"authorization": b"Bearer nope"})).status_code == 401
        # latin-1 because that is the encoding Starlette decodes header bytes with, so this is
        # the byte sequence that round-trips to the configured token.
        right = await inner.get("/api/status", headers={b"authorization": "Bearer pässord".encode("latin-1")})
        assert right.status_code != 401


async def test_rejections_carry_cors_headers_for_an_allowlisted_origin() -> None:
    # The rejection must be READABLE by an allow-listed caller (the Chrome extension is the
    # documented one): without Access-Control-Allow-Origin on the 401 its browser refuses the
    # response body and the user sees an opaque network error instead of "authentication
    # required". This is what the middleware ordering in create_app buys.
    app = create_app(Settings(serve_model=None, auth_token="s3cret", cors_origins=["http://ext.local"]))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as inner:
        r = await inner.get("/api/status", headers={"origin": "http://ext.local"})
        assert r.status_code == 401
        assert r.headers["access-control-allow-origin"] == "http://ext.local"

    # Same for the cross-site 403. A wildcard CORS config still does not exempt the mutating
    # call (see the test above) — it only makes the refusal legible.
    app = create_app(Settings(serve_model=None, cors_origins=["*"]))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as inner:
        r = await inner.post(
            "/api/load/ghost",
            headers={"sec-fetch-site": "cross-site", "origin": "https://evil.example"},
        )
        assert r.status_code == 403
        assert r.headers["access-control-allow-origin"] == "*"


async def test_api_docs_are_behind_the_token_when_one_is_set() -> None:
    # /docs, /redoc and /openapi.json describe every route and body shape — including the ones
    # that load models and run tools. On an exposed bind they must not be readable without the
    # token; on the loopback default (no token) they stay open like everything else.
    app = create_app(Settings(serve_model=None, auth_token="s3cret"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as inner:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await inner.get(path)).status_code == 401, path
            assert (await inner.get(path, headers={"authorization": "Bearer s3cret"})).status_code == 200, path

    app = create_app(Settings(serve_model=None))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as inner:
        assert (await inner.get("/openapi.json")).status_code == 200


async def test_concurrent_loads_are_serialized(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    # ServerManager has no internal lock, so two /api/load calls must not run
    # manager.load() at the same time (interleaving corrupts its process state).
    import asyncio
    import threading
    import time

    fake = LibraryModel(name="m", model_format=ModelFormat.gguf, path=Path("/x"), load_target=Path("/x/m.gguf"))
    monkeypatch.setattr("stabbur.routers.serving.chat.library_ops.find", lambda name: [fake])
    monkeypatch.setattr("stabbur.routers.serving.chat.runtime.runnable_error", lambda m: None)

    active = 0
    max_active = 0
    guard = threading.Lock()

    def slow_load(model: Any, n_ctx: int | None = None) -> None:
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.1)
        with guard:
            active -= 1

    # Patch the wrapped backend, not the facade: patching Backends.load would shadow the
    # delegation under test and prove nothing about the seam the route actually calls through.
    monkeypatch.setattr(app.state.manager.backend, "load", slow_load)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r1, r2 = await asyncio.gather(c.post("/api/load/m"), c.post("/api/load/m"))
    assert r1.status_code == 200 and r2.status_code == 200
    assert max_active == 1  # never two loads mutating state at once


async def test_load_and_unload_rejected_while_generating(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A running generation reserves the runtime (active_generations > 0); load/unload
    # must refuse (409) so the runtime it's streaming from is never swapped/killed.
    fake = LibraryModel(name="m", model_format=ModelFormat.gguf, path=Path("/x"), load_target=Path("/x/m.gguf"))
    monkeypatch.setattr("stabbur.routers.serving.chat.library_ops.find", lambda name: [fake])
    monkeypatch.setattr("stabbur.routers.serving.chat.runtime.runnable_error", lambda m: None)
    monkeypatch.setattr(app.state.manager.backend, "load", lambda *a, **k: None)
    monkeypatch.setattr(app.state.manager.backend, "stop", lambda *a, **k: None)

    app.state.active_generations = 1
    assert (await client.post("/api/load/m")).status_code == 409
    assert (await client.post("/api/unload")).status_code == 409

    # With nothing generating, both proceed (the reject is conditional, not a wall).
    app.state.active_generations = 0
    assert (await client.post("/api/load/m")).status_code == 200
    assert (await client.post("/api/unload")).status_code == 200


async def test_proxy_requires_a_loaded_model(client: AsyncClient) -> None:
    response = await client.post("/v1/chat/completions", json={"messages": []})
    assert response.status_code == 409


async def test_load_unknown_model_is_404(client: AsyncClient) -> None:
    response = await client.post("/api/load/definitely-not-a-real-model")
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("fmt", "is_ollama"),
    [(ModelFormat.safetensors, False), (ModelFormat.gguf, True)],
)
async def test_load_unrunnable_model_is_422_not_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, fmt: ModelFormat, is_ollama: bool
) -> None:
    # safetensors (convert/fine-tune source) and Ollama-native entries resolve to
    # a match but aren't runnable by stabbur — the API must reject them cleanly (422),
    # not pass them to manager.load and surface a 500 / ValueError traceback.
    p = Path("/tmp/x")
    model = LibraryModel(name="pub/X", model_format=fmt, is_ollama=is_ollama, path=p, load_target=p / "w")
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [model])
    response = await client.post("/api/load/pub/X")
    assert response.status_code == 422, response.text


def test_create_app_honors_settings_runtime_port() -> None:
    # The factory must use the runtime_port from the settings it's given.
    assert create_app(Settings(runtime_port=8123)).state.manager.backend._port == 8123


def test_create_app_autopicks_when_runtime_port_none() -> None:
    assert create_app(Settings(runtime_port=None)).state.manager.backend._port > 0


def test_reload_worker_honors_runtime_port_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulates a --reload worker: fresh process, no in-memory override, reads
    # STABBUR_RUNTIME_PORT that serve() propagated into the environment.
    from stabbur import config
    from stabbur.config import get_settings

    monkeypatch.setattr(config, "_runtime_port_override", None)
    monkeypatch.setenv("STABBUR_RUNTIME_PORT", "8124")
    get_settings.cache_clear()
    try:
        assert create_app().state.manager.backend._port == 8124
    finally:
        get_settings.cache_clear()


async def test_library_lists_runnable_models(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    gen = LibraryModel(
        name="pub/Chat-GGUF", model_format=ModelFormat.gguf, path=Path("/tmp/a"), load_target=Path("/tmp/a")
    )
    emb = LibraryModel(
        name="st/embed",
        model_format=ModelFormat.safetensors,
        generative=False,
        path=Path("/tmp/b"),
        load_target=Path("/tmp/b"),
    )
    monkeypatch.setattr(library_ops, "scan", lambda: [gen, emb])
    body = (await client.get("/api/library")).json()
    names = [m["name"] for m in body]
    assert names == ["pub/Chat-GGUF"]  # generative only; embedding excluded


async def test_api_doctor_returns_report(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # /api/doctor mirrors `stabbur doctor`: a list of typed health checks.
    monkeypatch.setattr(library_ops, "scan", lambda *a, **k: [])
    body = (await client.get("/api/doctor")).json()
    assert "checks" in body and isinstance(body["checks"], list)
    names = {c["name"] for c in body["checks"]}
    assert "llama.cpp (GGUF)" in names  # runtime checks are always present
    for c in body["checks"]:
        assert c["status"] in {"ok", "warn", "fail"}


async def test_api_chat_streams_tokens_and_tool_events(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # /api/chat runs the agent loop server-side and streams typed SSE: tokens plus
    # tool call/result events (which the raw /v1 proxy can't surface).
    class FakeManager:
        current = type("M", (), {"load_target": Path("/models/x")})()  # a model is "loaded"
        base_url = "http://runtime"

    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()

    async def fake_run(
        base: str,
        messages: list[dict[str, Any]],
        toolset: Any,
        max_tokens: int | None,
        on_event: Callable[[str, str], Any],
        on_token: Callable[[str], Any],
        **_: Any,
    ) -> str:
        # The /api/chat sinks are async (bounded-queue backpressure, V-12); await them like
        # the real agent loop does.
        await on_token("Hel")
        await on_event("call", "today()")
        await on_event("result", "Wednesday")
        await on_token("lo")
        return "Hello"

    monkeypatch.setattr(agent, "run", fake_run)
    try:
        r = await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
        assert r.status_code == 200
        body = r.text
        assert '"type": "token"' in body and '"text": "Hel"' in body
        assert '"kind": "call"' in body and '"kind": "result"' in body
        assert '"type": "done"' in body
    finally:
        app.dependency_overrides.clear()


async def test_api_chat_applies_default_max_tokens(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # /api/chat caps generation with the configured default when the client omits max_tokens
    # (bounds a runaway small model), and honors an explicit value when given.
    class FakeManager:
        current = type("M", (), {"load_target": Path("/models/x")})()
        base_url = "http://runtime"

    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    seen: list[int | None] = []

    async def fake_run(
        base: str, messages: list[dict[str, Any]], toolset: Any, max_tokens: int | None, *a: Any, **_: Any
    ) -> str:
        seen.append(max_tokens)
        return "ok"

    monkeypatch.setattr(agent, "run", fake_run)
    try:
        await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
        assert seen[-1] == 4096  # default cap applied when omitted
        await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 128})
        assert seen[-1] == 128  # explicit value wins
    finally:
        app.dependency_overrides.clear()


async def test_api_chat_default_max_tokens_zero_is_unbounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # default_max_tokens <= 0 disables the cap (unbounded), for power users who opt out.
    inner = create_app(Settings(serve_model=None, default_max_tokens=0))

    class FakeManager:
        current = type("M", (), {"load_target": Path("/models/x")})()
        base_url = "http://runtime"

    inner.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    seen: list[int | None] = []

    async def fake_run(
        base: str, messages: list[dict[str, Any]], toolset: Any, max_tokens: int | None, *a: Any, **_: Any
    ) -> str:
        seen.append(max_tokens)
        return "ok"

    monkeypatch.setattr(agent, "run", fake_run)
    transport = ASGITransport(app=inner)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert seen[-1] is None  # unbounded


async def test_api_unload_stops_the_runtime(app: FastAPI, client: AsyncClient) -> None:
    # /api/unload ejects the model by calling manager.stop(), returning stopped status.
    stopped = {"n": 0}

    class FakeManager:
        current = None
        n_ctx = None
        last_error = None
        is_upstream = False  # /api/status reports base_url only for an upstream backend
        name = "local"  # the active backend's name, which /api/status reports as `backend`

        def stop(self) -> None:
            stopped["n"] += 1

        async def state(self) -> Any:
            return type("S", (), {"value": "stopped"})()

    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    try:
        r = await client.post("/api/unload")
        assert r.status_code == 200
        assert r.json()["state"] == "stopped"
        assert stopped["n"] == 1  # runtime actually stopped
    finally:
        app.dependency_overrides.clear()


async def test_api_unload_rejected_when_locked(app: FastAPI, client: AsyncClient) -> None:
    # A locked (single-model) server must refuse ejecting its bound model.
    app.dependency_overrides[serving.get_conf] = lambda: Settings(serve_model="some-model")
    try:
        assert (await client.post("/api/unload")).status_code == 409
    finally:
        app.dependency_overrides.clear()


async def test_api_chat_requires_loaded_model(client: AsyncClient) -> None:
    r = await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 409


async def test_api_speak_unknown_kokoro_voice_is_422(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # An unknown Kokoro voice id is a client error, not an engine failure: it must 422, not 500.
    from types import SimpleNamespace

    from stabbur.routers.serving import voice as voice_router

    monkeypatch.setattr(voice_router.kokoro, "available", lambda: True)
    monkeypatch.setattr(voice_router.kokoro, "voices", lambda: [SimpleNamespace(id="af_heart")])
    r = await client.post("/api/speak", json={"text": "hello", "voice": "kokoro:not_a_voice"})
    assert r.status_code == 422


async def test_api_model_returns_card(client: AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# My Model\n\nUsage info here.")
    model = LibraryModel(name="pub/M", model_format=ModelFormat.gguf, path=tmp_path, load_target=tmp_path / "w.gguf")
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [model])
    body = (await client.get("/api/model", params={"name": "pub/M"})).json()
    assert body["name"] == "pub/M"
    assert "My Model" in (body["card"] or "")


async def test_api_chat_use_tools_flag_drops_tools(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # use_tools=false must run the loop with an empty toolset (for non-tool models),
    # even when the server has MCP tools configured.
    from stabbur.tools import MCPToolset

    class FakeManager:
        current = type("M", (), {"load_target": Path("/models/x")})()
        base_url = "http://runtime"

    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    ts = MCPToolset()
    ts.schemas = [{"type": "function", "function": {"name": "today"}}]
    app.state.toolset = ts
    seen: dict[str, list[str]] = {}

    async def fake_run(
        base: str,
        messages: list[dict[str, Any]],
        toolset: MCPToolset,
        max_tokens: int | None,
        on_event: Callable[[str, str], None],
        on_token: Callable[[str], None],
        **_: Any,
    ) -> str:
        seen["names"] = toolset.names
        return ""

    monkeypatch.setattr(agent, "run", fake_run)
    try:
        await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "use_tools": False})
        assert seen["names"] == []  # tools dropped
        await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "use_tools": True})
        assert seen["names"] == ["today"]  # tools attached
    finally:
        app.dependency_overrides.clear()


async def test_api_chat_enabled_tools_allowlist_is_explicit(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The wire contract the web UI's per-chat allow-list rides on: an absent enabled_tools means
    # every attached tool, an explicit list narrows to it, and `[]` really is "no tools" — not an
    # empty-means-unset shorthand, which is what let a server switched on for one chat stay
    # callable in every later one.
    from stabbur.tools import MCPToolset

    class FakeManager:
        current = type("M", (), {"load_target": Path("/models/x")})()
        base_url = "http://runtime"

    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    ts = MCPToolset()
    ts.schemas = [
        {"type": "function", "function": {"name": "datetime__today"}},
        {"type": "function", "function": {"name": "files__read"}},
    ]
    app.state.toolset = ts
    seen: dict[str, list[str]] = {}

    async def fake_run(base: str, messages: list[dict[str, Any]], toolset: MCPToolset, *a: Any, **_: Any) -> str:
        seen["names"] = toolset.names
        return ""

    monkeypatch.setattr(agent, "run", fake_run)
    body: dict[str, Any] = {"messages": [{"role": "user", "content": "hi"}]}
    try:
        await client.post("/api/chat", json=body)
        assert seen["names"] == ["datetime__today", "files__read"]  # absent → all attached
        await client.post("/api/chat", json={**body, "enabled_tools": ["datetime__today"]})
        assert seen["names"] == ["datetime__today"]  # narrowed to the chat's servers
        await client.post("/api/chat", json={**body, "enabled_tools": []})
        assert seen["names"] == []  # explicitly nothing
    finally:
        app.dependency_overrides.clear()


async def test_api_chat_sampling_parameters_override_recommendations(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # top_k / min_p / repeat_penalty are settable per request like temperature/top_p, and fall back
    # to the model's recommendation (here stabbur's defaults — /models/x ships no generation_config)
    # when omitted, so a UI control that isn't touched changes nothing.
    class FakeManager:
        current = type("M", (), {"load_target": Path("/models/x")})()
        base_url = "http://runtime"

    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    seen: dict[str, Any] = {}

    async def fake_run(base: str, messages: list[dict[str, Any]], *a: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return ""

    monkeypatch.setattr(agent, "run", fake_run)
    body: dict[str, Any] = {"messages": [{"role": "user", "content": "hi"}]}
    try:
        await client.post("/api/chat", json=body)
        assert (seen["temperature"], seen["top_p"]) == (sampling.DEFAULT_TEMPERATURE, sampling.DEFAULT_TOP_P)
        assert (seen["top_k"], seen["min_p"], seen["repeat_penalty"]) == (
            sampling.DEFAULT_TOP_K,
            sampling.DEFAULT_MIN_P,
            sampling.DEFAULT_REPEAT_PENALTY,
        )
        await client.post("/api/chat", json={**body, "top_k": 5, "min_p": 0.2, "repeat_penalty": 1.0})
        assert (seen["top_k"], seen["min_p"], seen["repeat_penalty"]) == (5, 0.2, 1.0)
    finally:
        app.dependency_overrides.clear()


async def test_status_reports_stabbur_sampling_defaults(client: AsyncClient) -> None:
    # The settings UI labels an untouched slider with the value actually in force, so /api/status
    # carries stabbur's own defaults rather than the frontend keeping a copy that can drift.
    body = (await client.get("/api/status")).json()
    assert body["default_sampling"] == sampling.defaults().model_dump()


async def test_api_chat_system_prompt_precedence(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # system_prompt precedence: explicit "" → no system message (project default
    # skipped); explicit string → that prompt; field absent → project default.
    class FakeManager:
        current = type("M", (), {"load_target": Path("/models/x")})()
        base_url = "http://runtime"

    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    app.state.toolset = None
    app.state.system_prompt = "PROJECT DEFAULT"
    seen: dict[str, Any] = {}

    async def fake_run(base: str, messages: list[dict[str, Any]], *a: Any, **_: Any) -> str:
        seen["messages"] = messages
        return ""

    monkeypatch.setattr(agent, "run", fake_run)
    try:
        # Explicit empty → no system message at all.
        await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "system_prompt": ""})
        assert all(m["role"] != "system" for m in seen["messages"])

        # Explicit non-empty → that exact prompt.
        await client.post(
            "/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "system_prompt": "be a cat"}
        )
        assert seen["messages"][0] == {"role": "system", "content": "be a cat"}

        # Field absent → falls back to the project default.
        await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
        assert seen["messages"][0] == {"role": "system", "content": "PROJECT DEFAULT"}
    finally:
        app.dependency_overrides.clear()


async def test_locked_unresolvable_model_fails_startup() -> None:
    # A locked --model that names no library model must fail startup loudly, not
    # silently start with nothing loaded (and then reject /api/load for being locked).
    app = create_app(Settings(serve_model="definitely-not-a-real-model"))
    with pytest.raises(RuntimeError, match="did not resolve"):
        async with app.router.lifespan_context(app):
            pass


async def test_status_locked_reads_app_settings_not_global(monkeypatch: pytest.MonkeyPatch) -> None:
    # Global cache claims locked; this app was configured unlocked. The status
    # endpoint must reflect the app's own settings, not the process-wide cache.
    monkeypatch.setattr("stabbur.config.get_settings", lambda: Settings(serve_model="ghost"))
    app = create_app(Settings(serve_model=None))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as inner:
        body = (await inner.get("/api/status")).json()
    assert body["locked"] is False


async def test_audio_speech_rejects_unsupported_model_upfront(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A6/VO-M3: a registry-unsupported voice model is rejected at the endpoint with a clear 422,
    # not attempted and failed as a slow opaque 502. Runs before any backend dispatch, so it
    # needs no mlx-audio installed. (No built-in entry is unsupported right now, so inject one.)
    from stabbur.voice import registry as voice_registry

    fake = voice_registry.VoiceModel(
        id="fake-tts",
        display_name="Fake-TTS",
        repo="acme/Fake-TTS",
        kind=voice_registry.VoiceKind.tts,
        backend=voice_registry.Backend.mlx_audio,
        voice_mode=voice_registry.VoiceMode.preset,
        supported=False,
    )
    monkeypatch.setattr("stabbur.routers.serving.voice.voice_registry.get", lambda _id: fake)
    r = await client.post("/v1/audio/speech", json={"model": "fake-tts", "input": "hello"})
    assert r.status_code == 422
    assert "supported" in r.json()["detail"].lower()


async def test_audio_speech_unknown_model_404s(client: AsyncClient) -> None:
    # An unknown model must 404 — not silently synthesize with the Kokoro fallback voice,
    # which would return wrong-voice audio with a 200 for a caller's explicit model choice.
    r = await client.post("/v1/audio/speech", json={"model": "mlx-community/NotARealTTS", "input": "hello"})
    assert r.status_code == 404
    assert "unknown" in r.json()["detail"].lower()


async def test_api_assistant_404_when_none(client: AsyncClient) -> None:
    # No project [assistant] block → the endpoint 404s (the panel then hides the target chip).
    r = await client.get("/api/assistant")
    assert r.status_code == 404


async def test_api_assistant_echoes_statics_and_extra_keys_not_verify(app: FastAPI, client: AsyncClient) -> None:
    from stabbur.project import AssistantInfo

    # Lifespan doesn't run under ASGITransport; set app.state directly (like the other tests).
    app.state.assistant = AssistantInfo.model_validate(
        {
            "name": "play42",
            "base_url": "https://demo/x",
            "auth": "basic",
            "readonly": True,
            "source": "d2w profile play42",
            "region": "eu",  # extra key must ride along
            "verify": {"tool": "dhis2__dhis2_cli", "args": {"args": ["profile", "verify", "play42"]}},
        }
    )
    body = (await client.get("/api/assistant")).json()
    assert body["name"] == "play42" and body["base_url"] == "https://demo/x" and body["readonly"] is True
    assert body["source"] == "d2w profile play42" and body["region"] == "eu"  # extras echoed
    assert "verify" not in body  # the verify spec is an execution detail, never echoed
    assert body["can_verify"] is False and body["verified"] is None  # no toolset attached


class _FakeToolset:
    """A minimal stand-in for MCPToolset: a names list + a scripted call_structured."""

    def __init__(self, names: list[str], result: Any = None, exc: Exception | None = None) -> None:
        self.names = names
        self._result = result
        self._exc = exc
        self.calls = 0

    async def call_structured(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> Any:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        return self._result


def _dhis2_assistant() -> Any:
    from stabbur.project import AssistantInfo

    return AssistantInfo.model_validate(
        {
            "name": "play42",
            "base_url": "https://demo/x",
            "verify": {"tool": "dhis2__dhis2_cli", "args": {"args": ["profile", "verify", "play42"]}, "timeout": 5.0},
        }
    )


async def test_api_assistant_can_verify_reflects_tool_presence(app: FastAPI, client: AsyncClient) -> None:
    app.state.assistant = _dhis2_assistant()
    # Toolset attached, but without the verify tool → can_verify stays False.
    app.state.toolset = _FakeToolset(names=["other__thing"])
    assert (await client.get("/api/assistant")).json()["can_verify"] is False
    # Toolset attached with the verify tool → can_verify True.
    app.state.toolset = _FakeToolset(names=["dhis2__dhis2_cli"])
    assert (await client.get("/api/assistant")).json()["can_verify"] is True


async def test_api_assistant_verify_happy_path(app: FastAPI, client: AsyncClient) -> None:
    app.state.assistant = _dhis2_assistant()
    app.state.toolset = _FakeToolset(names=["dhis2__dhis2_cli"], result={"ok": True, "server": "play42"})
    body = (await client.get("/api/assistant", params={"verify": 1})).json()
    assert body["can_verify"] is True
    assert body["verified"]["ok"] is True
    assert body["verified"]["data"] == {"ok": True, "server": "play42"}
    assert isinstance(body["verified"]["checked_at"], (int, float))


async def test_api_assistant_verify_unknown_tool_is_ok_false_200(app: FastAPI, client: AsyncClient) -> None:
    # call_structured raising (unknown tool / timeout) is a data state, not an API error: 200 + ok=False.
    app.state.assistant = _dhis2_assistant()
    app.state.toolset = _FakeToolset(names=["dhis2__dhis2_cli"], exc=KeyError("dhis2__dhis2_cli"))
    r = await client.get("/api/assistant", params={"verify": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["verified"]["ok"] is False and body["verified"]["error"]


async def test_api_assistant_verify_no_toolset_is_ok_false_200(app: FastAPI, client: AsyncClient) -> None:
    # ?verify=1 with a spec but no toolset → ok=False (nothing to run), still HTTP 200.
    app.state.assistant = _dhis2_assistant()
    app.state.toolset = None
    r = await client.get("/api/assistant", params={"verify": 1})
    assert r.status_code == 200
    assert r.json()["verified"]["ok"] is False


async def test_api_assistant_verify_uses_ttl_cache(app: FastAPI, client: AsyncClient) -> None:
    # A fresh cached outcome (< 60s) is returned without re-running the tool.
    import time

    from stabbur.routers.serving.assistant import AssistantVerified

    app.state.assistant = _dhis2_assistant()
    fake = _FakeToolset(names=["dhis2__dhis2_cli"], result={"live": True})
    app.state.toolset = fake
    cached = AssistantVerified(ok=True, data={"cached": True}, checked_at=time.time())
    # Compat now keys the shared per-id cache by the primary's id ('play42' for _dhis2_assistant).
    app.state.assistant_verified_by_id = {"play42": (cached.checked_at, cached)}
    body = (await client.get("/api/assistant", params={"verify": 1})).json()
    assert body["verified"]["data"] == {"cached": True}  # served from cache
    assert fake.calls == 0  # the tool was not re-run


def test_api_assistant_response_mirrors_info_fields() -> None:
    # AssistantResponse hand-mirrors AssistantInfo's fields (minus verify); a field added to
    # AssistantInfo must not silently vanish from the echo contract.
    from stabbur.project import AssistantInfo
    from stabbur.routers.serving.assistant import AssistantResponse

    info_fields = set(AssistantInfo.model_fields) - {"verify"}
    assert info_fields <= set(AssistantResponse.model_fields)


async def test_api_assistant_reserved_extra_keys_do_not_crash(app: FastAPI, client: AsyncClient) -> None:
    # A project extra key named after a response field (can_verify / verified) must be overridden
    # by stabbur's computed values, not raise TypeError (-> 500) or pollute the response.
    from stabbur.project import AssistantInfo

    app.state.assistant = AssistantInfo.model_validate({"name": "x", "can_verify": True, "verified": "spoofed"})
    r = await client.get("/api/assistant")
    assert r.status_code == 200
    body = r.json()
    assert body["can_verify"] is False  # computed (no toolset), not the echoed extra
    assert body["verified"] is None  # computed, not the echoed extra


async def test_api_assistant_concurrent_verify_probes_once(app: FastAPI, client: AsyncClient) -> None:
    # Two callers racing an empty cache share one probe (single-flight lock), so panel polling
    # cannot double-hit a rate-limited target instance.
    import asyncio

    class _SlowToolset(_FakeToolset):
        async def call_structured(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> Any:
            self.calls += 1
            await asyncio.sleep(0.05)
            return {"live": True}

    app.state.assistant = _dhis2_assistant()
    fake = _SlowToolset(names=["dhis2__dhis2_cli"])
    app.state.toolset = fake
    a, b = await asyncio.gather(
        client.get("/api/assistant", params={"verify": 1}),
        client.get("/api/assistant", params={"verify": 1}),
    )
    assert a.json()["verified"]["data"] == {"live": True}
    assert b.json()["verified"]["data"] == {"live": True}
    assert fake.calls == 1  # one probe served both


async def test_api_assistant_echoes_probe_and_sanitized_bind(app: FastAPI, client: AsyncClient) -> None:
    # probe is echoed verbatim (it is FOR the client to run); bind is echoed sanitized — the
    # browser-side mint recipe plus only the mode NAMES (a mode's argv/secret_env are server-side).
    import json as _json

    from stabbur.project import AssistantInfo

    app.state.assistant = AssistantInfo.model_validate(
        {
            "name": "play42",
            "base_url": "https://demo/x",
            "probe": {
                "paths": ["/api/me.json?fields=name", "/api/system/info.json"],
                "fields": {"name": ["0.name"]},
                "label": "Browsing as {name}",
            },
            "bind": {
                "mint_path": "/api/apiToken",
                "session_cookie": "JSESSIONID",
                "modes": {
                    "session": {"command": ["tool", "s", "{base_url}"], "secret_env": "COOKIE"},
                    "pat": {"command": ["tool", "p", "{base_url}"], "secret_env": "PAT"},
                },
            },
        }
    )
    body = (await client.get("/api/assistant")).json()
    assert body["probe"]["paths"][0] == "/api/me.json?fields=name"
    assert body["probe"]["label"] == "Browsing as {name}"
    assert body["can_bind"] is True
    assert body["bind"]["mint_path"] == "/api/apiToken"
    assert body["bind"]["modes"] == ["pat", "session"]  # names only, sorted
    dumped = _json.dumps(body["bind"])
    assert "secret_env" not in dumped and "command" not in dumped  # execution details never echoed

    # A bind block with no runnable mode → can_bind False, but the recipe still echoes.
    app.state.assistant = AssistantInfo.model_validate({"name": "y", "bind": {"mint_path": "/api/x"}})
    body2 = (await client.get("/api/assistant")).json()
    assert body2["can_bind"] is False and body2["bind"]["modes"] == []


async def test_api_assistant_bind_404_without_bind(app: FastAPI, client: AsyncClient) -> None:
    from stabbur.project import AssistantInfo

    # No assistant metadata at all → 404.
    assert (await client.post("/api/assistant/bind", json={"mode": "pat", "secret": "x"})).status_code == 404
    # Assistant present but no [assistant.bind] → 404.
    app.state.assistant = AssistantInfo.model_validate({"name": "x"})
    assert (await client.post("/api/assistant/bind", json={"mode": "pat", "secret": "x"})).status_code == 404


async def test_api_assistant_bind_bad_requests(app: FastAPI, client: AsyncClient) -> None:
    import sys

    from stabbur.project import AssistantInfo

    app.state.assistant = AssistantInfo.model_validate(
        {
            "name": "x",
            "base_url": "https://d/x",
            "bind": {"modes": {"pat": {"command": [sys.executable, "-c", "import sys"], "secret_env": "X"}}},
        }
    )
    assert (await client.post("/api/assistant/bind", json={"mode": "nope", "secret": "s"})).status_code == 400
    assert (await client.post("/api/assistant/bind", json={"mode": "pat", "secret": ""})).status_code == 400
    big = "a" * (16384 + 1)
    assert (await client.post("/api/assistant/bind", json={"mode": "pat", "secret": big})).status_code == 400


async def test_api_assistant_bind_runs_mode_and_redacts_secret(
    app: FastAPI, client: AsyncClient, tmp_path: Path
) -> None:
    # A bind mode runs its argv with the secret in secret_env (never on the argv), templates
    # {base_url} from AssistantInfo, redacts the secret from captured output, and invalidates verify.
    import sys
    import time

    from stabbur.project import AssistantInfo

    proof = tmp_path / "proof.txt"
    code = (
        "import os,sys\n"
        "open(sys.argv[1],'w').write(os.environ.get('MY_SECRET','')+'|'+sys.argv[2])\n"
        "print('leaked',os.environ.get('MY_SECRET',''))\n"
    )
    secret = "SUPERSECRET123"
    info = AssistantInfo.model_validate(
        {
            "name": "play42",
            "base_url": "https://demo.example/x",
            "bind": {
                "modes": {
                    "pat": {
                        "command": [sys.executable, "-c", code, str(proof), "{base_url}"],
                        "secret_env": "MY_SECRET",
                    }
                }
            },
        }
    )
    app.state.assistant = info
    # Compat bind invalidates the shared per-id cache keyed by the primary's id ('play42').
    app.state.assistant_verified_by_id = {"play42": (time.time(), object())}  # stale outcome the bind must clear
    r = await client.post("/api/assistant/bind", json={"mode": "pat", "secret": secret})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["exit_code"] == 0
    assert proof.read_text() == f"{secret}|https://demo.example/x"  # secret via env, base_url templated
    assert info.bind is not None
    assert all(secret not in arg for arg in info.bind.modes["pat"].command)  # never on argv
    assert "***" in body["stdout"] and secret not in body["stdout"]  # redacted from output
    assert "play42" not in app.state.assistant_verified_by_id  # verify cache invalidated


async def test_api_assistant_unbind_runs_and_requires_command(
    app: FastAPI, client: AsyncClient, tmp_path: Path
) -> None:
    import sys

    from stabbur.project import AssistantInfo

    marker = tmp_path / "unbound.txt"
    code = "import sys\nopen(sys.argv[1],'w').write('unbound')\n"
    app.state.assistant = AssistantInfo.model_validate(
        {
            "name": "x",
            "bind": {
                "modes": {
                    "pat": {
                        "command": [sys.executable, "-c", "import sys"],
                        "secret_env": "X",
                        "unbind_command": [sys.executable, "-c", code, str(marker)],
                    },
                    "session": {"command": [sys.executable, "-c", "import sys"], "secret_env": "Y"},  # no unbind
                }
            },
        }
    )
    r = await client.post("/api/assistant/unbind", json={"mode": "pat"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert marker.read_text() == "unbound"
    # A mode with no unbind_command → 400.
    assert (await client.post("/api/assistant/unbind", json={"mode": "session"})).status_code == 400


async def test_api_assistant_bind_cross_site_blocked(client: AsyncClient) -> None:
    # Mutating POSTs under /api are covered by the cross-site guard before the handler runs, so a
    # drive-by page can't run a bind mode / install a credential on the local server.
    r = await client.post(
        "/api/assistant/bind", json={"mode": "pat", "secret": "s"}, headers={"sec-fetch-site": "cross-site"}
    )
    assert r.status_code == 403
    r = await client.post("/api/assistant/unbind", json={"mode": "pat"}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


async def test_api_assistant_bind_missing_command_is_127_not_500(app: FastAPI, client: AsyncClient) -> None:
    # A mode whose argv[0] doesn't exist is a data state, not a crash: BindResult ok=False with
    # exit_code 127 (a shell's "command not found"), never an HTTP 500.
    from stabbur.project import AssistantInfo

    app.state.assistant = AssistantInfo.model_validate(
        {"name": "x", "bind": {"modes": {"pat": {"command": ["stabbur-definitely-missing-xyz"], "secret_env": "X"}}}}
    )
    r = await client.post("/api/assistant/bind", json={"mode": "pat", "secret": "s"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False and body["exit_code"] == 127
    assert "command not found" in body["stderr"]


async def test_api_assistant_bind_caps_chatty_output(app: FastAPI, client: AsyncClient) -> None:
    # A mode that floods stdout is capped (first 16384 bytes) with a truncation marker, so it can't
    # blow up RAM / the JSON response / the redaction scan.
    import sys

    from stabbur.project import AssistantInfo

    code = "import sys\nsys.stdout.write('A' * 200000)\n"
    app.state.assistant = AssistantInfo.model_validate(
        {"name": "x", "bind": {"modes": {"pat": {"command": [sys.executable, "-c", code], "secret_env": "X"}}}}
    )
    r = await client.post("/api/assistant/bind", json={"mode": "pat", "secret": "s"})
    assert r.status_code == 200, r.text
    stdout = r.json()["stdout"]
    assert stdout.endswith("... [truncated]")
    assert len(stdout) <= 16384 + len("... [truncated]")


async def test_api_assistant_bind_no_double_substitution(app: FastAPI, client: AsyncClient, tmp_path: Path) -> None:
    # Single-pass templating: a value that itself contains a literal "{name}" is never re-substituted.
    # Here base_url embeds "{name}", so the rendered argv keeps it verbatim (not replaced by the name).
    import sys

    from stabbur.project import AssistantInfo

    proof = tmp_path / "argv.txt"
    code = "import sys\nopen(sys.argv[1], 'w').write(sys.argv[2])\n"
    app.state.assistant = AssistantInfo.model_validate(
        {
            "name": "REALNAME",
            "base_url": "https://demo/{name}",
            "bind": {
                "modes": {"pat": {"command": [sys.executable, "-c", code, str(proof), "{base_url}"], "secret_env": "X"}}
            },
        }
    )
    r = await client.post("/api/assistant/bind", json={"mode": "pat", "secret": "s"})
    assert r.status_code == 200, r.text
    assert proof.read_text() == "https://demo/{name}"  # literal {name} preserved, not -> REALNAME


async def test_api_assistant_bind_timeout_kills_process_group(
    app: FastAPI, client: AsyncClient, tmp_path: Path
) -> None:
    # On timeout the whole process GROUP is killed, so a grandchild the mode forked (which carries the
    # secret in its env) does not survive as an orphan — a plain proc.kill() would leave it behind.
    import os
    import sys
    import time

    from stabbur.project import AssistantInfo

    pidfile = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time\n"
        "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "open(sys.argv[1], 'w').write(str(gc.pid))\n"
        "time.sleep(60)\n"
    )
    app.state.assistant = AssistantInfo.model_validate(
        {
            "name": "x",
            "bind": {
                "modes": {
                    "pat": {"command": [sys.executable, "-c", code, str(pidfile)], "secret_env": "X", "timeout": 1.0}
                }
            },
        }
    )
    r = await client.post("/api/assistant/bind", json={"mode": "pat", "secret": "s"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "timed out" in body["stderr"]
    gc_pid = int(pidfile.read_text())  # the mode did fork a grandchild
    deadline = time.time() + 3.0
    while time.time() < deadline:
        try:
            os.kill(gc_pid, 0)
        except ProcessLookupError:
            break  # reaped — the group kill got the grandchild too
        time.sleep(0.05)
    else:
        pytest.fail("grandchild survived the timeout kill (orphaned by a non-group kill)")


async def test_api_assistant_echoes_unbind_notes(app: FastAPI, client: AsyncClient) -> None:
    # A mode's user-facing unbind_note is echoed keyed by mode (guidance, not an execution detail);
    # command/secret_env stay excluded, and a mode's extra="allow" fields never ride into the echo.
    import json as _json

    from stabbur.project import AssistantInfo

    app.state.assistant = AssistantInfo.model_validate(
        {
            "name": "x",
            "bind": {
                "modes": {
                    "pat": {
                        "command": ["tool", "{base_url}"],
                        "secret_env": "PAT",
                        "unbind_note": "restore the demo profile",
                        "extra_flag": "should-not-echo",
                    },
                    "session": {"command": ["tool"], "secret_env": "COOKIE"},  # no note
                }
            },
        }
    )
    body = (await client.get("/api/assistant")).json()
    assert body["bind"]["unbind_notes"] == {"pat": "restore the demo profile"}  # only the mode with a note
    dumped = _json.dumps(body["bind"])
    assert "secret_env" not in dumped and "command" not in dumped and "should-not-echo" not in dumped


def test_truncate_detail_preserves_large_json_and_passes_non_json() -> None:
    # A large JSON tool detail stays valid JSON under the 2000-char cap (string values capped, JSON
    # structure intact) so the UI's collapsible chips still parse it; non-JSON is a plain hard cut.
    import json as _json

    from stabbur.routers.serving.chat import _truncate_detail

    detail = _json.dumps({"result": "x" * 5000, "n": 7})
    assert len(detail) > 2000
    out = _truncate_detail(detail)
    assert len(out) <= 2000
    parsed = _json.loads(out)  # still valid JSON
    assert parsed["n"] == 7 and parsed["result"].endswith("...")

    non_json = "not json " * 500
    assert _truncate_detail(non_json) == non_json[:2000]  # unchanged behavior for non-JSON
    assert _truncate_detail("hello") == "hello"  # small details pass straight through


async def test_audio_speech_openai_alias_maps_to_default_voice(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stock OpenAI clients send OpenAI's own model ids ("tts-1"); those must route to the
    # default chat voice (Kokoro backend — 503 here since it's stubbed unavailable), not 404.
    monkeypatch.setattr("stabbur.routers.serving.voice.kokoro.available", lambda: False)
    r = await client.post("/v1/audio/speech", json={"model": "tts-1", "input": "hello"})
    assert r.status_code == 503


# --- upstream mode (serve --upstream) ---------------------------------------


@pytest.fixture
def upstream_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """App fronting a fake remote /v1 (no network: models() and ready() are stubbed)."""
    from stabbur.server import UpstreamManager, UpstreamModel

    rows = [
        UpstreamModel(name="gemma-4-12b-qat", loaded=False, vision=True, audio=True),
        UpstreamModel(name="qwen3-coder", loaded=True),
    ]
    monkeypatch.setattr(UpstreamManager, "models", lambda self: list(rows))

    async def _ready(self: UpstreamManager) -> bool:
        return True

    monkeypatch.setattr(UpstreamManager, "ready", _ready)
    return create_app(Settings(upstream="http://up:1234"))


@pytest.fixture
async def upstream_client(upstream_app: FastAPI):
    transport = ASGITransport(app=upstream_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def test_upstream_library_lists_remote_models(upstream_client: AsyncClient) -> None:
    # The picker rows are the remote's /v1/models: format "remote", modality flags from the
    # listing, and a "loaded" tag marking what the remote has resident.
    body = (await upstream_client.get("/api/library")).json()
    assert [m["name"] for m in body] == ["gemma-4-12b-qat", "qwen3-coder"]
    assert all(m["model_format"] == "remote" for m in body)
    assert body[0]["vision"] and body[0]["audio"] and body[0]["tags"] == []
    assert body[1]["tags"] == ["loaded"]


async def test_upstream_status_names_the_remote(upstream_client: AsyncClient) -> None:
    # Nothing else in /api/status distinguishes a remote from a local runtime (a remote id looks
    # like a model name), so the base URL is reported for a UI that wants to say where a reply
    # comes from. Normalised the way UpstreamManager takes it — no trailing /v1.
    body = (await upstream_client.get("/api/status")).json()
    assert body["upstream"] == "http://up:1234"


async def test_upstream_load_selects_remote_id(upstream_app: FastAPI, upstream_client: AsyncClient) -> None:
    # "Loading" selects a remote id (matched case-insensitively); the remote itself loads it
    # on the next request. An unknown name 404s with what the remote actually serves.
    r = await upstream_client.post("/api/load/QWEN3-CODER")
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "qwen3-coder"
    assert r.json()["state"] == "ready"
    status = (await upstream_client.get("/api/status")).json()
    assert status["model"] == "qwen3-coder"

    r = await upstream_client.post("/api/load/not-served")
    assert r.status_code == 404
    assert "available" in r.json()["detail"]
    # A failed switch keeps the selection.
    assert (await upstream_client.get("/api/status")).json()["model"] == "qwen3-coder"


async def test_upstream_unload_clears_selection_only(upstream_client: AsyncClient) -> None:
    await upstream_client.post("/api/load/gemma-4-12b-qat")
    r = await upstream_client.post("/api/unload")
    assert r.status_code == 200
    assert r.json()["model"] is None  # selection cleared; the remote itself is untouched


def test_reasoning_fields_llama_dialect() -> None:
    # The reasoning knob speaks llama-server's dialect (what its own webui sends):
    # enable_thinking toggles the chat template, thinking_budget_tokens caps the effort.
    assert agent.reasoning_fields(None) == {}
    off = agent.reasoning_fields("off")
    assert off["chat_template_kwargs"] == {"enable_thinking": False} and off["reasoning_control"] is True
    assert "thinking_budget_tokens" not in off
    assert agent.reasoning_fields("low")["thinking_budget_tokens"] == 512
    assert agent.reasoning_fields("medium")["thinking_budget_tokens"] == 2048
    high = agent.reasoning_fields("high")
    assert high["thinking_budget_tokens"] == 8192 and high["chat_template_kwargs"] == {"enable_thinking": True}
    assert "thinking_budget_tokens" not in agent.reasoning_fields("max")  # max = unbounded


async def test_api_chat_forwards_reasoning(app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeManager:
        current = type("M", (), {"load_target": Path("/models/x")})()
        base_url = "http://runtime"

    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    seen: list[str | None] = []

    async def fake_run(base: str, messages: list[dict[str, Any]], toolset: Any, *a: Any, **kw: Any) -> str:
        seen.append(kw.get("reasoning"))
        return "ok"

    monkeypatch.setattr(agent, "run", fake_run)
    try:
        await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
        assert seen[-1] is None  # absent -> model default
        await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "reasoning": "low"})
        assert seen[-1] == "low"
        r = await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "reasoning": "huge"})
        assert r.status_code == 422  # not a valid level
    finally:
        app.dependency_overrides.clear()


async def test_spa_index_is_revalidated_but_hashed_assets_are_immutable(tmp_path: Path) -> None:
    """A built SPA must not be served without a cache policy.

    FastAPI's frontend() sends only ETag/Last-Modified, and a response with no Cache-Control
    is heuristically cached — so index.html would be held without revalidation and keep
    loading the previous build's content-hashed bundle after an upgrade.
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>stabbur</title>")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")

    app = create_app(Settings(serve_model=None, serve_ui=True, frontend_dir=dist))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        index = await client.get("/")
        assert index.status_code == 200
        assert index.headers["cache-control"] == "no-cache"

        asset = await client.get("/assets/index-abc123.js")
        assert asset.status_code == 200
        assert "immutable" in asset.headers["cache-control"]

        # API responses keep their own semantics — the SPA policy must not leak onto them.
        api = await client.get("/api/status")
        assert "cache-control" not in api.headers


async def test_spa_client_routes_serve_the_shell_without_shadowing_the_api(tmp_path: Path) -> None:
    """/chat and /settings are routes inside the bundle, not files — they must load the app.

    FastAPI's frontend(fallback="index.html") does not cover them (it only applies within the
    static mount, which never matches a path that isn't a file), so a deep link or a refresh
    answered 404. The fallback belongs where a request lands only after everything else has
    missed, which is what this pins: API routes and real files still win, and an API 404 stays
    JSON rather than becoming HTML a fetch() would choke on.
    """
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>stabbur</title>")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")

    app = create_app(Settings(serve_model=None, serve_ui=True, frontend_dir=dist))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/chat", "/settings", "/some/deep/link"):
            page = await client.get(path)
            assert page.status_code == 200, path
            assert page.headers["content-type"].startswith("text/html"), path
            assert "<title>stabbur</title>" in page.text

        asset = await client.get("/assets/index-abc123.js")  # a real file still wins
        assert asset.status_code == 200
        assert asset.text == "console.log(1)"

        assert (await client.get("/api/status")).status_code == 200  # an API route still wins
        missing_api = await client.get("/api/no-such-endpoint")  # and a missing one stays an API 404
        assert missing_api.status_code == 404
        assert missing_api.headers["content-type"].startswith("application/json")
        missing_asset = await client.get("/assets/gone.js")  # a stale bundle reference stays a 404
        assert missing_asset.status_code == 404


async def test_unconfigured_library_answers_with_its_hint_not_a_500(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A first run with no STABBUR_LIBRARY_ROOT must not look like a crash.

    The exception carries a ready-to-print hint naming the variable to set; escaping as a bare
    500 stranded the SPA with broken panels and left that hint in the server log.
    """

    def _unconfigured(*_a: object, **_k: object) -> list[LibraryModel]:
        raise library_ops.LibraryNotConfigured

    monkeypatch.setattr(library_ops, "scan", _unconfigured)
    response = await client.get("/api/library")
    assert response.status_code == 503
    assert "STABBUR_LIBRARY_ROOT" in response.json()["detail"]


async def test_api_chat_passes_response_format_to_the_runtime(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Structured output reaches the runtime verbatim. Only `json_schema` is actually enforced by
    # llama-server; `json_object` is silently ignored there, which is why the docs say so rather
    # than stabbur pretending to support it.
    class FakeManager:
        current = type("M", (), {"load_target": Path("/models/x")})()
        base_url = "http://runtime"

    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    seen: list[dict[str, Any] | None] = []

    async def fake_run(*a: Any, response_format: dict[str, Any] | None = None, **_: Any) -> str:
        seen.append(response_format)
        return "ok"

    monkeypatch.setattr(agent, "run", fake_run)
    schema = {"type": "json_schema", "json_schema": {"name": "s", "schema": {"type": "object"}}}
    try:
        await client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "hi"}], "use_tools": False, "response_format": schema},
        )
        assert seen[-1] == schema
        await client.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "use_tools": False})
        assert seen[-1] is None  # absent unless asked for
    finally:
        app.dependency_overrides.clear()


async def test_api_chat_refuses_response_format_together_with_tools(app: FastAPI, client: AsyncClient) -> None:
    # llama-server compiles one grammar per request and rejects the pair with
    # 400 "failed to parse grammar" — a message naming neither feature. stabbur refuses first,
    # and the message has to name the fix, or the caller is no better off than upstream.
    class FakeManager:
        current = type("M", (), {"load_target": Path("/models/x")})()
        base_url = "http://runtime"

    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    try:
        r = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "response_format": {"type": "json_schema", "json_schema": {"name": "s", "schema": {}}},
            },
        )
        assert r.status_code == 400
        assert "use_tools" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


async def test_v1_models_lists_the_library_when_nothing_is_loaded(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `GET /v1/models` is how an OpenAI client discovers what it may ask for. Refusing it until
    # something is loaded is a chicken-and-egg — the client cannot choose before it can list —
    # and the docs tell people to point any OpenAI client at this base URL.
    class FakeManager:
        current = None
        base_url = "http://runtime"
        is_upstream = False  # a local backend is what makes discovery fall back to the library

    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    model = LibraryModel(
        name="pub/Some-GGUF",
        model_format=ModelFormat.gguf,
        path=Path("/lib/gguf/pub/Some-GGUF"),
        load_target=Path("/lib/gguf/pub/Some-GGUF/w.gguf"),
    )
    monkeypatch.setattr(proxy.library_ops, "scan", lambda *a, **k: [model])
    try:
        r = await client.get("/v1/models")
        assert r.status_code == 200
        body = r.json()
        assert body["object"] == "list"
        assert [m["id"] for m in body["data"]] == ["pub/Some-GGUF"]
    finally:
        app.dependency_overrides.clear()


async def test_v1_completions_still_refuse_when_nothing_is_loaded(app: FastAPI, client: AsyncClient) -> None:
    # Discovery is the only exemption: listing and using are different questions, and a
    # completion with no runtime behind it must still say so plainly.
    class FakeManager:
        current = None
        base_url = "http://runtime"

    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    try:
        r = await client.post("/v1/chat/completions", json={"messages": []})
        assert r.status_code == 409
        assert r.json()["detail"] == "No model loaded"
    finally:
        app.dependency_overrides.clear()


def test_openapi_operation_ids_are_unique() -> None:
    # One `api_route(methods=["GET", "POST"])` gives both methods a single operation id, which is a
    # duplicate in the schema: a startup UserWarning, and a generated client silently missing one.
    schema = create_app(Settings(serve_model=None)).openapi()
    ids = [op.get("operationId") for path in schema["paths"].values() for op in path.values()]
    assert len(ids) == len(set(ids))


async def test_locked_library_lists_only_the_locked_model(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A locked server refuses to load anything else, so enumerating the rest of the library tells
    # a caller only what is on the machine. Locked mode answers with the one row it can serve.
    def _model(name: str) -> LibraryModel:
        return LibraryModel(
            name=name, model_format=ModelFormat.gguf, path=Path(f"/tmp/{name}"), load_target=Path(f"/tmp/{name}")
        )

    monkeypatch.setattr(library_ops, "scan", lambda: [_model("some-model"), _model("other-model")])
    assert len((await client.get("/api/library")).json()) == 2  # unlocked: the whole library
    app.dependency_overrides[serving.get_conf] = lambda: Settings(serve_model="some-model")
    try:
        body = (await client.get("/api/library")).json()
        assert [m["name"] for m in body] == ["some-model"]
    finally:
        app.dependency_overrides.clear()


# --- side-effectful reads (the cross-site guard's GET exemption) -------------------------------


async def test_cross_site_verify_get_is_blocked(client: AsyncClient) -> None:
    # GET /api/assistants/{id}?verify=1 spawns the target's MCP servers and runs its verify tool.
    # The guard exempts GETs as reads, so a drive-by <img src> from any page fired those side
    # effects. A GET that *runs* something is guarded exactly like a POST.
    headers = {"sec-fetch-site": "cross-site", "origin": "https://evil.example"}
    blocked = await client.get("/api/assistants/anything", params={"verify": 1}, headers=headers)
    assert blocked.status_code == 403
    assert (await client.get("/api/assistant", params={"verify": 1}, headers=headers)).status_code == 403


async def test_cross_site_plain_assistant_read_is_not_blocked(client: AsyncClient) -> None:
    # Only the side-effectful shape is guarded: the plain metadata read stays a read (404 here
    # because no project is loaded — the point is that it isn't a 403).
    headers = {"sec-fetch-site": "cross-site", "origin": "https://evil.example"}
    assert (await client.get("/api/assistants/anything", headers=headers)).status_code == 404
    assert (await client.get("/api/assistants", headers=headers)).status_code == 200
    assert (await client.get("/api/assistant", params={"verify": 0}, headers=headers)).status_code == 404


async def test_same_origin_verify_get_still_works(app: FastAPI, client: AsyncClient) -> None:
    # The guard is about cross-site callers; the served SPA and non-browser clients are unaffected.
    app.state.assistant = _dhis2_assistant()
    app.state.toolset = _FakeToolset(names=["dhis2__dhis2_cli"], result={"ok": True})
    r = await client.get("/api/assistant", params={"verify": 1}, headers={"sec-fetch-site": "same-origin"})
    assert r.status_code == 200 and r.json()["verified"]["ok"] is True


async def test_post_verify_routes_run_the_probe(app: FastAPI, client: AsyncClient) -> None:
    # The POST form is the same probe under a method that admits it runs something.
    app.state.assistant = _dhis2_assistant()
    fake = _FakeToolset(names=["dhis2__dhis2_cli"], result={"ok": True, "server": "play42"})
    app.state.toolset = fake
    body = (await client.post("/api/assistant/verify")).json()
    assert body["verified"]["data"] == {"ok": True, "server": "play42"}
    assert fake.calls == 1
    # The per-target route shares the one per-id cache, so it doesn't re-probe within the TTL.
    app.state.registry = _one_target_registry(app.state.assistant)
    assert (await client.post("/api/assistants/play42/verify")).json()["verified"]["ok"] is True
    assert fake.calls == 1
    assert (await client.post("/api/assistants/nope/verify")).status_code == 404


def _one_target_registry(info: Any) -> Any:
    """A registry holding just ``info`` (its id is the slug of its name), for the per-target routes."""
    from stabbur.targets import AssistantRegistry

    return AssistantRegistry(targets=[info])


def test_capture_redacts_before_truncating() -> None:
    # A secret straddling the 16KB cut used to keep its leading half: the output was capped first,
    # so the redaction pass had only a fragment to match and left it in the response.
    from stabbur.routers.serving.assistant import _MAX_OUTPUT, _capture

    secret = "s3cret-token-value"
    raw = b"x" * (_MAX_OUTPUT - 5) + secret.encode() + b" tail"
    out = _capture(raw, (secret, None))
    assert secret not in out
    assert secret[:5] not in out  # not even the prefix that straddled the cut
    assert "***" in out and out.endswith("... [truncated]")


async def test_bind_invalidates_a_verify_that_is_still_in_flight(app: FastAPI, client: AsyncClient) -> None:
    # bind's invalidate() and the verify cache write used to take DIFFERENT locks, so a probe that
    # started before the bind could write its (pre-bind) outcome afterwards and pin it for the full
    # 60s TTL — the panel then reported "not bound" for a minute after a successful bind.
    import asyncio
    import sys

    from stabbur.project import AssistantInfo

    entered, release = asyncio.Event(), asyncio.Event()

    class _SlowToolset:
        names = ["dhis2__dhis2_cli"]

        async def call_structured(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> Any:
            entered.set()
            await release.wait()
            return {"bound": False}  # the STALE, pre-bind answer

    info = AssistantInfo.model_validate(
        {
            "name": "play42",
            "base_url": "https://demo/x",
            "verify": {"tool": "dhis2__dhis2_cli", "args": {}, "timeout": 5.0},
            "bind": {"modes": {"pat": {"command": [sys.executable, "-c", ""], "secret_env": "PAT"}}},
        }
    )
    app.state.assistant = info
    app.state.toolset = _SlowToolset()

    verifying = asyncio.create_task(client.get("/api/assistant", params={"verify": 1}))
    await asyncio.wait_for(entered.wait(), timeout=5)  # the probe holds the target's verify lock
    binding = asyncio.create_task(client.post("/api/assistant/bind", json={"mode": "pat", "secret": "tok"}))
    await asyncio.sleep(0.05)  # let the bind run its command and reach the invalidate
    release.set()
    assert (await asyncio.wait_for(binding, timeout=10)).json()["ok"] is True
    assert (await asyncio.wait_for(verifying, timeout=10)).json()["verified"]["data"] == {"bound": False}
    # The stale outcome must not be cached: the next ?verify=1 re-probes against the new credential.
    assert app.state.assistant_verified_by_id.get("play42") is None
