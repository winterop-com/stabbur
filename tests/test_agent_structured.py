"""Structured output: what the agent loop puts on the wire for ``response_format``.

The route-level test proves the value reaches :func:`stabbur.agent.run`; this proves ``run``
puts it in the request body, which is the part a caller actually depends on.

Measured against llama-server rather than assumed, because the behaviour is not uniform:
``{"type": "json_schema", ...}`` is enforced and produces schema-conforming JSON, while
``{"type": "json_object"}`` is silently IGNORED and answers in prose. Combining either with
tools is rejected upstream with 400 "failed to parse grammar" — a message naming neither
feature — which is why the route refuses that pair before it gets there.
"""

from typing import Any

import pytest

from stabbur import agent


class _NoTools:
    """A toolset with nothing in it, so the loop makes exactly one round."""

    schemas: list[dict[str, Any]] = []


def _capture(bodies: list[dict[str, Any]]) -> Any:
    """A ``_stream_turn`` that records each request body and answers with plain text."""

    async def staged(
        http: Any, base_url: str, body: dict[str, Any], on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[Any], dict[str, Any] | None, str | None]:
        bodies.append(body)
        return ("done", [], None, "stop")

    return staged


async def test_response_format_is_sent_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict[str, Any]] = []
    monkeypatch.setattr(agent, "_stream_turn", _capture(bodies))
    schema = {
        "type": "json_schema",
        "json_schema": {"name": "s", "strict": True, "schema": {"type": "object"}},
    }
    await agent.run("http://runtime", [{"role": "user", "content": "hi"}], _NoTools(), response_format=schema)  # type: ignore[arg-type]

    # Verbatim: the runtime owns the dialect, so stabbur must not normalise or re-wrap it.
    assert bodies[-1]["response_format"] == schema


async def test_response_format_is_absent_unless_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    # An always-present ``response_format: null`` would be a behaviour change for every existing
    # caller, and some runtimes reject an unexpected null.
    bodies: list[dict[str, Any]] = []
    monkeypatch.setattr(agent, "_stream_turn", _capture(bodies))
    await agent.run("http://runtime", [{"role": "user", "content": "hi"}], _NoTools())  # type: ignore[arg-type]

    assert "response_format" not in bodies[-1]
