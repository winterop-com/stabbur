"""Tests for lazy per-target MCP bridge spawning (heim.tools.MCPBridge + connect_bridge).

A multi-target registry spawns only the eager set at startup — shared/unowned servers plus the primary
target's own servers — and defers a non-primary scoped target's servers to their first use. These tests
exercise the split policy (``_eager_prefixes``), the on-demand spawn (single-flight, failure + retry),
and ``connect_bridge`` end-to-end with a stubbed spawn so nothing real is launched.
"""

import asyncio
from contextlib import AsyncExitStack
from typing import Any

from heim import tools
from heim.mcpservers import McpServer


class _FakeTool:
    """A stand-in for an MCP tool as returned by ``client.list_tools()``."""

    def __init__(self, name: str, read_only: bool = True) -> None:
        self.name = name
        self.description = ""
        self.inputSchema: dict[str, Any] = {"type": "object", "properties": {}}
        self.annotations = type("A", (), {"readOnlyHint": read_only})()


class _FakeClient:
    """A stub MCP client whose ``list_tools()`` returns a fixed set of ``_FakeTool``s."""

    def __init__(self, names: list[str]) -> None:
        self._tools = [_FakeTool(n) for n in names]

    async def list_tools(self) -> list[_FakeTool]:
        return self._tools


def _bridge(
    pending: dict[str, list[str]], *, delay: float = 0.0, fail_first: set[str] | None = None
) -> tuple[tools.MCPBridge, dict[str, int]]:
    """An ``MCPBridge`` whose ``_spawn`` adds fake tools instead of launching a real server.

    ``pending`` maps a fixed prefix to the tool base names its (deferred) server exposes; ``fail_first``
    marks prefixes whose *first* spawn fails (to exercise the retry path). Returns the bridge and a
    per-prefix spawn-attempt counter so single-flight / retry can be asserted.
    """
    toolset = tools.MCPToolset()
    bridge = tools.MCPBridge(toolset, AsyncExitStack())
    bridge._pending = {p: McpServer(name=p, command="x") for p in pending}
    counts: dict[str, int] = {}
    failed_once: set[str] = set()
    fail = fail_first or set()

    async def fake_spawn(prefix: str, server: McpServer) -> bool:
        counts[prefix] = counts.get(prefix, 0) + 1
        if delay:
            await asyncio.sleep(delay)
        if prefix in fail and prefix not in failed_once:
            failed_once.add(prefix)
            toolset.errors.append((prefix, "boom"))
            return False
        await toolset.add(_FakeClient(pending[prefix]), prefix)  # type: ignore[arg-type]
        return True

    bridge._spawn = fake_spawn  # type: ignore[method-assign]
    return bridge, counts


# --- _eager_prefixes: the eager-vs-lazy split policy ------------------------------------------------


def test_eager_split_primary_and_shared_eager_nonprimary_lazy() -> None:
    # A scoped primary + a scoped non-primary: eager = primary's own + shared (owned by nobody); the
    # non-primary target's own server is the only one deferred.
    routing = tools.TargetRouting(explicit={"play42": {"play42"}, "staging": {"staging"}})
    eager = tools._eager_prefixes(routing, "play42", {"play42", "staging", "datetime"}, eager_all=False)
    assert eager == {"play42", "datetime"}  # staging (non-primary) is lazy


def test_eager_split_freeplay_is_full_eager() -> None:
    # Free-play (empty routing) pins today's behavior: everything eager.
    eager = tools._eager_prefixes(tools.TargetRouting(), None, {"a", "b"}, eager_all=False)
    assert eager == {"a", "b"}


def test_eager_split_single_owns_all_is_full_eager() -> None:
    # A single-[assistant] project (primary owns-all) sees the whole toolset, so nothing is deferred.
    routing = tools.TargetRouting(owns_all={"solo"})
    eager = tools._eager_prefixes(routing, "solo", {"a", "b"}, eager_all=False)
    assert eager == {"a", "b"}


def test_eager_split_owns_all_primary_with_scoped_sibling_is_full_eager() -> None:
    # An owns-all PRIMARY alongside a scoped sibling: the primary reaches everything, so nothing lazy.
    routing = tools.TargetRouting(explicit={"scoped": {"a"}}, owns_all={"catchall"})
    eager = tools._eager_prefixes(routing, "catchall", {"a", "b"}, eager_all=False)
    assert eager == {"a", "b"}


def test_eager_split_eager_all_flag_forces_full() -> None:
    # HEIM_EAGER_MCP=1 (eager_all) restores full eager spawning even for a multi-target registry.
    routing = tools.TargetRouting(explicit={"play42": {"play42"}, "staging": {"staging"}})
    eager = tools._eager_prefixes(routing, "play42", {"play42", "staging"}, eager_all=True)
    assert eager == {"play42", "staging"}


# --- MCPBridge: spawn-on-demand -------------------------------------------------------------------


