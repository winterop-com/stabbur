"""Tests for /api/library merged across several declared backends, including down ones."""

import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from stabbur import backends
from stabbur import library as library_ops
from stabbur.app import create_app
from stabbur.backends import BackendSpec, declare
from stabbur.config import Settings
from stabbur.library import LibraryModel
from stabbur.models import ModelFormat
from stabbur.server import UpstreamManager, UpstreamModel

LOCAL = BackendSpec(name="local")
MSAI = BackendSpec(name="gpu-box", url="http://gpu-box:8080/v1")
BOX = BackendSpec(name="box", url="http://box:9000")


def _library_model(path: Path, name: str) -> LibraryModel:
    return LibraryModel(name=name, model_format=ModelFormat.gguf, path=path, load_target=path)


def _stub_upstreams(monkeypatch: pytest.MonkeyPatch, behaviour: dict[str, object]) -> None:
    """Answer ``UpstreamManager.models`` per host: rows, an exception, or a hang until released."""

    def _models(self: UpstreamManager) -> list[UpstreamModel]:
        outcome = behaviour[self.base_url]
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, threading.Event):
            assert outcome.wait(30), "the hung probe was never released"
            return []
        return cast(list[UpstreamModel], outcome)

    monkeypatch.setattr(UpstreamManager, "models", _models)


async def _library(app: FastAPI) -> list[dict[str, Any]]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/library")
    assert response.status_code == 200, response.text
    return cast(list[dict[str, Any]], response.json())


def _app(specs: list[BackendSpec]) -> FastAPI:
    """An app holding the given declaration.

    Config parsing is another change's job, so the declaration is installed directly rather
    than routed through Settings — this file is about what ``/api/library`` does with one.
    """
    app = create_app(Settings(serve_model=None))
    app.state.manager = backends.declare(specs)
    return app


async def test_library_merges_every_backend_and_names_each_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(library_ops, "scan", lambda: [_library_model(tmp_path, "pub/Local-GGUF")])
    _stub_upstreams(
        monkeypatch,
        {
            "http://gpu-box:8080": [UpstreamModel(name="gemma-4-12b", loaded=True, vision=True)],
            "http://box:9000": [UpstreamModel(name="qwen3-coder")],
        },
    )

    rows = await _library(_app([LOCAL, MSAI, BOX]))

    # Every row says where it came from — the name half of a model@backend id, and what the
    # picker groups by. Two hosts serving the same model name are only tellable apart by it.
    assert [(r["name"], r["backend"]) for r in rows] == [
        ("pub/Local-GGUF", "local"),
        ("gemma-4-12b", "gpu-box"),
        ("qwen3-coder", "box"),
    ]
    assert rows[0]["model_format"] == "gguf"  # the local rows keep their real format and size
    assert rows[1]["model_format"] == "remote" and rows[1]["vision"] and rows[1]["tags"] == ["loaded"]
    assert all(r["error"] is None for r in rows)


async def test_a_down_backend_becomes_a_row_and_the_rest_still_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The requirement that matters: never an empty list, never a 502. A picker that silently
    # drops an unreachable host looks exactly like a host with no models.
    monkeypatch.setattr(library_ops, "scan", lambda: [_library_model(tmp_path, "pub/Local-GGUF")])
    _stub_upstreams(
        monkeypatch,
        {
            "http://gpu-box:8080": RuntimeError("upstream http://gpu-box:8080 unreachable: [Errno 61] refused"),
            "http://box:9000": [UpstreamModel(name="qwen3-coder")],
        },
    )

    rows = await _library(_app([LOCAL, MSAI, BOX]))

    assert [r["name"] for r in rows] == ["pub/Local-GGUF", "gpu-box", "qwen3-coder"]
    dead = rows[1]
    assert dead["backend"] == "gpu-box" and dead["model_format"] == "unavailable"
    assert "refused" in dead["error"]
    assert [r["error"] for r in rows if r["name"] != "gpu-box"] == [None, None]


async def test_a_hung_backend_costs_the_listing_a_timeout_not_the_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A powered-off host black-holes the connection instead of refusing it, so only a deadline
    # ends the wait. The probe is STILL RUNNING when the response arrives, which is the proof:
    # the listing did not wait for it, and the healthy rows are all there.
    monkeypatch.setattr(library_ops, "scan", lambda: [_library_model(tmp_path, "pub/Local-GGUF")])
    monkeypatch.setattr(backends, "PROBE_TIMEOUT", 0.3)
    hung = threading.Event()
    _stub_upstreams(monkeypatch, {"http://gpu-box:8080": hung, "http://box:9000": [UpstreamModel(name="qwen3-coder")]})
    try:
        started = time.monotonic()
        rows = await _library(_app([LOCAL, MSAI, BOX]))
        elapsed = time.monotonic() - started

        assert elapsed < 2.0, f"the hung backend stalled the response ({elapsed:.2f}s)"
        assert not hung.is_set(), "the probe finished, so this proved nothing about the timeout"
        assert [r["name"] for r in rows] == ["pub/Local-GGUF", "gpu-box", "qwen3-coder"]
        assert rows[1]["error"] == "did not answer within 0.3s"
    finally:
        hung.set()


async def test_one_local_backend_lists_exactly_what_it_did_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The single-backend path is the one that must not move: same rows, same filtering
    # (non-generative and Ollama-native models are not runnable by this picker), plus the
    # qualifier every row now carries.
    embedder = LibraryModel(
        name="st/embed",
        model_format=ModelFormat.safetensors,
        generative=False,
        path=tmp_path,
        load_target=tmp_path,
    )
    monkeypatch.setattr(library_ops, "scan", lambda: [_library_model(tmp_path, "pub/Chat-GGUF"), embedder])

    rows = await _library(_app([LOCAL]))

    assert [r["name"] for r in rows] == ["pub/Chat-GGUF"]
    assert rows[0]["backend"] == "local"


def test_declare_starts_pointed_at_the_named_backend_not_the_first() -> None:
    """The seam where declaration and the facade meet, and where they disagreed.

    `declared_backends` lists the library FIRST because that is how a picker should read.
    `declare` defaults to the first spec. Wire those together with no explicit active and
    `serve --upstream gpu-box` starts pointed at the library - the flag silently stops meaning
    what it says. Pinned because it is a wrong-answer failure, not a crash.
    """
    specs = [BackendSpec(name="local"), BackendSpec(name="gpu-box", url="http://gpu-box:8080/v1")]

    assert declare(specs).name == "local"  # default: first declared
    assert declare(specs, active="gpu-box").name == "gpu-box"  # what --upstream must produce


def test_declare_refuses_an_active_backend_that_was_not_declared() -> None:
    # A typo here would otherwise pick the first backend silently, which is the same
    # wrong-answer failure one layer up.
    specs = [BackendSpec(name="local"), BackendSpec(name="gpu-box", url="http://gpu-box:8080/v1")]
    with pytest.raises(ValueError, match="was not declared"):
        declare(specs, active="msia")
