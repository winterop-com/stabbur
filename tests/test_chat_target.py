"""Tests for per-turn registry-target routing on /api/chat (Wave 3, chunk 3).

An /api/chat turn carries an optional ``target`` (a registry target id). With a non-empty registry
the toolset is narrowed to that target's servers plus any shared (unowned) servers, and the confirm
policy follows the resolved target's ``readonly``. Free-play (no registry) keeps the full toolset and
today's behavior. These tests patch ``agent.run`` to capture the toolset + policy the loop received,
the same fixture idiom ``tests/test_chat_confirm.py`` uses.
"""

import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from stabbur import agent, mcpservers, tools
from stabbur.app import create_app
from stabbur.config import Settings
from stabbur.project import AssistantInfo
from stabbur.routers import serving
from stabbur.targets import AssistantRegistry


def _servers(*names: str) -> list[mcpservers.McpServer]:
    """Resolved ``McpServer``s (only name/command matter for prefix routing; nothing is spawned)."""
    return [mcpservers.McpServer(name=n, command="x") for n in names]


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


class FakeManager:
    """A model is 'loaded' so /api/chat proceeds into the stream."""

    current = type("M", (), {"load_target": Path("/models/x")})()
    base_url = "http://runtime"


class _FakeTool:
    """A stand-in for an MCP tool as returned by ``client.list_tools()``."""

    def __init__(self, name: str, read_only: bool) -> None:
        self.name = name
        self.description = f"{name} tool"
        self.inputSchema: dict[str, Any] = {"type": "object", "properties": {}}
        self.annotations = type("A", (), {"readOnlyHint": read_only})()


class _FakeClient:
    """A stub MCP client whose ``list_tools()`` returns a fixed set of ``_FakeTool``s."""

    def __init__(self, tools_: list[_FakeTool]) -> None:
        self._tools = tools_

    async def list_tools(self) -> list[_FakeTool]:
        return self._tools


async def _make_toolset() -> tools.MCPToolset:
    """A toolset over three servers: two target-owned ('play42', 'staging') and a shared one ('datetime')."""
    toolset = tools.MCPToolset()
    await toolset.add(_FakeClient([_FakeTool("read", True)]), "play42")  # type: ignore[arg-type]
    await toolset.add(_FakeClient([_FakeTool("write_thing", False)]), "staging")  # type: ignore[arg-type]
    await toolset.add(_FakeClient([_FakeTool("now", True)]), "datetime")  # type: ignore[arg-type]
    return toolset


def _two_target_registry() -> AssistantRegistry:
    """A registry with a read-only primary ('play42') and a write-enabled second target ('staging')."""
    return AssistantRegistry(
        targets=[
            AssistantInfo(name="play42", mcp_servers=["play42"], readonly=True),
            AssistantInfo(name="staging", mcp_servers=["staging"], readonly=False),
        ]
    )


def _events(text: str) -> list[dict[str, Any]]:
    """Parse the SSE data events from a buffered /api/chat response body."""
    return [json.loads(line[len("data: ") :]) for line in text.splitlines() if line.startswith("data: ")]


def _install_capturing_run(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Any]) -> None:
    """Patch agent.run with a stub that records the toolset + confirm policy the loop received."""

    async def fake_run(
        base: str,
        messages: list[dict[str, Any]],
        toolset: Any,
        max_tokens: int | None,
        on_event: Any,
        on_token: Any,
        **kw: Any,
    ) -> str:
        captured["names"] = sorted(toolset.names)
        captured["prefixes"] = toolset.prefixes()
        captured["policy"] = kw.get("confirm_policy")
        captured["on_confirm"] = kw.get("on_confirm")
        await on_token("ok")
        return "ok"

    monkeypatch.setattr(agent, "run", fake_run)


