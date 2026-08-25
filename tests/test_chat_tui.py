"""Headless pilot tests for the Textual chat app."""

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from heim import chat_tui, runtime
from heim.library import LibraryModel
from heim.models import ModelFormat
from heim.runtime.sampling import ModelSampling


@pytest.fixture(autouse=True)
def _stub_model_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    # _apply_model reads the model's config/files; stub those so the app builds fast + hermetic.
    monkeypatch.setattr(chat_tui.app.sampling_mod, "recommended", lambda _m: ModelSampling(repeat_penalty=1.1))
    monkeypatch.setattr(
        chat_tui.app.capabilities, "capabilities", lambda _m: SimpleNamespace(context_length=1024, vision=False)
    )


def _fake_runtime(model: LibraryModel, base: str = "http://127.0.0.1:9", port: int = 9) -> runtime.RuntimeProc:
    """A supervised-runtime handle whose process is a mock (nothing actually serves)."""
    rt = runtime.RuntimeProc(
        proc=cast("subprocess.Popen[bytes]", MagicMock()),
        base=base,
        port=port,
        cmd=["llama-server"],
        state_dir=Path("/tmp/heim-fake-runtime"),
        log_fh=None,
    )
    rt.model = model
    return rt


def _app() -> chat_tui.ChatApp:
    model = LibraryModel(
        name="pub/Foo-GGUF",
        model_format=ModelFormat.gguf,
        path=Path("/lib/gguf/pub/Foo-GGUF"),
        load_target=Path("/lib/gguf/pub/Foo-GGUF/model.gguf"),
    )
    rt = _fake_runtime(model)
    return chat_tui.ChatApp(
        endpoint=rt,
        servers=[],
        system_prompt="",
        images=[],
        audios=[],
        max_tokens=None,
    )


