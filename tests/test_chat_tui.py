"""Headless pilot tests for the Textual chat app."""

import asyncio
import contextlib
import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from stabbur import chat_tui, runtime
from stabbur.library import LibraryModel
from stabbur.models import ModelFormat
from stabbur.runtime.sampling import ModelSampling


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
        state_dir=Path("/tmp/stabbur-fake-runtime"),
        log_fh=None,
    )
    rt.model = model
    return rt


def _app(*, servers: list[Any] | None = None, max_tokens: int | None = None) -> chat_tui.ChatApp:
    model = LibraryModel(
        name="pub/Foo-GGUF",
        model_format=ModelFormat.gguf,
        path=Path("/lib/gguf/pub/Foo-GGUF"),
        load_target=Path("/lib/gguf/pub/Foo-GGUF/model.gguf"),
    )
    rt = _fake_runtime(model)
    return chat_tui.ChatApp(
        endpoint=rt,
        servers=servers or [],
        system_prompt="",
        images=[],
        audios=[],
        max_tokens=max_tokens,
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
        # Collapsed by default (thinking is a debugging aid); the auto-collapse after the
        # answer must NOT flip the preference.
        assert app._reason_collapsed_pref is True
        box1 = app.query_one(Collapsible)
        assert box1.collapsed is True  # started collapsed

        # Simulate the user expanding the thinking block: the expansion sticks...
        box1.collapsed = False
        await pilot.pause()
        assert app._reason_collapsed_pref is False
        # ...and collapsing it again sticks back.
        box1.collapsed = True
        await pilot.pause()
        assert app._reason_collapsed_pref is True

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
        from stabbur.chat_tui._widgets import ModelPickerModal

        app.action_show_models()
        for _ in range(20):
            if isinstance(app.screen, ModelPickerModal):
                break
            await pilot.pause()
        assert isinstance(app.screen, ModelPickerModal)
        app.screen.dismiss(0)  # row 0 — what an arrow-key + Enter selection resolves to
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


async def test_set_reasoning_levels_and_reset() -> None:
    # /set reasoning off|low|medium|high|max sets the thinking effort for later turns;
    # default/auto/none resets to the model's own behavior; junk is rejected.
    app = _app()
    async with app.run_test():
        assert app._reasoning is None
        app._run_command("/set reasoning off")
        assert app._reasoning == "off"
        app._run_command("/set reasoning HIGH")
        assert app._reasoning == "high"
        app._run_command("/set reasoning bogus")
        assert app._reasoning == "high"  # unknown level: unchanged, just a note
        app._run_command("/set reasoning default")
        assert app._reasoning is None


async def test_model_picker_tolerates_a_name_in_two_formats() -> None:
    # A library legitimately holds one model in several formats (keeping GGUF *and* MLX is the
    # documented default policy), so the picker's rows can repeat a name. Textual raises
    # DuplicateID when two options share an id, which crashed /model on exactly the libraries
    # that policy encourages. Every row must survive, and Enter must answer with the row the
    # user highlighted — the *index*, since the name alone can't tell the two builds apart.
    from textual.widgets import OptionList

    from stabbur.chat_tui._widgets import ModelPickerModal

    rows = [("pub/Foo", "gguf"), ("pub/Foo", "mlx"), ("pub/Bar", "gguf")]
    picked: list[int | None] = []
    app = _app()
    async with app.run_test() as pilot:
        app.push_screen(ModelPickerModal(rows, 0, "models · 3"), picked.append)  # row 0 is running
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ModelPickerModal)
        picker = screen.query_one(OptionList)
        assert picker.option_count == 3  # no row dropped, and compose didn't raise DuplicateID
        assert picker.highlighted == 0  # opens on the running build
        # Only that build wears the "running" dot — marking by name would light up both rows
        # named pub/Foo and leave the user unable to tell which one is loaded.
        marked = [i for i in range(3) if "●" in str(picker.get_option_at_index(i).prompt)]
        assert marked == [0]

        # Arrow down onto the MLX build of the *same* name, then Enter.
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()

    assert picked == [1]  # the second row, not "whichever row is named pub/Foo"


