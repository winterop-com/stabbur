"""Tests for the multi-target registry endpoints (/api/assistants*), plus /api/assistant compat.

Named ``test_assistants_registry`` (not ``test_serve_registry`` — that name already covers the unrelated
``heim.runtime.serve_registry`` serve-discovery module). The lifespan doesn't run under ASGITransport, so
these set ``app.state.registry`` (and ``app.state.assistant`` = the primary) directly, mirroring the
single-target tests in test_api.py.
"""

import sys
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from heim.app import create_app
from heim.config import Settings
from heim.project import AssistantInfo
from heim.targets import AssistantRegistry


@pytest.fixture
def app() -> FastAPI:
    """App with a clean (no model loaded) manager and an empty registry."""
    return create_app(Settings(serve_model=None))


@pytest.fixture
async def client(app: FastAPI):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class _FakeToolset:
    """A stand-in for MCPToolset: a names list + a per-tool call counter."""

    def __init__(self, names: list[str], result: Any = None) -> None:
        self.names = names
        self._result = result
        self.calls: dict[str, int] = {}

    async def call_structured(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> Any:
        self.calls[name] = self.calls.get(name, 0) + 1
        return self._result if self._result is not None else {"tool": name}


def _target(
    name: str, base_url: str, *, verify_tool: str | None = None, bind_proof: str | None = None
) -> AssistantInfo:
    data: dict[str, Any] = {"name": name, "base_url": base_url}
    if verify_tool is not None:
        data["verify"] = {"tool": verify_tool, "args": {"args": ["verify"]}, "timeout": 5.0}
    if bind_proof is not None:
        code = "import os,sys\nopen(sys.argv[1],'w').write(os.environ.get('S','')+'|'+sys.argv[2])\n"
        data["bind"] = {
            "modes": {"pat": {"command": [sys.executable, "-c", code, bind_proof, "{base_url}"], "secret_env": "S"}}
        }
    return AssistantInfo.model_validate(data)


def _install(app: FastAPI, *targets: AssistantInfo) -> AssistantRegistry:
    registry = AssistantRegistry(targets=list(targets))
    app.state.registry = registry
    app.state.assistant = registry.primary  # lifespan sets this; keep compat routes on the primary
    return registry


# --- list shape + ids ---------------------------------------------------------------------------------


async def test_assistants_empty_registry_is_200_empty_list(client: AsyncClient) -> None:
    # An empty registry is 200 {"targets": []}, NOT 404 — the extension reads generic mode from the
    # empty list (the /api/assistant compat 404 is the other generic-mode signal).
    r = await client.get("/api/assistants")
    assert r.status_code == 200
    assert r.json() == {"targets": []}


async def test_assistants_lists_sanitized_targets_with_registry_ids(app: FastAPI, client: AsyncClient) -> None:
    registry = _install(
        app,
        _target("play42", "https://play.example/dev-2-42"),
        _target("play42", "https://play.example/dev-2-41"),  # same name -> collision-safe id
    )
    body = (await client.get("/api/assistants")).json()
    assert "selected" not in body and "matches" not in body  # selection keys omitted without ?url=
    ids = [t["id"] for t in body["targets"]]
    assert ids == registry.ids == ["play42", "play42-2"]  # collision disambiguated
    assert body["targets"][0]["base_url"] == "https://play.example/dev-2-42"
    assert body["targets"][0]["mcp_servers"] == []  # echoed field present


async def test_assistants_entry_sanitizes_bind_like_compat(app: FastAPI, client: AsyncClient) -> None:
    import json as _json

    _install(app, _target("play42", "https://play.example/x", bind_proof="/tmp/never"))
    entry = (await client.get("/api/assistants")).json()["targets"][0]
    assert entry["can_bind"] is True
    assert entry["bind"]["modes"] == ["pat"]  # names only
    dumped = _json.dumps(entry["bind"])
    assert "secret_env" not in dumped and "command" not in dumped  # execution details never echoed


# --- ?url= selection ----------------------------------------------------------------------------------


async def test_assistants_url_single_match_selects(app: FastAPI, client: AsyncClient) -> None:
    _install(
        app,
        _target("play42", "https://play.example/dev-2-42"),
        _target("play41", "https://play.example/dev-2-41"),
    )
    body = (await client.get("/api/assistants", params={"url": "https://play.example/dev-2-42/dhis-web/x"})).json()
    assert body["matches"] == ["play42"]
    assert body["selected"] == "play42"


async def test_assistants_url_tie_selects_null_keeps_both(app: FastAPI, client: AsyncClient) -> None:
    # Two root-path targets on one origin tie for any url there: selected null, both ids in matches.
    _install(
        app,
        _target("alpha", "https://tie.example"),
        _target("beta", "https://tie.example"),
    )
    body = (await client.get("/api/assistants", params={"url": "https://tie.example/anything"})).json()
    assert body["selected"] is None
    assert sorted(body["matches"]) == ["alpha", "beta"]


async def test_assistants_url_no_match_selects_null_empty(app: FastAPI, client: AsyncClient) -> None:
    _install(app, _target("play42", "https://play.example/dev-2-42"))
    body = (await client.get("/api/assistants", params={"url": "https://other.example/x"})).json()
    assert body["selected"] is None and body["matches"] == []


# --- per-target verify (isolation, 404, TTL) ----------------------------------------------------------


async def test_assistants_verify_is_per_target_isolated(app: FastAPI, client: AsyncClient) -> None:
    _install(
        app,
        _target("play42", "https://play.example/dev-2-42", verify_tool="tool_a"),
        _target("play41", "https://play.example/dev-2-41", verify_tool="tool_b"),
    )
    fake = _FakeToolset(names=["tool_a", "tool_b"])
    app.state.toolset = fake

    a = (await client.get("/api/assistants/play42", params={"verify": 1})).json()
    assert a["verified"]["ok"] is True and a["verified"]["data"] == {"tool": "tool_a"}
    # Only A's cache slot is populated; B is untouched.
    assert set(app.state.assistant_verified_by_id) == {"play42"}
    assert fake.calls == {"tool_a": 1}

    b = (await client.get("/api/assistants/play41", params={"verify": 1})).json()
    assert b["verified"]["data"] == {"tool": "tool_b"}  # B verifies independently via its own tool
    assert set(app.state.assistant_verified_by_id) == {"play42", "play41"}
    assert fake.calls == {"tool_a": 1, "tool_b": 1}


async def test_assistants_verify_uses_per_id_ttl_cache(app: FastAPI, client: AsyncClient) -> None:
    from heim.routers.serving.assistant import AssistantVerified

    _install(app, _target("play42", "https://play.example/x", verify_tool="tool_a"))
    fake = _FakeToolset(names=["tool_a"], result={"live": True})
    app.state.toolset = fake
    cached = AssistantVerified(ok=True, data={"cached": True}, checked_at=time.time())
    app.state.assistant_verified_by_id = {"play42": (cached.checked_at, cached)}
    body = (await client.get("/api/assistants/play42", params={"verify": 1})).json()
    assert body["verified"]["data"] == {"cached": True}  # served from the per-id cache
    assert fake.calls == {}  # the tool was not re-run


async def test_assistants_detail_and_verify_404_for_unknown_id(app: FastAPI, client: AsyncClient) -> None:
    _install(app, _target("play42", "https://play.example/x", verify_tool="tool_a"))
    app.state.toolset = _FakeToolset(names=["tool_a"])
    assert (await client.get("/api/assistants/nope")).status_code == 404
    assert (await client.get("/api/assistants/nope", params={"verify": 1})).status_code == 404


async def test_assistants_concurrent_verify_probes_once_per_target(app: FastAPI, client: AsyncClient) -> None:
    import asyncio

    class _SlowToolset(_FakeToolset):
        async def call_structured(self, name: str, arguments: dict[str, Any], timeout: float | None = None) -> Any:
            self.calls[name] = self.calls.get(name, 0) + 1
            await asyncio.sleep(0.05)
            return {"tool": name}

    _install(app, _target("play42", "https://play.example/x", verify_tool="tool_a"))
    fake = _SlowToolset(names=["tool_a"])
    app.state.toolset = fake
    a, b = await asyncio.gather(
        client.get("/api/assistants/play42", params={"verify": 1}),
        client.get("/api/assistants/play42", params={"verify": 1}),
    )
    assert a.json()["verified"]["data"] == {"tool": "tool_a"}
    assert b.json()["verified"]["data"] == {"tool": "tool_a"}
    assert fake.calls == {"tool_a": 1}  # single-flight: one probe served both


# --- per-target bind (routing, 404, cache invalidation) -----------------------------------------------


async def test_assistants_bind_routes_to_the_right_target(app: FastAPI, client: AsyncClient, tmp_path: Path) -> None:
    proof_a, proof_b = tmp_path / "a.txt", tmp_path / "b.txt"
    _install(
        app,
        _target("play42", "https://a.example/x", bind_proof=str(proof_a)),
        _target("play41", "https://b.example/x", bind_proof=str(proof_b)),
    )
    r = await client.post("/api/assistants/play41/bind", json={"mode": "pat", "secret": "SEKRET"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert proof_b.read_text() == "SEKRET|https://b.example/x"  # B's mode ran, base_url templated from B
    assert not proof_a.exists()  # A's mode did not run


async def test_assistants_bind_invalidates_only_that_target(app: FastAPI, client: AsyncClient, tmp_path: Path) -> None:
    from heim.routers.serving.assistant import AssistantVerified

    proof_a = tmp_path / "a.txt"
    _install(
        app,
        _target("play42", "https://a.example/x", bind_proof=str(proof_a)),
        _target("play41", "https://b.example/x"),
    )
    stale = AssistantVerified(ok=True, data={}, checked_at=time.time())
    app.state.assistant_verified_by_id = {"play42": (stale.checked_at, stale), "play41": (stale.checked_at, stale)}
    r = await client.post("/api/assistants/play42/bind", json={"mode": "pat", "secret": "s"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert set(app.state.assistant_verified_by_id) == {"play41"}  # only play42's slot cleared


async def test_assistants_bind_unbind_404_for_unknown_id(app: FastAPI, client: AsyncClient) -> None:
    _install(app, _target("play42", "https://a.example/x", bind_proof="/tmp/never"))
    assert (await client.post("/api/assistants/nope/bind", json={"mode": "pat", "secret": "s"})).status_code == 404
    assert (await client.post("/api/assistants/nope/unbind", json={"mode": "pat"})).status_code == 404


async def test_assistants_unbind_runs_for_target(app: FastAPI, client: AsyncClient, tmp_path: Path) -> None:
    marker = tmp_path / "unbound.txt"
    code = "import sys\nopen(sys.argv[1],'w').write('unbound')\n"
    info = AssistantInfo.model_validate(
        {
            "name": "play42",
            "base_url": "https://a.example/x",
            "bind": {
                "modes": {
                    "pat": {
                        "command": [sys.executable, "-c", "import sys"],
                        "secret_env": "S",
                        "unbind_command": [sys.executable, "-c", code, str(marker)],
                    }
                }
            },
        }
    )
    _install(app, info)
    r = await client.post("/api/assistants/play42/unbind", json={"mode": "pat"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert marker.read_text() == "unbound"


# --- compat routes still target the primary -----------------------------------------------------------


async def test_compat_assistant_returns_primary(app: FastAPI, client: AsyncClient) -> None:
    _install(
        app,
        _target("play42", "https://play.example/dev-2-42"),
        _target("play41", "https://play.example/dev-2-41"),
    )
    body = (await client.get("/api/assistant")).json()
    assert body["name"] == "play42"  # the primary (first) target, not the second


async def test_compat_bind_targets_primary(app: FastAPI, client: AsyncClient, tmp_path: Path) -> None:
    proof_primary, proof_secondary = tmp_path / "p.txt", tmp_path / "s.txt"
    _install(
        app,
        _target("play42", "https://a.example/x", bind_proof=str(proof_primary)),
        _target("play41", "https://b.example/x", bind_proof=str(proof_secondary)),
    )
    r = await client.post("/api/assistant/bind", json={"mode": "pat", "secret": "s"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert proof_primary.exists() and not proof_secondary.exists()  # compat ran the primary's mode