def _remote_app(**endpoint_kw: Any) -> chat_tui.ChatApp:
    """An app attached to a remote server (no local model unless passed in)."""
    endpoint = chat_tui.RemoteEndpoint(base="http://127.0.0.1:8000", model_name="pub/Served-GGUF", **endpoint_kw)
    return chat_tui.ChatApp(
        endpoint=endpoint,
        servers=[],
        system_prompt="",
        images=[],
        audios=[],
        max_tokens=None,
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

    monkeypatch.setattr(chat_tui.app.agent, "run", fake_run)
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

    monkeypatch.setattr(chat_tui.app.agent, "run", fake_run)
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


async def test_export_thinking_includes_reasoning_only_when_asked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # /export stays lean by default; /export --thinking folds each turn's reasoning into the file
    # (the thinking is kept out of self.messages, so export pulls it from the per-turn store).
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
        on_reasoning("deciding to use browser_find")
        on_token("done")
        messages.append({"role": "assistant", "content": "done"})
        return "done"

    monkeypatch.setattr(chat_tui.app.agent, "run", fake_run)
    app = _app()
    plain = tmp_path / "plain.md"
    withthink = tmp_path / "think.md"
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.action_export(str(plain))
        app.action_export(str(withthink), thinking=True)

    plain_text = plain.read_text(encoding="utf-8")
    think_text = withthink.read_text(encoding="utf-8")
    assert "deciding to use browser_find" not in plain_text  # default export omits thinking
    assert "deciding to use browser_find" in think_text  # --thinking includes it
    assert "<details>" in think_text and "Thinking" in think_text  # folded, not inline
    assert "done" in think_text  # the answer is still there


async def test_clear_purges_stored_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    # Reasoning is stored keyed by id() of the assistant message; /clear frees those dicts, so it
    # must purge the store — else a later turn reusing a freed address could fold a cleared
    # conversation's thinking into /export --thinking.
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
        on_reasoning("private thinking")
        on_token("answer")
        messages.append({"role": "assistant", "content": "answer"})
        return "answer"

    monkeypatch.setattr(chat_tui.app.agent, "run", fake_run)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app._reasonings  # the turn's reasoning was stored
        app.action_clear()
        assert app._reasonings == {}  # and dropped on clear


async def test_thinking_collapse_preference_is_sticky(monkeypatch: pytest.MonkeyPatch) -> None:
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
        on_reasoning("thinking")
        on_token("answer")
        messages.append({"role": "assistant", "content": "answer"})
        return "answer"

    monkeypatch.setattr(chat_tui.app.agent, "run", fake_run)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        # The auto-collapse after the answer must NOT flip the preference.
        assert app._reason_collapsed_pref is False
        box1 = app.query_one(Collapsible)

        # Simulate the user expanding then collapsing the thinking block.
        box1.collapsed = False
        await pilot.pause()
        assert app._reason_collapsed_pref is False
        box1.collapsed = True
        await pilot.pause()
        assert app._reason_collapsed_pref is True  # the user's collapse stuck

        # Next turn: the new thinking block starts collapsed by that preference.
        await pilot.press("y", "o")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert list(app.query(Collapsible))[-1].collapsed is True


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

    monkeypatch.setattr(chat_tui.app.agent, "run", fake_run)
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

    monkeypatch.setattr(chat_tui.app.agent, "run", fake_run)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("a", "backslash")
        await pilot.press("enter")  # continuation: newline, must NOT send
        await pilot.pause()
        assert sent == []
        assert "\n" in app.query_one(chat_tui.ChatInput).text


async def test_model_switch_swaps_the_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    model_b = LibraryModel(
        name="pub/Bar-GGUF",
        model_format=ModelFormat.gguf,
        path=Path("/lib/gguf/pub/Bar-GGUF"),
        load_target=Path("/lib/gguf/pub/Bar-GGUF/model.gguf"),
    )
    app = _app()
    model_a = app._model  # the starting model (pub/Foo-GGUF)
    # Both models are "in the library"; the runtime is faked so nothing actually serves.
    monkeypatch.setattr(chat_tui.app.library_ops, "scan", lambda: [model_a, model_b])
    stopped: list[Any] = []
    monkeypatch.setattr(chat_tui.app.runtime_mod, "stop", lambda rt: stopped.append(rt.model.name))
    monkeypatch.setattr(chat_tui.app.runtime_mod, "wait_ready", lambda rt, timeout=None: None)
    monkeypatch.setattr(
        chat_tui.app.runtime_mod,
        "start",
        lambda m: _fake_runtime(m, base="http://127.0.0.1:5555", port=5555),
    )

    async with app.run_test() as pilot:
        app.action_switch_model("Bar-GGUF")  # by bare repo tail
        for _ in range(200):
            await pilot.pause()
            if not app._switching:
                break

    assert app._model is not None and app._model.name == "pub/Bar-GGUF"  # rebound to the new model
    assert app._base == "http://127.0.0.1:5555"  # and its new runtime URL
    assert app._model_name == "pub/Bar-GGUF"  # derived fields refreshed
    assert stopped == ["pub/Foo-GGUF"]  # the old runtime was torn down


async def test_remote_attach_chats_against_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def fake_run(
        base: str,
        messages: list[dict[str, Any]],
        toolset: Any,
        max_tokens: Any,
        on_event: Any,
        on_token: Any,
        **kw: Any,
    ) -> str:
        seen["base"] = base
        seen["model"] = kw.get("model")
        messages.append({"role": "assistant", "content": "ok"})
        return "ok"

    monkeypatch.setattr(chat_tui.app.agent, "run", fake_run)
    app = _remote_app(n_ctx=4096)
    assert app._model_format == "remote"  # no local copy: server-reported fields only
    assert app._ctx_max == 4096  # the window the server loaded
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert seen["base"] == "http://127.0.0.1:8000"  # generation hits the attached server
    assert seen["model"] is None  # no local load_target; the field is omitted


async def test_remote_attach_switches_by_repointing_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # A remote attach never touches local runtimes: switching is just changing the OpenAI
    # ``model`` field (a router-mode server hot-swaps on the next request).
    from textual.widgets import Static

    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("a remote attach must never touch local runtimes")

    monkeypatch.setattr(chat_tui.app.runtime_mod, "stop", _boom)
    monkeypatch.setattr(chat_tui.app.runtime_mod, "start", _boom)
    listing = [("pub/Served-GGUF", False), ("qwen-router-alias", True)]
    monkeypatch.setattr(chat_tui.ChatApp, "_fetch_remote_models", lambda self: listing)
    app = _remote_app()
    async with app.run_test() as pilot:
        app.action_switch_model("qwen-router-alias")
        await pilot.pause()
        assert app._model_name == "qwen-router-alias"
        assert app._model_target == "qwen-router-alias"  # the OpenAI model field follows the switch
        app.action_switch_model("not-served")
        await pilot.pause()
        assert app._model_name == "qwen-router-alias"  # unknown name: no change, just a note
        refused = [w for w in app.query(Static) if "does not serve" in str(w.render())]
        assert len(refused) == 1
        # /model opens the arrow-key picker over the remote's ids; a selection switches.
        from heim.chat_tui._widgets import ModelPickerModal

        app.action_show_models()
        for _ in range(20):
            if isinstance(app.screen, ModelPickerModal):
                break
            await pilot.pause()
        assert isinstance(app.screen, ModelPickerModal)
        app.screen.dismiss("pub/Served-GGUF")  # what an arrow-key + Enter selection resolves to
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app._model_name == "pub/Served-GGUF"  # the picked model became the session's


def test_run_interactive_stops_owned_runtime_only(monkeypatch: pytest.MonkeyPatch) -> None:
    stopped: list[Any] = []
    monkeypatch.setattr(chat_tui.app.runtime_mod, "stop", lambda rt: stopped.append(rt))
    monkeypatch.setattr(chat_tui.ChatApp, "run", lambda self: None)  # headless: skip the event loop

    model = LibraryModel(
        name="pub/Foo-GGUF",
        model_format=ModelFormat.gguf,
        path=Path("/lib/gguf/pub/Foo-GGUF"),
        load_target=Path("/lib/gguf/pub/Foo-GGUF/model.gguf"),
    )
    rt = _fake_runtime(model)
    chat_tui.run_interactive(endpoint=rt, servers=[], system_prompt="", images=[], audios=[], max_tokens=None)
    assert stopped == [rt]  # an owned runtime is torn down on exit

    stopped.clear()
    remote = chat_tui.RemoteEndpoint(base="http://127.0.0.1:8000", model_name="pub/Served-GGUF")
    chat_tui.run_interactive(endpoint=remote, servers=[], system_prompt="", images=[], audios=[], max_tokens=None)
    assert stopped == []  # a remote server is left running


async def test_ctrl_y_copies_last_reply(monkeypatch: pytest.MonkeyPatch) -> None:
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
        on_token("Copy me")
        messages.append({"role": "assistant", "content": "Copy me"})
        return "Copy me"

    monkeypatch.setattr(chat_tui.app.agent, "run", fake_run)
    app = _app()
    copied: list[str] = []
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        # Capture what the copy binding sends to the clipboard, then trigger it (works even
        # with the input focused, since the binding is priority).
        monkeypatch.setattr(app, "copy_to_clipboard", lambda t: copied.append(t))
        await pilot.press("ctrl+y")
        await pilot.pause()
    assert copied == ["Copy me"]


async def test_system_prompt_override_keeps_history() -> None:
    # /system replaces (or inserts) messages[0] without touching the conversation; /system
    # clear drops it. The raw text keeps its case and spacing.
    app = _remote_app()  # started with no system prompt -> first /system inserts
    async with app.run_test():
        app.messages.append({"role": "user", "content": "hi"})
        app.messages.append({"role": "assistant", "content": "hello"})
        app._run_command("/system Be Terse. Always.")
        assert app.messages[0] == {"role": "system", "content": "Be Terse. Always."}
        assert len(app.messages) == 3  # history kept
        app._run_command("/system Be verbose.")
        assert app.messages[0]["content"] == "Be verbose."
        assert sum(1 for m in app.messages if m.get("role") == "system") == 1  # replaced, not stacked
        app._run_command("/system clear")
        assert all(m.get("role") != "system" for m in app.messages)
        assert len(app.messages) == 2  # only the system message went
