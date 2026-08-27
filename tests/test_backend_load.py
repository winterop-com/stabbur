"""Tests for ``/api/load`` resolving a qualified ``model@backend`` id across declared backends.

The bug these pin down: the route used to branch on whether the *active* backend was an
upstream and never look at the ``@`` at all, so with several backends declared the picker had no
way to say which one it meant. Clicking a remote's row while the library was active fed its name
to ``library.find`` — and a same-named local model loaded instead, silently.
"""

from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from stabbur import backends, runtime
from stabbur import library as library_ops
from stabbur.app import create_app
from stabbur.backends import BackendSpec, parse_id
from stabbur.config import Settings
from stabbur.library import LibraryModel, _scan
from stabbur.models import ModelFormat
from stabbur.server import ServerManager, UpstreamManager, UpstreamModel

# The name both backends serve — the collision the qualifier exists to resolve.
SHARED = "gemma-4-12B-it-QAT-GGUF"

LOCAL = BackendSpec(name="local")
REMOTE = BackendSpec(name="gpu-box", url="http://gpu-box:8080/v1")


def _library_model(path: Path, name: str) -> LibraryModel:
    return LibraryModel(name=name, model_format=ModelFormat.gguf, path=path, load_target=path / "w.gguf")


@pytest.fixture
def loaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Record what each backend was asked to load, without spawning or dialling anything.

    Local rows and remote rows deliberately overlap on :data:`SHARED`; the remote also serves an
    id that exists nowhere locally, which is the "remote active, local model clicked" direction.
    """
    calls: list[str] = []
    local_rows = [_library_model(tmp_path, SHARED), _library_model(tmp_path, "pub/Local-only-GGUF")]
    remote_rows = [UpstreamModel(name=SHARED), UpstreamModel(name="some-remote-model", loaded=True)]

    # Both spellings of the scan: ``Backends._rows`` reads the package re-export, while
    # ``library.find`` (what the load resolves through) reads the one in ``_scan``. Patching the
    # scan rather than ``find`` keeps the real matching rules in play — they are half of what
    # decides whether two backends collide.
    monkeypatch.setattr(library_ops, "scan", lambda *a, **k: list(local_rows))
    monkeypatch.setattr(_scan, "scan", lambda *a, **k: list(local_rows))
    monkeypatch.setattr(runtime, "runnable_error", lambda m: None)
    monkeypatch.setattr(UpstreamManager, "models", lambda self: list(remote_rows))

    # ``ServerManager.current`` is derived from a live child process, so a fake load has to
    # stand in for the whole thing rather than set a field: what matters here is only that a
    # successful load reads back as loaded.
    resident: dict[str, LibraryModel | None] = {"local": None}

    def _local_load(self: ServerManager, model: LibraryModel, n_ctx: int | None = None) -> None:
        calls.append(f"local:{model.name}")
        resident["local"] = model

    async def _local_ready(self: ServerManager) -> bool:
        return True

    monkeypatch.setattr(ServerManager, "load", _local_load)
    monkeypatch.setattr(ServerManager, "ready", _local_ready)
    monkeypatch.setattr(ServerManager, "current", property(lambda self: resident["local"]))

    def _remote_load(self: UpstreamManager, name: str, *, warmup: bool = True) -> None:
        match = next((r for r in remote_rows if r.name.lower() == name.strip().lower()), None)
        if match is None:
            raise RuntimeError(f"{name!r} is not served by {self.base_url} — available: ...")
        calls.append(f"gpu-box:{match.name}")
        self._selected = match  # noqa: SLF001 - stand in for the remote's own selection

    async def _remote_ready(self: UpstreamManager) -> bool:
        return True

    monkeypatch.setattr(UpstreamManager, "load_by_name", _remote_load)
    monkeypatch.setattr(UpstreamManager, "ready", _remote_ready)
    return calls


def _app(active: str = "local") -> FastAPI:
    """An app holding both backends, with ``active`` the one the scalar surface points at.

    The declaration is installed directly rather than routed through Settings: parsing
    ``[[backends]]`` is a different change's job, and this file is about what ``/api/load``
    does with a declaration however it arrived.
    """
    app = create_app(Settings(serve_model=None))
    app.state.manager = backends.declare([LOCAL, REMOTE], active=active)
    return app


async def _post(app: FastAPI, path: str) -> tuple[int, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(path)
    return response.status_code, response.json()


async def _status(app: FastAPI) -> dict[str, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/status")
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def test_parse_id_splits_on_the_last_at() -> None:
    # The model half owns every other separator (publisher `/`, Ollama `:`), so only the LAST
    # `@` can be the qualifier — and a name with none is bare, not a parse failure.
    assert parse_id("gemma4:12b-mlx@gpu-box") == backends.ModelId(model="gemma4:12b-mlx", backend="gpu-box")
    assert parse_id("unsloth/Qwen3.5-4B-GGUF@local") == backends.ModelId(
        model="unsloth/Qwen3.5-4B-GGUF", backend="local"
    )
    assert parse_id("a@b@c") == backends.ModelId(model="a@b", backend="c")
    assert parse_id("plain-name") == backends.ModelId(model="plain-name", backend=None)
    # An empty half qualifies nothing: the whole string stays the model, never a silent truncation.
    assert parse_id("trailing@") == backends.ModelId(model="trailing@", backend=None)
    assert parse_id("@leading") == backends.ModelId(model="@leading", backend=None)


async def test_qualified_id_loads_on_the_named_backend(loaded: list[str]) -> None:
    # THE REGRESSION. Local is active and the library holds a model of the same name, so the old
    # route (which branched on the active backend and never parsed `@`) loaded the LOCAL copy.
    app = _app(active="local")
    code, body = await _post(app, f"/api/load/{SHARED}@gpu-box")
    assert code == 200, body
    assert loaded == [f"gpu-box:{SHARED}"]  # the remote's, not the same-named local one
    assert body["backend"] == "gpu-box"
    assert body["upstream"] == "http://gpu-box:8080"


async def test_qualified_id_loads_locally_while_a_remote_is_active(loaded: list[str]) -> None:
    # The other direction: the remote is active, and the old route sent the name to it — a 404
    # from a host that never had the model. Qualifying it activates the library instead.
    app = _app(active="gpu-box")
    code, body = await _post(app, "/api/load/pub/Local-only-GGUF@local")
    assert code == 200, body
    assert loaded == ["local:pub/Local-only-GGUF"]
    assert body["backend"] == "local"
    assert body["upstream"] is None


async def test_unknown_backend_is_404_naming_the_declared_ones(loaded: list[str]) -> None:
    app = _app(active="local")
    code, body = await _post(app, f"/api/load/{SHARED}@nowhere")
    assert code == 404
    assert body["detail"] == "No backend named 'nowhere' — declared: local, gpu-box"
    assert loaded == []
    assert (await _status(app))["backend"] == "local"  # a bad qualifier repoints nothing


async def test_bare_name_on_two_backends_is_409_naming_both(loaded: list[str]) -> None:
    # Never a silent pick: the same name on two backends is exactly the collision the qualifier
    # exists for, so the caller is told what to retry with rather than given one of them.
    app = _app(active="local")
    code, body = await _post(app, f"/api/load/{SHARED}")
    assert code == 409
    assert body["detail"] == (
        f"'{SHARED}' is served by more than one backend — load one of: {SHARED}@local, {SHARED}@gpu-box"
    )
    assert loaded == []


async def test_bare_name_still_resolves_on_the_active_backend(loaded: list[str]) -> None:
    # A name only one backend has keeps today's behaviour: it resolves on the ACTIVE backend, and
    # a name the active backend does not have is still that backend's 404.
    app = _app(active="local")
    code, body = await _post(app, "/api/load/pub/Local-only-GGUF")
    assert code == 200, body
    assert loaded == ["local:pub/Local-only-GGUF"]

    code, body = await _post(app, "/api/load/some-remote-model")  # the remote's, but local is active
    assert code == 404
    assert loaded == ["local:pub/Local-only-GGUF"]


async def test_a_failed_qualified_load_puts_the_active_backend_back(loaded: list[str]) -> None:
    # Activating is a side effect of resolution, so a load that then fails must unwind it —
    # otherwise /v1 and /api/chat end up pointed at a backend holding nothing, and the model the
    # caller still had running becomes unreachable.
    app = _app(active="local")
    assert (await _post(app, f"/api/load/{SHARED}@local"))[0] == 200
    code, _ = await _post(app, "/api/load/not-served-anywhere@gpu-box")
    assert code == 404
    status = await _status(app)
    assert status["backend"] == "local"
    assert status["model"] == SHARED  # still loaded, still addressable


async def test_a_down_backend_does_not_block_a_bare_load(loaded: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    # The ambiguity probe runs beside the load, so a backend that cannot be asked is simply not a
    # candidate: an unreachable remote must not cost the caller a load aimed at the library.
    def _boom(self: UpstreamManager) -> list[UpstreamModel]:
        raise RuntimeError("upstream http://gpu-box:8080 is unreachable")

    monkeypatch.setattr(UpstreamManager, "models", _boom)
    app = _app(active="local")
    code, body = await _post(app, f"/api/load/{SHARED}")
    assert code == 200, body
    assert loaded == [f"local:{SHARED}"]


async def test_status_names_the_active_backend_for_a_single_backend_too(loaded: list[str]) -> None:
    # With one backend the qualifier is redundant, not absent — a client must not have to
    # special-case the single-backend case to know where the loaded model lives.
    app = create_app(Settings(serve_model=None))
    assert (await _status(app))["backend"] == "local"
