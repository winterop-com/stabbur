"""Tests for the readOnlyHint plumbing on ``MCPToolset`` (the confirmation-gate metadata).

Each MCP tool may expose ``tool.annotations.readOnlyHint``; the toolset records it per
qualified name so the agent loop can decide whether a call needs confirmation. The default is
fail-safe: an unknown tool, or one with no annotation, is treated as NOT read-only.
"""

from typing import Any

from stabbur import tools


class _FakeTool:
    """A stand-in for an MCP tool as returned by ``client.list_tools()``."""

    def __init__(self, name: str, annotations: Any) -> None:
        self.name = name
        self.description = f"{name} tool"
        self.inputSchema: dict[str, Any] = {"type": "object", "properties": {}}
        self.annotations = annotations


class _Annotations:
    """A ``ToolAnnotations``-shaped object carrying only the ``readOnlyHint`` we care about."""

    def __init__(self, read_only_hint: bool | None) -> None:
        self.readOnlyHint = read_only_hint


class _FakeClient:
    """A stub MCP client whose ``list_tools()`` returns a fixed set of ``_FakeTool``s."""

    def __init__(self, tools_: list[_FakeTool]) -> None:
        self._tools = tools_

    async def list_tools(self) -> list[_FakeTool]:
        return self._tools


async def _build() -> tools.MCPToolset:
    """A toolset over one server with four tools spanning the annotation cases."""
    client = _FakeClient(
        [
            _FakeTool("readonly", _Annotations(True)),
            _FakeTool("write", _Annotations(False)),
            _FakeTool("hint_none", _Annotations(None)),
            _FakeTool("no_annotations", None),
        ]
    )
    toolset = tools.MCPToolset()
    await toolset.add(client, "srv")  # type: ignore[arg-type]
    return toolset


async def test_is_readonly_reflects_the_annotation() -> None:
    # Only an explicit readOnlyHint == True is read-only; False/None/missing annotations are not
    # (fail-safe: unknown -> treat as a write that needs confirmation).
    toolset = await _build()
    assert toolset.is_readonly("srv__readonly") is True
    assert toolset.is_readonly("srv__write") is False
    assert toolset.is_readonly("srv__hint_none") is False
    assert toolset.is_readonly("srv__no_annotations") is False


async def test_is_readonly_unknown_tool_is_false() -> None:
    # An unrecorded name defaults to False (needs confirmation), never a KeyError.
    toolset = await _build()
    assert toolset.is_readonly("srv__does_not_exist") is False
    assert tools.MCPToolset().is_readonly("anything") is False


async def test_subset_preserves_readonly_for_kept_names() -> None:
    # A narrowed view keeps the confirmation-gate metadata for the tools it keeps and drops the rest.
    toolset = await _build()
    view = toolset.subset({"srv__readonly", "srv__write"})
    assert view.is_readonly("srv__readonly") is True
    assert view.is_readonly("srv__write") is False
    # A dropped tool is no longer recorded -> fail-safe False, and absent from the map.
    assert "srv__hint_none" not in view._readonly
    assert view.is_readonly("srv__hint_none") is False


async def _build_two_servers() -> tools.MCPToolset:
    """A toolset over two servers ('a' and 'b'), each with a readonly + a write tool."""
    toolset = tools.MCPToolset()
    for prefix in ("a", "b"):
        client = _FakeClient([_FakeTool("read", _Annotations(True)), _FakeTool("write", _Annotations(False))])
        await toolset.add(client, prefix)  # type: ignore[arg-type]
    return toolset


async def test_prefixes_lists_server_namespaces() -> None:
    # prefixes() reports the <prefix> of each <prefix>__<tool>, so a caller never re-parses "__".
    toolset = await _build_two_servers()
    assert toolset.prefixes() == {"a", "b"}
    assert tools.MCPToolset().prefixes() == set()


async def test_names_for_prefixes_selects_qualified_names() -> None:
    # names_for_prefixes() returns a ready subset() argument: every tool under the given prefixes.
    toolset = await _build_two_servers()
    assert toolset.names_for_prefixes({"a"}) == {"a__read", "a__write"}
    assert toolset.names_for_prefixes({"a", "b"}) == {"a__read", "a__write", "b__read", "b__write"}
    assert toolset.names_for_prefixes({"missing"}) == set()


async def _build_three_servers() -> tools.MCPToolset:
    """A toolset over two target-owned servers ('a', 'b') plus a shared one ('shared')."""
    toolset = await _build_two_servers()
    client = _FakeClient([_FakeTool("read", _Annotations(True)), _FakeTool("write", _Annotations(False))])
    await toolset.add(client, "shared")  # type: ignore[arg-type]
    return toolset


async def test_narrow_to_servers_keeps_shared_and_preserves_readonly() -> None:
    # narrow_to_servers routes through subset(): the resolved target's server + the unowned 'shared'
    # server are kept (the other target hidden), and _readonly survives for every kept tool.
    toolset = await _build_three_servers()
    routing = tools.TargetRouting(explicit={"t1": {"a"}, "t2": {"b"}})
    view = tools.narrow_to_servers(toolset, routing, "t1")
    assert view.prefixes() == {"a", "shared"}  # 'b' owned by t2 → hidden; 'shared' owned by none → kept
    assert view.is_readonly("a__read") is True
    assert view.is_readonly("a__write") is False
    assert view.is_readonly("shared__read") is True
    assert view.is_readonly("b__read") is False  # dropped → fail-safe False


async def test_narrow_to_servers_hides_other_targets() -> None:
    # A server owned exclusively by another target is hidden from the resolved target's view.
    toolset = await _build_two_servers()
    routing = tools.TargetRouting(explicit={"t1": {"a"}, "t2": {"b"}})
    view = tools.narrow_to_servers(toolset, routing, "t1")
    assert view.prefixes() == {"a"}  # 'b' belongs to t2, not shared
    assert view.is_readonly("a__read") is True
    assert view.is_readonly("b__read") is False  # dropped -> fail-safe False


async def test_narrow_to_servers_freeplay_and_owns_all_are_noops() -> None:
    # An empty routing (free-play) and an owns-all target both keep the full toolset untouched
    # (identity): a tool is only hidden when it is owned *exclusively by another* explicit target.
    toolset = await _build_two_servers()
    assert tools.narrow_to_servers(toolset, tools.TargetRouting(), "anything") is toolset
    assert tools.narrow_to_servers(toolset, tools.TargetRouting(owns_all={"only"}), "only") is toolset


async def test_narrow_to_servers_single_scoped_target_gets_everything() -> None:
    # A lone scoped target: its explicit set is the whole claimed union, so every other server is shared
    # -> it resolves to the full toolset (the single-[assistant]-with-mcp_servers case). No tool is hidden.
    toolset = await _build_two_servers()
    routing = tools.TargetRouting(explicit={"only": {"a"}})
    view = tools.narrow_to_servers(toolset, routing, "only")
    assert view.prefixes() == {"a", "b"}  # 'b' is unclaimed by any *other* target -> shared -> kept
