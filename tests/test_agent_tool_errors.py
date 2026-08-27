"""Tests for what the agent loop tells the model when a tool call raises.

A tool failure is fed back as a ``tool`` turn so the model can react to it, which only works if
the text says something. A whole class of exceptions carries no message at all — a bare
``TimeoutError``, anyio's closed-stream errors — and ``f"error: {exc}"`` rendered those as a bare
``error: ``, leaving the model to retry blind against a failure it had been told nothing about.
"""

import asyncio
from typing import Any

import pytest

from stabbur import agent, tools


class _ClosedResource(Exception):
    """Stands in for the message-less stream errors an MCP transport raises (anyio's, httpx's)."""


class _RaisingToolset:
    """A toolset whose single tool always raises the exception it was built with."""

    schemas: list[dict[str, Any]] = []

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def is_readonly(self, name: str) -> bool:
        return True

    async def call(self, name: str, args: dict[str, Any], timeout: float | None = None) -> tools.ToolResult:
        raise self._exc


def _one_tool_then_done(name: str) -> Any:
    """A staged ``_stream_turn``: round 1 calls ``name``, round 2 answers with plain text."""
    rounds = iter([("", [{"id": "1", "name": name, "args": "{}"}], None, "tool_calls"), ("done", [], None, "stop")])

    async def staged(
        http: Any, base_url: str, body: Any, on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[Any], dict[str, Any] | None, str | None]:
        return next(rounds)

    return staged


async def _tool_message_for(exc: BaseException, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(agent, "_stream_turn", _one_tool_then_done("srv__t"))
    messages: list[dict[str, Any]] = [{"role": "user", "content": "hi"}]
    out = await agent.run("http://runtime", messages, _RaisingToolset(exc), confirm_policy="none")  # type: ignore[arg-type]
    assert out == "done"  # a failed tool does not end the loop
    return str(next(m for m in messages if m.get("role") == "tool")["content"])


def test_exc_text_never_returns_an_empty_description() -> None:
    assert agent._exc_text(RuntimeError("boom")) == "RuntimeError: boom"
    assert agent._exc_text(asyncio.CancelledError()) == "CancelledError"
    assert agent._exc_text(TimeoutError()) == "TimeoutError"
    assert agent._exc_text(ValueError("   ")) == "ValueError"  # whitespace is not a message either


async def test_a_message_less_failure_still_names_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    content = await _tool_message_for(_ClosedResource(), monkeypatch)
    assert content == "error: _ClosedResource"
    assert content.strip() != "error:"


async def test_a_bare_timeout_still_names_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _tool_message_for(TimeoutError(), monkeypatch) == "error: TimeoutError"


async def test_a_failure_with_a_message_keeps_it(monkeypatch: pytest.MonkeyPatch) -> None:
    content = await _tool_message_for(RuntimeError("the server closed the connection"), monkeypatch)
    assert content == "error: RuntimeError: the server closed the connection"
