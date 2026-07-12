"""Tests for the readOnlyHint plumbing on ``MCPToolset`` (the confirmation-gate metadata).

Each MCP tool may expose ``tool.annotations.readOnlyHint``; the toolset records it per
qualified name so the agent loop can decide whether a call needs confirmation. The default is
fail-safe: an unknown tool, or one with no annotation, is treated as NOT read-only.
"""

from typing import Any

from kodo import tools


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
