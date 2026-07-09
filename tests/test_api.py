"""Tests for the serving API (status + proxy guards), no real runtime needed."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from kodo import agent
from kodo import library as library_ops
from kodo.app import create_app
from kodo.config import Settings
from kodo.library import LibraryModel
from kodo.models import ModelFormat
from kodo.routers import serving


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


async def test_status_exposes_project_model(app: FastAPI, client: AsyncClient) -> None:
    # /api/status surfaces the project's bound model (kodo.toml [project].model),
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


async def test_concurrent_loads_are_serialized(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    # ServerManager has no internal lock, so two /api/load calls must not run
    # manager.load() at the same time (interleaving corrupts its process state).
    import asyncio
    import threading
    import time

    fake = LibraryModel(name="m", model_format=ModelFormat.gguf, path=Path("/x"), load_target=Path("/x/m.gguf"))
    monkeypatch.setattr("kodo.routers.serving.chat.library_ops.find", lambda name: [fake])
    monkeypatch.setattr("kodo.routers.serving.chat.runtime.runnable_error", lambda m: None)

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

    monkeypatch.setattr(app.state.manager, "load", slow_load)

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
    monkeypatch.setattr("kodo.routers.serving.chat.library_ops.find", lambda name: [fake])
    monkeypatch.setattr("kodo.routers.serving.chat.runtime.runnable_error", lambda m: None)
    monkeypatch.setattr(app.state.manager, "load", lambda *a, **k: None)
    monkeypatch.setattr(app.state.manager, "stop", lambda *a, **k: None)

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
    # a match but aren't runnable by kodo — the API must reject them cleanly (422),
    # not pass them to manager.load and surface a 500 / ValueError traceback.
    p = Path("/tmp/x")
    model = LibraryModel(name="pub/X", model_format=fmt, is_ollama=is_ollama, path=p, load_target=p / "w")
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [model])
    response = await client.post("/api/load/pub/X")
    assert response.status_code == 422, response.text


def test_create_app_honors_settings_runtime_port() -> None:
    # The factory must use the runtime_port from the settings it's given.
    assert create_app(Settings(runtime_port=8123)).state.manager._port == 8123


def test_create_app_autopicks_when_runtime_port_none() -> None:
    assert create_app(Settings(runtime_port=None)).state.manager._port > 0


def test_reload_worker_honors_runtime_port_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulates a --reload worker: fresh process, no in-memory override, reads
    # KODO_RUNTIME_PORT that serve() propagated into the environment.
    from kodo import config
    from kodo.config import get_settings

    monkeypatch.setattr(config, "_runtime_port_override", None)
    monkeypatch.setenv("KODO_RUNTIME_PORT", "8124")
    get_settings.cache_clear()
    try:
        assert create_app().state.manager._port == 8124
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
    # /api/doctor mirrors `kodo doctor`: a list of typed health checks.
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


async def test_api_unload_stops_the_runtime(app: FastAPI, client: AsyncClient) -> None:
    # /api/unload ejects the model by calling manager.stop(), returning stopped status.
    stopped = {"n": 0}

    class FakeManager:
        current = None
        n_ctx = None
        last_error = None

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

    from kodo.routers.serving import voice as voice_router

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
    from kodo.tools import MCPToolset

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
    monkeypatch.setattr("kodo.config.get_settings", lambda: Settings(serve_model="ghost"))
    app = create_app(Settings(serve_model=None))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as inner:
        body = (await inner.get("/api/status")).json()
    assert body["locked"] is False


async def test_audio_speech_rejects_unsupported_model_upfront(client: AsyncClient) -> None:
    # A6/VO-M3: a registry-unsupported voice model (Qwen3-TTS — mlx-audio can't load its speech
    # tokenizer) is rejected at the endpoint with a clear 422, not attempted and failed as a slow
    # opaque 502. Runs before any backend dispatch, so it needs no mlx-audio installed.
    r = await client.post("/v1/audio/speech", json={"model": "qwen3-tts", "input": "hello"})
    assert r.status_code == 422
    assert "supported" in r.json()["detail"].lower()


async def test_audio_speech_unknown_model_404s(client: AsyncClient) -> None:
    # An unknown model must 404 — not silently synthesize with the Kokoro fallback voice,
    # which would return wrong-voice audio with a 200 for a caller's explicit model choice.
    r = await client.post("/v1/audio/speech", json={"model": "mlx-community/NotARealTTS", "input": "hello"})
    assert r.status_code == 404
    assert "unknown" in r.json()["detail"].lower()


async def test_audio_speech_openai_alias_maps_to_default_voice(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stock OpenAI clients send OpenAI's own model ids ("tts-1"); those must route to the
    # default chat voice (Kokoro backend — 503 here since it's stubbed unavailable), not 404.
    monkeypatch.setattr("kodo.routers.serving.voice.kokoro.available", lambda: False)
    r = await client.post("/v1/audio/speech", json={"model": "tts-1", "input": "hello"})
    assert r.status_code == 503
