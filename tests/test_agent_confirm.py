"""Tests for the per-action confirmation gate in ``agent.run``.

The gate is purely ``confirm_policy`` + ``toolset.is_readonly`` (no tool-name knowledge): ``"none"``
runs every tool, ``"writes"`` confirms tools not known read-only, ``"all"`` confirms every call.
A declined call is not executed; the model instead gets a ``tool`` turn whose text is exactly
``error: user declined this action`` (a shared contract with the backend/UI) and the loop continues.
"""

from typing import Any

import pytest

from stabbur import agent, tools


class _StubToolset:
    """A toolset whose one tool records its executions; read-only-ness is set per instance."""

    schemas: list[dict[str, Any]] = []

    def __init__(self, readonly: set[str]) -> None:
        self._readonly = readonly
        self.calls: list[str] = []

    def is_readonly(self, name: str) -> bool:
        return name in self._readonly

    async def call(self, name: str, args: dict[str, Any], timeout: float | None = None) -> tools.ToolResult:
        self.calls.append(name)
        return tools.ToolResult(text="ran")


def _one_tool_then_done(name: str) -> Any:
    """A staged ``_stream_turn``: round 1 calls ``name``, round 2 answers with plain text."""
    rounds = iter([("", [{"id": "1", "name": name, "args": "{}"}], None, "tool_calls"), ("done", [], None, "stop")])

    async def staged(
        http: Any, base_url: str, body: Any, on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[Any], dict[str, Any] | None, str | None]:
        return next(rounds)

    return staged


def _approver(value: bool) -> Any:
    """An ``on_confirm`` that records its calls and returns a fixed decision."""
    seen: list[tuple[str, dict[str, Any]]] = []

    async def on_confirm(name: str, args: dict[str, Any]) -> bool:
        seen.append((name, args))
        return value

    on_confirm.seen = seen  # type: ignore[attr-defined]
    return on_confirm


async def test_policy_none_never_confirms_and_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default policy: on_confirm is never consulted and the tool runs.
    monkeypatch.setattr(agent, "_stream_turn", _one_tool_then_done("srv__write"))
    on_confirm = _approver(False)  # would deny if consulted
    toolset = _StubToolset(readonly=set())
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    out = await agent.run("http://runtime", messages, toolset, on_confirm=on_confirm, confirm_policy="none")  # type: ignore[arg-type]

    assert out == "done"
    assert toolset.calls == ["srv__write"]  # executed
    assert on_confirm.seen == []  # never asked


async def test_policy_writes_readonly_tool_not_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    # A read-only tool under "writes" is not gated: it executes without consulting on_confirm.
    monkeypatch.setattr(agent, "_stream_turn", _one_tool_then_done("srv__read"))
    on_confirm = _approver(False)
    toolset = _StubToolset(readonly={"srv__read"})
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    out = await agent.run("http://runtime", messages, toolset, on_confirm=on_confirm, confirm_policy="writes")  # type: ignore[arg-type]

    assert out == "done"
    assert toolset.calls == ["srv__read"]
    assert on_confirm.seen == []


async def test_policy_writes_approved_write_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    # A non-read-only tool under "writes" is gated; approval lets it run.
    monkeypatch.setattr(agent, "_stream_turn", _one_tool_then_done("srv__write"))
    on_confirm = _approver(True)
    toolset = _StubToolset(readonly=set())
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    out = await agent.run("http://runtime", messages, toolset, on_confirm=on_confirm, confirm_policy="writes")  # type: ignore[arg-type]

    assert out == "done"
    assert toolset.calls == ["srv__write"]  # executed after approval
    assert [n for n, _ in on_confirm.seen] == ["srv__write"]  # was asked


async def test_policy_writes_declined_write_not_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    # Decline: the tool does NOT run, the model gets the exact declined-contract text, loop continues.
    monkeypatch.setattr(agent, "_stream_turn", _one_tool_then_done("srv__write"))
    on_confirm = _approver(False)
    toolset = _StubToolset(readonly=set())
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    out = await agent.run("http://runtime", messages, toolset, on_confirm=on_confirm, confirm_policy="writes")  # type: ignore[arg-type]

    assert out == "done"  # loop continued to a final answer
    assert toolset.calls == []  # never executed
    tool_msg = next(m for m in messages if m.get("role") == "tool")
    assert tool_msg["content"] == "error: user declined this action"  # exact shared contract


async def test_policy_writes_no_confirm_sink_denies(monkeypatch: pytest.MonkeyPatch) -> None:
    # No confirmation channel is a denial (fail-safe): a gated write is refused, not silently run.
    monkeypatch.setattr(agent, "_stream_turn", _one_tool_then_done("srv__write"))
    toolset = _StubToolset(readonly=set())
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    out = await agent.run("http://runtime", messages, toolset, on_confirm=None, confirm_policy="writes")  # type: ignore[arg-type]

    assert out == "done"
    assert toolset.calls == []
    tool_msg = next(m for m in messages if m.get("role") == "tool")
    assert tool_msg["content"] == "error: user declined this action"


async def test_policy_all_gates_even_readonly(monkeypatch: pytest.MonkeyPatch) -> None:
    # "all" confirms every call, including a read-only tool; a decline still blocks it.
    monkeypatch.setattr(agent, "_stream_turn", _one_tool_then_done("srv__read"))
    on_confirm = _approver(False)
    toolset = _StubToolset(readonly={"srv__read"})
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    out = await agent.run("http://runtime", messages, toolset, on_confirm=on_confirm, confirm_policy="all")  # type: ignore[arg-type]

    assert out == "done"
    assert toolset.calls == []  # gated despite being read-only
    assert [n for n, _ in on_confirm.seen] == ["srv__read"]  # was asked