async def _run(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> tuple[dict[str, Any], Any]:
    """POST one /api/chat turn with the given body; return (captured, response)."""
    app.dependency_overrides[serving.get_manager] = lambda: FakeManager()
    captured: dict[str, Any] = {}
    _install_capturing_run(monkeypatch, captured)
    try:
        resp = await client.post("/api/chat", json=body)
    finally:
        app.dependency_overrides.clear()
    return captured, resp


def _configure(
    app: FastAPI,
    toolset: tools.MCPToolset,
    registry: AssistantRegistry,
    resolved: list[mcpservers.McpServer] | None = None,
) -> None:
    """Wire the app state a serve lifespan would: toolset, registry, routing table, primary alias.

    The routing table is built via the *production* helper (``tools.build_target_routing``) from the
    resolved ``McpServer``s — the same path the serve lifespan uses — so the test seeds real slugged
    prefixes, not ``toolset.prefixes()`` shortcuts. ``resolved`` defaults to the three-server toolset.
    """
    app.state.toolset = toolset
    app.state.registry = registry
    app.state.assistant = registry.primary
    app.state.target_routing = tools.build_target_routing(
        resolved if resolved is not None else _servers("play42", "staging", "datetime"), registry
    )


async def _configure_lazy(
    app: FastAPI,
    registry: AssistantRegistry,
    resolved: list[mcpservers.McpServer],
    eager: dict[str, list[_FakeTool]],
    pending: dict[str, list[_FakeTool]],
) -> dict[str, int]:
    """Wire app state with a lazy MCP bridge: ``eager`` tools live now, ``pending`` spawned on first use.

    The bridge's ``_spawn`` is stubbed to add the pending server's fake tools instead of launching a real
    server; the returned counter records per-prefix spawn attempts so a test can assert first-use spawning.
    """
    toolset = tools.MCPToolset()
    for prefix, tls in eager.items():
        await toolset.add(_FakeClient(tls), prefix)  # type: ignore[arg-type]
    bridge = tools.MCPBridge(toolset, AsyncExitStack())
    bridge._pending = {p: mcpservers.McpServer(name=p, command="x") for p in pending}
    counts: dict[str, int] = {}

    async def fake_spawn(prefix: str, server: mcpservers.McpServer) -> bool:
        counts[prefix] = counts.get(prefix, 0) + 1
        await toolset.add(_FakeClient(pending[prefix]), prefix)  # type: ignore[arg-type]
        return True

    bridge._spawn = fake_spawn  # type: ignore[method-assign]
    app.state.toolset = toolset
    app.state.mcp_bridge = bridge
    app.state.registry = registry
    app.state.assistant = registry.primary
    app.state.target_routing = tools.build_target_routing(resolved, registry)
    return counts


# --- lazy per-target bridge: non-primary target's servers spawn on first use -------------------


async def test_lazy_nonprimary_target_spawns_on_first_use(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = await _configure_lazy(
        app,
        _two_target_registry(),
        _servers("play42", "staging", "datetime"),
        eager={"play42": [_FakeTool("read", True)], "datetime": [_FakeTool("now", True)]},
        pending={"staging": [_FakeTool("write_thing", False)]},
    )
    captured, resp = await _run(
        app, client, monkeypatch, {"messages": [{"role": "user", "content": "hi"}], "target": "staging"}
    )
    assert resp.status_code == 200
    # First turn for 'staging' spawned its deferred bridge; the turn sees it + the shared datetime.
    assert captured["prefixes"] == {"staging", "datetime"}
    assert captured["names"] == ["datetime__now", "staging__write_thing"]
    assert counts == {"staging": 1}


async def test_lazy_primary_target_does_not_spawn_secondary(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    counts = await _configure_lazy(
        app,
        _two_target_registry(),
        _servers("play42", "staging", "datetime"),
        eager={"play42": [_FakeTool("read", True)], "datetime": [_FakeTool("now", True)]},
        pending={"staging": [_FakeTool("write_thing", False)]},
    )
    # No target -> the primary (play42, eager). The non-primary 'staging' bridge is never spawned.
    captured, resp = await _run(app, client, monkeypatch, {"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert captured["prefixes"] == {"play42", "datetime"}
    assert counts == {}


# --- explicit target narrows to its servers + shared (others hidden) --------------------------


async def test_explicit_target_narrows_to_its_servers_plus_shared(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(app, await _make_toolset(), _two_target_registry())
    captured, resp = await _run(
        app, client, monkeypatch, {"messages": [{"role": "user", "content": "hi"}], "target": "staging"}
    )
    assert resp.status_code == 200
    # 'staging' owns 'staging'; 'datetime' is shared (owned by no target) → both kept, 'play42' hidden.
    assert captured["prefixes"] == {"staging", "datetime"}
    assert captured["names"] == ["datetime__now", "staging__write_thing"]


async def test_hyphenated_server_name_isolation(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The load-bearing case: a target's mcp_servers name is the RAW .mcp.json name ('dhis2-prod'), while
    # connect namespaces its tools under the SLUGGED prefix ('dhis2_prod'). The routing must map name ->
    # slug (via the production helper) or the narrowing would leak the sibling's tools.
    toolset = tools.MCPToolset()
    await toolset.add(_FakeClient([_FakeTool("read", True)]), "dhis2_prod")  # type: ignore[arg-type]
    await toolset.add(_FakeClient([_FakeTool("write_thing", False)]), "dhis2_staging")  # type: ignore[arg-type]
    registry = AssistantRegistry(
        targets=[
            AssistantInfo(name="dhis2-prod", mcp_servers=["dhis2-prod"], readonly=True),
            AssistantInfo(name="dhis2-staging", mcp_servers=["dhis2-staging"], readonly=False),
        ]
    )
    _configure(app, toolset, registry, resolved=_servers("dhis2-prod", "dhis2-staging"))
    captured, resp = await _run(
        app, client, monkeypatch, {"messages": [{"role": "user", "content": "hi"}], "target": "dhis2-prod"}
    )
    assert resp.status_code == 200
    # 'dhis2-prod' owns only its own tools; the hyphen->underscore slug is handled, so staging stays hidden.
    assert captured["prefixes"] == {"dhis2_prod"}
    assert captured["names"] == ["dhis2_prod__read"]


async def test_mixed_scoped_and_owns_all_keeps_shared_shared(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A scoped target A (explicit ['dhis2A']) alongside an owns-all target B: the shared 'datetime' server
    # stays shared (A keeps it), and B still resolves to everything. Owns-all does NOT swallow the shared.
    toolset = tools.MCPToolset()
    await toolset.add(_FakeClient([_FakeTool("read", True)]), "dhis2A")  # type: ignore[arg-type]
    await toolset.add(_FakeClient([_FakeTool("now", True)]), "datetime")  # type: ignore[arg-type]
    registry = AssistantRegistry(
        targets=[
            AssistantInfo(name="scoped", mcp_servers=["dhis2A"], readonly=True),
            AssistantInfo(name="catchall", mcp_servers=[], readonly=True),  # owns-all
        ]
    )
    _configure(app, toolset, registry, resolved=_servers("dhis2A", "datetime"))
    cap_a, resp_a = await _run(
        app, client, monkeypatch, {"messages": [{"role": "user", "content": "hi"}], "target": "scoped"}
    )
    assert resp_a.status_code == 200
    assert cap_a["prefixes"] == {"dhis2A", "datetime"}  # scoped gets its own + the shared datetime
    cap_b, resp_b = await _run(
        app, client, monkeypatch, {"messages": [{"role": "user", "content": "hi"}], "target": "catchall"}
    )
    assert resp_b.status_code == 200
    assert cap_b["prefixes"] == {"dhis2A", "datetime"}  # owns-all target resolves to everything


async def test_none_target_narrows_to_primary_plus_shared(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configure(app, await _make_toolset(), _two_target_registry())
    # No ``target`` field → the primary ('play42'); 'staging' hidden, 'datetime' shared kept.
    captured, resp = await _run(app, client, monkeypatch, {"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert captured["prefixes"] == {"play42", "datetime"}
    assert captured["names"] == ["datetime__now", "play42__read"]


async def test_freeplay_no_registry_keeps_full_toolset(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No registry (free-play): the full toolset survives, target field ignored, today's behavior.
    app.state.toolset = await _make_toolset()
    app.state.registry = AssistantRegistry()
    app.state.assistant = None
    app.state.target_routing = tools.TargetRouting()
    captured, resp = await _run(app, client, monkeypatch, {"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert captured["prefixes"] == {"play42", "staging", "datetime"}
    assert captured["policy"] == "none"


async def test_unknown_target_is_400(app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(app, await _make_toolset(), _two_target_registry())
    _, resp = await _run(app, client, monkeypatch, {"messages": [{"role": "user", "content": "hi"}], "target": "nope"})
    assert resp.status_code == 400


# --- single-[assistant] compat: one target owns ALL servers → narrowing is a no-op ------------


async def test_single_assistant_compat_narrowing_is_noop(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A single [assistant] with no mcp_servers is recorded owns-all (not pre-expanded to every prefix),
    # so narrowing keeps every tool — existing single-target behavior is preserved.
    registry = AssistantRegistry(targets=[AssistantInfo(name="play42", mcp_servers=[], readonly=True)])
    _configure(app, await _make_toolset(), registry)
    assert app.state.target_routing == tools.TargetRouting(owns_all={"play42"})
    captured, resp = await _run(app, client, monkeypatch, {"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert captured["prefixes"] == {"play42", "staging", "datetime"}


# --- confirm-policy default follows the resolved target's readonly -----------------------------


async def test_confirm_default_readonly_target_is_none(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The read-only primary ('play42') gates nothing: policy "none", no confirm channel wired.
    _configure(app, await _make_toolset(), _two_target_registry())
    captured, resp = await _run(
        app, client, monkeypatch, {"messages": [{"role": "user", "content": "hi"}], "target": "play42"}
    )
    assert resp.status_code == 200
    assert captured["policy"] == "none"
    assert captured["on_confirm"] is None


async def test_confirm_default_write_target_gates_writes(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The write-enabled 'staging' target defaults to gating writes: policy "writes", confirm channel wired.
    _configure(app, await _make_toolset(), _two_target_registry())
    captured, resp = await _run(
        app, client, monkeypatch, {"messages": [{"role": "user", "content": "hi"}], "target": "staging"}
    )
    assert resp.status_code == 200
    assert captured["policy"] == "writes"
    assert captured["on_confirm"] is not None


async def test_explicit_confirm_tools_overrides_target_default(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An explicit confirm_tools still wins over the resolved target's default.
    _configure(app, await _make_toolset(), _two_target_registry())
    captured, resp = await _run(
        app,
        client,
        monkeypatch,
        {"messages": [{"role": "user", "content": "hi"}], "target": "play42", "confirm_tools": "all"},
    )
    assert resp.status_code == 200
    assert captured["policy"] == "all"


# --- enabled_tools intersects AFTER target narrowing ------------------------------------------


async def test_enabled_tools_intersects_after_narrowing(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # enabled_tools narrows on top of target narrowing: a tool owned by a hidden target can't be
    # re-enabled, and a shared tool the client omits is dropped.
    _configure(app, await _make_toolset(), _two_target_registry())
    captured, resp = await _run(
        app,
        client,
        monkeypatch,
        {
            "messages": [{"role": "user", "content": "hi"}],
            "target": "staging",
            "enabled_tools": ["staging__write_thing", "play42__read"],  # play42__read already hidden
        },
    )
    assert resp.status_code == 200
    assert captured["names"] == ["staging__write_thing"]  # play42__read stays hidden; datetime__now not enabled