async def test_picking_another_format_of_the_running_model_actually_switches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The picker used to answer with the row's *name*, so choosing the MLX build of a model
    # already running as GGUF read as "you picked what you're on" and did nothing at all — on
    # exactly the libraries stabbur's keep-GGUF-and-MLX policy produces. The row's identity (its
    # index) must survive the round trip, and the two builds run on different runtimes.
    from stabbur.chat_tui._widgets import ModelPickerModal

    app = _app()
    gguf = app._model
    assert gguf is not None
    mlx = LibraryModel(
        name="pub/Foo-GGUF",  # same name, different build
        model_format=ModelFormat.mlx,
        path=Path("/lib/mlx/pub/Foo-GGUF"),
        load_target=Path("/lib/mlx/pub/Foo-GGUF"),
    )
    monkeypatch.setattr(chat_tui.app.library_ops, "scan", lambda: [gguf, mlx])
    monkeypatch.setattr(chat_tui.app.runtime_mod, "stop", lambda rt: None)
    monkeypatch.setattr(chat_tui.app.runtime_mod, "wait_ready", lambda rt, timeout=None: None)
    monkeypatch.setattr(
        chat_tui.app.runtime_mod, "start", lambda m: _fake_runtime(m, base="http://127.0.0.1:5555", port=5555)
    )

    async with app.run_test() as pilot:
        app.action_show_models()
        for _ in range(40):
            await pilot.pause()
            if isinstance(app.screen, ModelPickerModal):
                break
        assert isinstance(app.screen, ModelPickerModal)
        app.screen.dismiss(1)  # the MLX row (arrow-down from the pre-marked GGUF row, then Enter)
        for _ in range(200):
            await pilot.pause()
            if app._model is not None and app._model.model_format is ModelFormat.mlx:
                break

    assert app._model is not None
    assert app._model.model_format is ModelFormat.mlx  # the highlighted build loaded, not the first namesake
    assert app._model_format == "mlx"
    assert app._base == "http://127.0.0.1:5555"


async def test_model_by_name_refuses_an_ambiguous_format(monkeypatch: pytest.MonkeyPatch) -> None:
    # `/model <name>` can't choose between two builds of one name, and guessing would silently
    # start the wrong runtime. It says so; `/model <name> <format>` narrows it.
    from textual.widgets import Static

    app = _app()
    gguf = app._model
    assert gguf is not None
    mlx = LibraryModel(
        name="pub/Foo-GGUF",
        model_format=ModelFormat.mlx,
        path=Path("/lib/mlx/pub/Foo-GGUF"),
        load_target=Path("/lib/mlx/pub/Foo-GGUF"),
    )
    bar = LibraryModel(
        name="pub/Bar-GGUF",
        model_format=ModelFormat.gguf,
        path=Path("/lib/gguf/pub/Bar-GGUF"),
        load_target=Path("/lib/gguf/pub/Bar-GGUF/model.gguf"),
    )
    monkeypatch.setattr(chat_tui.app.library_ops, "scan", lambda: [gguf, mlx, bar])
    started: list[str] = []
    monkeypatch.setattr(chat_tui.app.runtime_mod, "stop", lambda rt: None)
    monkeypatch.setattr(chat_tui.app.runtime_mod, "wait_ready", lambda rt, timeout=None: None)

    def start(model: LibraryModel) -> runtime.RuntimeProc:
        started.append(model.model_format.value)
        return _fake_runtime(model, base="http://127.0.0.1:5555", port=5555)

    monkeypatch.setattr(chat_tui.app.runtime_mod, "start", start)

    async with app.run_test() as pilot:
        app._run_command("/model Foo-GGUF")
        await pilot.pause()
        assert started == []  # no coin-flip load
        notes = [w for w in app.query(Static) if "2 formats" in str(w.render())]
        assert len(notes) == 1 and "gguf, mlx" in str(notes[0].render())

        app._run_command("/model Foo-GGUF mlx")  # narrowed: this one loads
        for _ in range(200):
            await pilot.pause()
            if not app._switching and started:
                break

    assert started == ["mlx"]