async def test_lazy_tools_absent_before_first_use_present_after() -> None:
    bridge, counts = _bridge({"staging": ["write_thing"]})
    assert bridge.toolset.names == []  # nothing spawned yet
    assert bridge.pending_prefixes == {"staging"}
    routing = tools.TargetRouting(explicit={"staging": {"staging"}})
    await bridge.ensure_target(routing, "staging")
    assert bridge.toolset.names == ["staging__write_thing"]  # present after first use
    assert bridge.pending_prefixes == set()
    assert counts == {"staging": 1}


async def test_concurrent_first_uses_spawn_once() -> None:
    # Two concurrent first-turns for the same target must single-flight the spawn.
    bridge, counts = _bridge({"staging": ["w"]}, delay=0.02)
    routing = tools.TargetRouting(explicit={"staging": {"staging"}})
    await asyncio.gather(
        bridge.ensure_target(routing, "staging"),
        bridge.ensure_target(routing, "staging"),
    )
    assert counts == {"staging": 1}  # one spawn served both
    assert bridge.toolset.names == ["staging__w"]


async def test_spawn_failure_is_clean_and_retries() -> None:
    bridge, counts = _bridge({"staging": ["w"]}, fail_first={"staging"})
    routing = tools.TargetRouting(explicit={"staging": {"staging"}})
    await bridge.ensure_target(routing, "staging")
    assert bridge.toolset.names == []  # first spawn failed
    assert bridge.pending_prefixes == {"staging"}  # stays pending -> not poisoned
    assert bridge.toolset.errors and bridge.toolset.errors[0][0] == "staging"  # recorded like a startup fail
    await bridge.ensure_target(routing, "staging")  # first use retries
    assert bridge.toolset.names == ["staging__w"]
    assert counts == {"staging": 2}


async def test_owns_all_target_warms_every_pending_server() -> None:
    # An owns-all target owns the whole toolset, so its first use spawns every still-pending server.
    bridge, counts = _bridge({"a": ["ta"], "b": ["tb"]})
    routing = tools.TargetRouting(explicit={"scoped": {"a"}}, owns_all={"catchall"})
    await bridge.ensure_target(routing, "catchall")
    assert sorted(bridge.toolset.names) == ["a__ta", "b__tb"]
    assert counts == {"a": 1, "b": 1}


async def test_freeplay_routing_spawns_nothing() -> None:
    bridge, counts = _bridge({"a": ["ta"]})
    await bridge.ensure_target(tools.TargetRouting(), "anything")
    assert counts == {}
    assert bridge.pending_prefixes == {"a"}


# --- connect_bridge: end-to-end split (stubbed spawn) ---------------------------------------------


async def test_connect_bridge_defers_nonprimary_target(monkeypatch: Any) -> None:
    spawned: list[str] = []

    async def fake_spawn_into(stack: Any, toolset: tools.MCPToolset, prefix: str, server: McpServer) -> bool:
        spawned.append(prefix)
        await toolset.add(_FakeClient([f"{prefix}_tool"]), prefix)  # type: ignore[arg-type]
        return True

    monkeypatch.setattr(tools, "_spawn_into", fake_spawn_into)

    from heim.project import AssistantInfo
    from heim.targets import AssistantRegistry

    resolved = [
        McpServer(name="play42", command="x"),
        McpServer(name="play41", command="x"),
        McpServer(name="datetime", command="x"),
    ]
    registry = AssistantRegistry(
        targets=[
            AssistantInfo(name="play42", mcp_servers=["play42"], readonly=True),
            AssistantInfo(name="play41", mcp_servers=["play41"], readonly=True),
        ]
    )
    routing = tools.build_target_routing(resolved, registry)
    async with tools.connect_bridge(resolved, routing, registry.ids[0]) as bridge:
        # Eager: the primary (play42) + the shared datetime; play41 (non-primary) deferred.
        assert sorted(spawned) == ["datetime", "play42"]
        assert bridge.pending_prefixes == {"play41"}
        assert sorted(bridge.toolset.names) == ["datetime__datetime_tool", "play42__play42_tool"]
        await bridge.ensure_target(routing, "play41")  # first use spawns it
        assert "play41__play41_tool" in bridge.toolset.names
        assert bridge.pending_prefixes == set()


async def test_connect_bridge_eager_all_spawns_everything(monkeypatch: Any) -> None:
    spawned: list[str] = []

    async def fake_spawn_into(stack: Any, toolset: tools.MCPToolset, prefix: str, server: McpServer) -> bool:
        spawned.append(prefix)
        return True

    monkeypatch.setattr(tools, "_spawn_into", fake_spawn_into)

    from heim.project import AssistantInfo
    from heim.targets import AssistantRegistry

    resolved = [McpServer(name="play42", command="x"), McpServer(name="play41", command="x")]
    registry = AssistantRegistry(
        targets=[
            AssistantInfo(name="play42", mcp_servers=["play42"], readonly=True),
            AssistantInfo(name="play41", mcp_servers=["play41"], readonly=True),
        ]
    )
    routing = tools.build_target_routing(resolved, registry)
    async with tools.connect_bridge(resolved, routing, registry.ids[0], eager_all=True) as bridge:
        assert sorted(spawned) == ["play41", "play42"]  # HEIM_EAGER_MCP -> nothing deferred
        assert bridge.pending_prefixes == set()
