"""Headless pilot tests for the Textual chat app."""

import asyncio
from typing import Any

import pytest

from kodo import chat_tui
from kodo.sampling import ModelSampling


def _app() -> chat_tui.ChatApp:
    return chat_tui.ChatApp(
        model_name="pub/Foo-GGUF",
        model_format="gguf",
        model_target="/lib/gguf/pub/Foo-GGUF/model.gguf",
        base="http://127.0.0.1:9",
        servers=[],
        system_prompt="",
        images=[],
        audios=[],
        max_tokens=None,
        ctx_max=1024,
        sampling=ModelSampling(repeat_penalty=1.1),
    )


async def test_enter_sends_and_streams_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run(
        base: str,
        messages: list[dict[str, Any]],
        toolset: Any,
        max_tokens: Any,
        on_event: Any,
        on_token: Any,
        on_reasoning: Any = None,
        on_usage: Any = None,
        **_kw: Any,
    ) -> str:
        for tok in ("Hello", ", ", "world"):
            on_token(tok)
        if on_usage:
            on_usage({"total_tokens": 123})
        messages.append({"role": "assistant", "content": "Hello, world"})
        return "Hello, world"

    monkeypatch.setattr(chat_tui.agent, "run", fake_run)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

    # The user + assistant turns are recorded, and context usage was captured.
    assert app.messages[-2]["role"] == "user"
    assert app.messages[-1] == {"role": "assistant", "content": "Hello, world"}
    assert app.ctx_used == 123


async def test_reasoning_collapses_after_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    from textual.widgets import Collapsible

    async def fake_run(
        base: str,
        messages: list[dict[str, Any]],
        toolset: Any,
        max_tokens: Any,
        on_event: Any,
        on_token: Any,
        on_reasoning: Any = None,
        on_usage: Any = None,
        **_kw: Any,
    ) -> str:
        on_reasoning("let me think")
        on_token("the answer")
        messages.append({"role": "assistant", "content": "the answer"})
        return "the answer"

    monkeypatch.setattr(chat_tui.agent, "run", fake_run)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        box = app.query_one(Collapsible)
        assert box.display is True  # reasoning was shown
        assert box.collapsed is True  # and collapsed once the answer arrived
        assert box.title.startswith("thought for")


async def test_prompts_queue_while_busy_and_run_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[Any] = []
    release = asyncio.Event()
    calls = 0

    async def fake_run(
        base: str,
        messages: list[dict[str, Any]],
        toolset: Any,
        max_tokens: Any,
        on_event: Any,
        on_token: Any,
        on_reasoning: Any = None,
        on_usage: Any = None,
        **_kw: Any,
    ) -> str:
        nonlocal calls
        calls += 1
        prompt = messages[-1]["content"]
        if calls == 1:
            await release.wait()  # hold the first reply open so the 2nd gets queued
        order.append(prompt)
        messages.append({"role": "assistant", "content": "ok"})
        return "ok"

    monkeypatch.setattr(chat_tui.agent, "run", fake_run)
    app = _app()
    async with app.run_test() as pilot:
        app.on_chat_input_submitted(chat_tui.ChatInput.Submitted("first"))
        await pilot.pause()
        assert app._busy is True
        app.on_chat_input_submitted(chat_tui.ChatInput.Submitted("second"))
        await pilot.pause()
        assert app._queue == ["second"]  # held behind the in-flight reply
        release.set()
        for _ in range(100):
            await pilot.pause()
            if not app._busy and not app._queue:
                break
        assert order == ["first", "second"]  # ran in submission order


async def test_trailing_backslash_inserts_newline_instead_of_sending(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    async def fake_run(*args: Any, **kwargs: Any) -> str:
        sent.append("ran")
        args[1].append({"role": "assistant", "content": "ok"})
        return "ok"

    monkeypatch.setattr(chat_tui.agent, "run", fake_run)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("a", "backslash")
        await pilot.press("enter")  # continuation: newline, must NOT send
        await pilot.pause()
        assert sent == []
        assert "\n" in app.query_one(chat_tui.ChatInput).text