async def test_a_failed_switch_reloads_the_model_it_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    # Switching frees the running model before loading the next one, so a load that fails used
    # to leave the session pointed at a port nothing listens on — the footer still named the old
    # model while every later message died on a closed connection. The previous model comes back.
    from textual.widgets import Static

    app = _app()
    model_a = app._model
    assert model_a is not None
    model_b = LibraryModel(
        name="pub/Bar-GGUF",
        model_format=ModelFormat.gguf,
        path=Path("/lib/gguf/pub/Bar-GGUF"),
        load_target=Path("/lib/gguf/pub/Bar-GGUF/model.gguf"),
    )
    monkeypatch.setattr(chat_tui.app.library_ops, "scan", lambda: [model_a, model_b])
    stopped: list[str] = []
    monkeypatch.setattr(chat_tui.app.runtime_mod, "stop", lambda rt: stopped.append(rt.model.name))
    monkeypatch.setattr(chat_tui.app.runtime_mod, "wait_ready", lambda rt, timeout=None: None)

    def start(model: LibraryModel) -> Any:
        if model.name == "pub/Bar-GGUF":
            raise RuntimeError("llama-server: failed to allocate")
        return _fake_runtime(model, base="http://127.0.0.1:7777", port=7777)

    monkeypatch.setattr(chat_tui.app.runtime_mod, "start", start)

    async with app.run_test() as pilot:
        app.action_switch_model("Bar-GGUF")
        for _ in range(200):
            await pilot.pause()
            if not app._switching:
                break

        assert stopped == ["pub/Foo-GGUF"]  # the old runtime was freed to make room
        assert app._model is model_a  # still the model the session was on...
        assert app._base == "http://127.0.0.1:7777"  # ...but behind a live runtime again
        posted = " ".join(str(w.render()) for w in app.query(Static))
        assert "switch failed" in posted and "back on pub/Foo-GGUF" in posted


async def test_a_prompt_typed_during_a_switch_is_not_eaten() -> None:
    # A model load takes tens of seconds; pressing Enter into it used to clear the input and
    # drop the prompt with only a toast. The text stays put so Enter works once the model is up.
    app = _app()
    async with app.run_test() as pilot:
        app._switching = True
        app.query_one(chat_tui.ChatInput).text = "a long, carefully written prompt"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one(chat_tui.ChatInput).text == "a long, carefully written prompt"
        assert app.messages == []  # and nothing was sent to the model being swapped out


async def test_palette_lists_the_remotes_models_on_an_attach(monkeypatch: pytest.MonkeyPatch) -> None:
    # A remote attach can only move between the ids the server serves, so the Ctrl+P palette must
    # offer those — not local library models it would refuse ("does not serve") — and it must not
    # scan the library at all: that blocks the UI on a drive this session never touches.
    from stabbur.chat_tui._widgets import _StabburCommands

    def _boom() -> list[LibraryModel]:
        raise AssertionError("a remote attach must not scan the local library")

    monkeypatch.setattr(chat_tui.app.library_ops, "scan", _boom)
    monkeypatch.setattr(
        chat_tui.ChatApp, "_fetch_remote_models", lambda self: [("served-a", True), ("served-b", False)]
    )
    app = _remote_app()
    app._endpoint.model_name = "served-a"  # type: ignore[union-attr]
    app._apply_model()
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        provider = _StabburCommands(app.screen)
        titles = [title for title, _help, _callback in provider._commands()]

    assert "Switch model: served-b" in titles  # the other model the server holds
    assert "Switch model: served-a" not in titles  # not the one already selected
    assert not any(t.startswith("Switch model: pub/") for t in titles)  # no local library rows


