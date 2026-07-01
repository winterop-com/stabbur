"""Tests for the serving API (status + proxy guards), no real runtime needed."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from kodo import library as library_ops
from kodo.app import create_app
from kodo.config import Settings
from kodo.library import LibraryModel
from kodo.models import ModelFormat


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