async def test_export_path_expands_home_and_keeps_spaces(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The chat input is not a shell: `~` was taken literally (a `./~` directory, so the write
    # failed), and a path with spaces was truncated at the first word by the command split.
    monkeypatch.setenv("HOME", str(tmp_path))
    app = _app()
    async with app.run_test() as pilot:
        app.messages.append({"role": "user", "content": "hi"})
        app.messages.append({"role": "assistant", "content": "hello"})
        app._run_command("/export ~/My Chat.md")
        await pilot.pause()

    written = tmp_path / "My Chat.md"
    assert written.exists()  # under $HOME, under the full name
    assert "hello" in written.read_text(encoding="utf-8")


# -- responsiveness ------------------------------------------------------------------------


def _status_text(app: chat_tui.ChatApp) -> str:
    """The status footer as plain text (a mounted Static's render() is an opaque visual in 8.x)."""
    from rich.console import Group
    from rich.text import Text

    def plain(renderable: Any) -> str:
        if isinstance(renderable, Group):
            return "  ".join(plain(r) for r in renderable.renderables)
        return renderable.plain if isinstance(renderable, Text) else str(renderable)

    return plain(app._status_renderable())


def _toolset_with(*names: str) -> Any:
    """A toolset carrying ``names`` (enough for the footer's tool count and identity checks)."""
    from stabbur import tools as mcp_tools

    toolset = mcp_tools.MCPToolset()
    for name in names:
        toolset.schemas.append({"function": {"name": name, "description": ""}})
    return toolset


async def test_startup_stays_responsive_while_mcp_connects(monkeypatch: pytest.MonkeyPatch) -> None:
    # Awaited on the message pump, a slow MCP connect froze paint, keys and quit for as long as it
    # took (up to the 60s init timeout, per server). It runs in a worker now: the UI takes input
    # immediately and the tools land in the footer when they arrive.
    from contextlib import asynccontextmanager

    release = asyncio.Event()
    connected = _toolset_with("slow__ping")

    @asynccontextmanager
    async def slow_connect(_servers: Any) -> AsyncGenerator[Any, None]:
        # Bounded: awaited on the message pump (the defect) this would otherwise deadlock the test
        # rather than failing it — the release only comes from the body, which never gets to run.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(release.wait(), 5)
        yield connected

    monkeypatch.setattr(chat_tui.app.mcp_tools, "connect", slow_connect)
    app = _app(servers=[("slow", ["stabbur-mcp-slow"], {})])
    async with app.run_test() as pilot:
        # The connect is still hanging...
        await pilot.press("h", "i")
        assert app.query_one(chat_tui.ChatInput).text == "hi"  # ...and the UI is fully alive
        assert app.toolset is not connected
        assert "tool" not in _status_text(app)

        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.toolset is connected  # tools appear once connected...
        assert "1 tool" in _status_text(app)  # ...and the footer says so


async def test_a_failed_mcp_connect_is_reported_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import asynccontextmanager

    from textual.widgets import Static

    @asynccontextmanager
    async def boom(_servers: Any) -> AsyncGenerator[Any, None]:
        raise RuntimeError("no such command")
        yield  # pragma: no cover - unreachable; keeps this an async generator

    monkeypatch.setattr(chat_tui.app.mcp_tools, "connect", boom)
    app = _app(servers=[("broken", ["nope"], {})])
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        posted = " ".join(str(w.render()) for w in app.query(Static))
        assert "MCP connect failed" in posted and "no such command" in posted
        await pilot.press("h", "i")  # the session keeps going, just without tools
        assert app.query_one(chat_tui.ChatInput).text == "hi"


async def test_per_server_mcp_failures_are_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    # connect() records a bad server instead of raising; unsurfaced, its missing tools just read as
    # "the model ignored them".
    from contextlib import asynccontextmanager

    from textual.widgets import Static

    toolset = _toolset_with("ok__ping")
    toolset.errors.append(("broken", "executable not found"))

    @asynccontextmanager
    async def partial(_servers: Any) -> AsyncGenerator[Any, None]:
        yield toolset

    monkeypatch.setattr(chat_tui.app.mcp_tools, "connect", partial)
    app = _app(servers=[("ok", ["a"], {}), ("broken", ["b"], {})])
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        posted = " ".join(str(w.render()) for w in app.query(Static))
    assert "broken" in posted and "executable not found" in posted


async def test_remote_model_by_name_does_not_block_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    # `/model <name>` on a remote attach listed the server's models with a blocking httpx.get (5s
    # timeout) straight on the event loop. It runs in a thread now: the UI keeps taking keys while
    # the listing is in flight, and the switch lands when it answers.
    import threading

    gate = threading.Event()

    def slow_fetch(_self: Any) -> list[tuple[str, bool]]:
        gate.wait(10)  # bounded, so a regression fails the test instead of hanging the suite
        return [("served-a", True), ("served-b", False)]

    monkeypatch.setattr(chat_tui.ChatApp, "_fetch_remote_models", slow_fetch)
    app = _remote_app()
    async with app.run_test() as pilot:
        app._remote_models_cache = None  # cold cache: the switch has to go and ask
        app.action_switch_model("served-b")
        await pilot.pause()
        # On the event loop this press could not be delivered until the fetch returned.
        await pilot.press("x")
        assert app.query_one(chat_tui.ChatInput).text == "x"
        assert app._model_name == "pub/Served-GGUF"  # not switched yet

        gate.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app._model_name == "served-b"


async def test_the_palette_never_scans_the_library(monkeypatch: pytest.MonkeyPatch) -> None:
    # Building the palette's entries used to call the synchronous library scan on the event loop —
    # seconds of frozen UI on an external drive, and twice per open (discover + search).
    from stabbur.chat_tui._widgets import _StabburCommands

    # Counted, not raised: _scan_switchable swallows every exception, so an assert inside the scan
    # would be eaten and the test would pass against the very defect it is here to catch.
    scans: list[int] = []

    def counted_scan() -> list[LibraryModel]:
        scans.append(1)
        return []

    monkeypatch.setattr(chat_tui.app.library_ops, "scan", counted_scan)
    app = _app()
    async with app.run_test() as pilot:
        app._models_cache = None  # cold: nothing primed yet
        scans.clear()  # ignore the mount worker's own (off-thread) priming scan
        titles = [t for t, _h, _c in _StabburCommands(app.screen)._commands()]
        assert scans == []  # the palette answered without touching the filesystem
        assert titles and not any(t.startswith("Switch model:") for t in titles)  # listed, never scanned

        # Once the (worker-primed) cache is warm, the rows are there.
        other = LibraryModel(
            name="pub/Bar-GGUF",
            model_format=ModelFormat.gguf,
            path=Path("/lib/gguf/pub/Bar-GGUF"),
            load_target=Path("/lib/gguf/pub/Bar-GGUF/model.gguf"),
        )
        app._models_cache = [other]
        titles = [t for t, _h, _c in _StabburCommands(app.screen)._commands()]
        assert "Switch model: pub/Bar-GGUF (gguf)" in titles
        await pilot.pause()


async def test_the_palette_builds_its_commands_once_per_opening(monkeypatch: pytest.MonkeyPatch) -> None:
    # Textual calls discover() and then search() on the same provider; each rebuilt the whole list,
    # and each rebuild raced the model cache.
    from stabbur.chat_tui._widgets import _StabburCommands

    app = _app()
    async with app.run_test():
        builds: list[int] = []

        def counted_models() -> list[LibraryModel]:
            builds.append(1)
            return []

        monkeypatch.setattr(app, "_cached_switchable_models", counted_models)
        provider = _StabburCommands(app.screen)
        [hit async for hit in provider.discover()]
        [hit async for hit in provider.search("model")]
        assert len(builds) == 1


# -- correctness ---------------------------------------------------------------------------


async def test_a_failed_turn_marks_the_line_and_gives_the_text_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # A failed turn is dropped from the history, but its line stayed on screen unmarked — the
    # transcript showed a question the model never received, with no way to retry it.
    from textual.widgets import Static

    async def fake_run(*_a: Any, **_kw: Any) -> str:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(chat_tui.app.agent, "run", fake_run)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        posted = " ".join(str(w.render()) for w in app.query(Static))
        assert "error: connection refused" in posted
        assert "(not sent)" in posted  # the user line says it never reached the model
        assert app.messages == []  # and the history agrees
        assert app.query_one(chat_tui.ChatInput).text == "hi"  # retry is one keypress


async def test_a_failed_turn_does_not_overwrite_newly_typed_text(monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def fake_run(*_a: Any, **_kw: Any) -> str:
        started.set()
        await release.wait()
        raise RuntimeError("boom")

    monkeypatch.setattr(chat_tui.app.agent, "run", fake_run)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await started.wait()
        await pilot.press("n", "e", "x", "t")  # typed while the reply was in flight
        release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one(chat_tui.ChatInput).text == "next"  # never clobbered


async def test_canceling_keeps_the_transcript_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    from textual.widgets import Static

    async def fake_run(*_a: Any, **_kw: Any) -> str:
        await asyncio.Event().wait()  # hangs until the turn is canceled
        raise AssertionError("unreachable")

    monkeypatch.setattr(chat_tui.app.agent, "run", fake_run)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        for _ in range(50):
            await pilot.pause()
            if app._busy:
                break
        app.action_cancel()
        for _ in range(100):
            await pilot.pause()
            if not app._busy:
                break
        posted = " ".join(str(w.render()) for w in app.query(Static))
        assert "(canceled)" in posted
        assert "(not sent)" in posted  # the dropped turn is marked, not left looking answered
        assert app.messages == []
        # A deliberate stop is not a failure: the text is NOT pushed back into the input.
        assert app.query_one(chat_tui.ChatInput).text == ""


async def test_truncating_the_history_prunes_stored_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    # _reasonings is keyed by id() of the message dict, so a message dropped without pruning leaves
    # an entry pointing at a freed address — which a later dict can reuse, folding a dead turn's
    # thinking into an unrelated one's `/export --thinking`.
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
        assert app._reasonings  # stored against the assistant message

        app._drop_messages(0)  # the one truncation path every caller goes through
        assert app.messages == []
        assert app._reasonings == {}

        # /system clear drops messages[0] on its own; it must prune too.
        app.messages.insert(0, {"role": "system", "content": "be terse"})
        app._reasonings[id(app.messages[0])] = "stale"
        app._run_command("/system clear")
        assert app._reasonings == {}


def test_the_tui_applies_the_configured_max_token_cap() -> None:
    # Web parity: the serving path caps a request that omits max_tokens so a small model can't run
    # away on a hard tool question and never emit a final answer. The TUI sent None (unbounded).
    from stabbur.config import get_settings

    assert _app()._max_tokens == get_settings().default_max_tokens
    assert _app(max_tokens=7)._max_tokens == 7  # an explicit -n still wins


async def test_the_cap_reaches_the_agent_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from stabbur.config import get_settings

    seen: dict[str, Any] = {}

    async def fake_run(
        base: str, messages: list[dict[str, Any]], toolset: Any, max_tokens: Any, *_a: Any, **_kw: Any
    ) -> str:
        seen["max_tokens"] = max_tokens
        messages.append({"role": "assistant", "content": "ok"})
        return "ok"

    monkeypatch.setattr(chat_tui.app.agent, "run", fake_run)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert seen["max_tokens"] == get_settings().default_max_tokens


# -- UX ------------------------------------------------------------------------------------


async def test_ctrl_d_quits_only_on_an_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    # Ctrl+D used to quit outright, throwing away a half-written message and the conversation on a
    # single (easily mistyped) keystroke. Readline semantics: it only quits an empty input.
    quits: list[bool] = []

    async def fake_quit(_self: Any) -> None:
        quits.append(True)

    monkeypatch.setattr(chat_tui.ChatApp, "action_quit", fake_quit)
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert quits == []  # nothing quit...
        assert app.query_one(chat_tui.ChatInput).text == "hi"  # ...and nothing was discarded

        app.query_one(chat_tui.ChatInput).text = ""
        await pilot.press("ctrl+d")
        await pilot.pause()
        assert quits == [True]


async def test_export_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    # /export defaults to chat.md, so a second export destroyed the first transcript — and a
    # transcript is not something the user can get back.
    dest = tmp_path / "chat.md"
    dest.write_text("PRE-EXISTING", encoding="utf-8")
    app = _app()
    async with app.run_test() as pilot:
        app.messages.append({"role": "user", "content": "hi"})
        app.messages.append({"role": "assistant", "content": "hello"})
        app.action_export(str(dest))
        app.action_export(str(dest))
        await pilot.pause()

    assert dest.read_text(encoding="utf-8") == "PRE-EXISTING"  # untouched
    assert "hello" in (tmp_path / "chat-2.md").read_text(encoding="utf-8")
    assert "hello" in (tmp_path / "chat-3.md").read_text(encoding="utf-8")  # and again, no clobber


async def test_the_footer_and_help_document_quit_and_multiline() -> None:
    from textual.widgets import Static

    app = _app()
    async with app.run_test() as pilot:
        footer = _status_text(app)
        assert "^d quit" in footer  # the hint advertised no way out at all
        assert "^j newline" in footer
        app.action_help()
        await pilot.pause()
        helped = " ".join(str(w.render()) for w in app.query(Static))
    assert "ctrl+j" in helped  # multi-line input was undocumented
    assert "backslash" in helped
