"""Chat-TUI confirmation gate: the ConfirmModal and its wiring into the agent loop.

A write-enabled project assistant gates non-read-only tool calls behind a ConfirmModal; Approve
resumes the turn, Deny feeds the model the declined contract. A read-only / absent assistant gates
nothing. The modal is exercised in isolation (mount + press) and the policy derivation directly.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from kodo import chat_tui, runtime
from kodo.chat_tui._widgets import ConfirmModal
from kodo.chat_tui.app import _confirm_policy
from kodo.library import LibraryModel
from kodo.models import ModelFormat
from kodo.project import AssistantInfo, Project
from kodo.runtime.sampling import ModelSampling


@pytest.fixture(autouse=True)
def _stub_model_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_tui.app.sampling_mod, "recommended", lambda _m: ModelSampling(repeat_penalty=1.1))
    monkeypatch.setattr(
        chat_tui.app.capabilities, "capabilities", lambda _m: SimpleNamespace(context_length=1024, vision=False)
    )


def _modal_open(app: Any) -> bool:
    """Whether the app's current screen is a ConfirmModal (a fresh read, so no sticky narrowing)."""
    return isinstance(app.screen, ConfirmModal)


def _fake_runtime(model: LibraryModel) -> runtime.RuntimeProc:
    rt = runtime.RuntimeProc(
        proc=cast("subprocess.Popen[bytes]", MagicMock()),
        base="http://127.0.0.1:9",
        port=9,
        cmd=["llama-server"],
        state_dir=Path("/tmp/kodo-fake-runtime"),
        log_fh=None,
    )
    rt.model = model
    return rt


def _app(monkeypatch: pytest.MonkeyPatch, proj: Project | None) -> chat_tui.ChatApp:
    monkeypatch.setattr(chat_tui.app.project, "load", lambda *a, **k: proj)
    model = LibraryModel(
        name="pub/Foo-GGUF",
        model_format=ModelFormat.gguf,
        path=Path("/lib/gguf/pub/Foo-GGUF"),
        load_target=Path("/lib/gguf/pub/Foo-GGUF/model.gguf"),
    )
    return chat_tui.ChatApp(
        endpoint=_fake_runtime(model), servers=[], system_prompt="", images=[], audios=[], max_tokens=None
    )


# -- policy derivation ---------------------------------------------------------


def test_confirm_policy_derivation() -> None:
    assert _confirm_policy(None) == "none"  # no project
    assert _confirm_policy(Project(model="m")) == "none"  # no [assistant] block
    assert _confirm_policy(Project(model="m", assistant=AssistantInfo(readonly=True))) == "none"
    assert _confirm_policy(Project(model="m", assistant=AssistantInfo(readonly=False))) == "writes"
    assert _confirm_policy(Project(model="m", assistant=AssistantInfo())) == "writes"  # present, not read-only


def test_app_derives_policy_from_project(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _app(monkeypatch, _write_enabled())._confirm_policy == "writes"
    assert _app(monkeypatch, None)._confirm_policy == "none"


# -- ConfirmModal in isolation -------------------------------------------------


class _ModalHost(App[None]):
    """A tiny host app that opens a ConfirmModal and stashes its dismiss value."""

    def __init__(self) -> None:
        super().__init__()
        self.result: bool | None = None

    def compose(self) -> ComposeResult:
        return iter(())

    async def open(self) -> None:
        self.result = await self.push_screen_wait(ConfirmModal("srv__write", {"id": "abc", "n": 1}))


async def test_confirm_modal_approve_returns_true() -> None:
    host = _ModalHost()
    async with host.run_test() as pilot:
        host.run_worker(host.open(), group="modal")
        await pilot.pause()
        assert isinstance(host.screen, ConfirmModal)  # the modal surfaced
        await pilot.click("#approve")
        await pilot.pause()
    assert host.result is True


async def test_confirm_modal_deny_returns_false() -> None:
    host = _ModalHost()
    async with host.run_test() as pilot:
        host.run_worker(host.open(), group="modal")
        await pilot.pause()
        await pilot.click("#deny")
        await pilot.pause()
    assert host.result is False


async def test_confirm_modal_shows_tool_name_and_args() -> None:
    from textual.widgets import Label, Static  # noqa: PLC0415

    host = _ModalHost()
    async with host.run_test() as pilot:
        host.run_worker(host.open(), group="modal")
        await pilot.pause()
        modal = host.screen
        assert isinstance(modal, ConfirmModal)
        title = str(modal.query_one("#confirm-title", Label).render())
        args = str(modal.query_one("#confirm-args", Static).render())
        assert "srv__write" in title  # the tool name
        assert "abc" in args and "1" in args  # a compact view of the args
        assert {b.id for b in modal.query(Button)} == {"approve", "deny"}
        await pilot.click("#deny")  # unwind cleanly


# -- full-loop wiring ----------------------------------------------------------


def _write_enabled() -> Project:
    return Project(model="pub/Foo-GGUF", assistant=AssistantInfo(readonly=False))


class _StubToolset:
    schemas: list[dict[str, Any]] = []

    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def names(self) -> set[str]:
        return set()

    def is_readonly(self, name: str) -> bool:
        return False

    def subset(self, names: Any) -> "_StubToolset":
        return self

    async def call(self, name: str, args: dict[str, Any], timeout: float | None = None) -> Any:
        from kodo import tools  # noqa: PLC0415

        self.calls.append(name)
        return tools.ToolResult(text="wrote")


def _staged_stream() -> Any:
    rounds = iter([("", [{"id": "1", "name": "srv__write", "args": "{}"}], None), ("done", [], None)])

    async def staged(
        http: Any, base_url: str, body: Any, on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[Any], dict[str, Any] | None]:
        return next(rounds)

    return staged


async def test_gated_write_surfaces_modal_and_approve_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_tui.app.agent, "_stream_turn", _staged_stream())
    toolset = _StubToolset()
    app = _app(monkeypatch, _write_enabled())
    app.toolset = cast("Any", toolset)
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        for _ in range(100):
            await pilot.pause()
            if isinstance(app.screen, ConfirmModal):
                break
        assert isinstance(app.screen, ConfirmModal)  # the write gated on a modal
        await pilot.click("#approve")
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert toolset.calls == ["srv__write"]  # approved -> executed


async def test_gated_write_deny_declines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_tui.app.agent, "_stream_turn", _staged_stream())
    toolset = _StubToolset()
    app = _app(monkeypatch, _write_enabled())
    app.toolset = cast("Any", toolset)
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        for _ in range(100):
            await pilot.pause()
            if isinstance(app.screen, ConfirmModal):
                break
        await pilot.click("#deny")
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert toolset.calls == []  # denied -> never executed
    tool_msg = next(m for m in app.messages if m.get("role") == "tool")
    assert tool_msg["content"] == "error: user declined this action"


async def test_stop_mid_confirmation_unwinds_the_modal(monkeypatch: pytest.MonkeyPatch) -> None:
    # A stop (Esc) while the ConfirmModal is up must cancel the turn and close the modal cleanly.
    monkeypatch.setattr(chat_tui.app.agent, "_stream_turn", _staged_stream())
    toolset = _StubToolset()
    app = _app(monkeypatch, _write_enabled())
    app.toolset = cast("Any", toolset)
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        for _ in range(100):
            await pilot.pause()
            if isinstance(app.screen, ConfirmModal):
                break
        assert isinstance(app.screen, ConfirmModal)
        app.action_cancel()  # Esc: stop the in-flight turn while the modal is open
        for _ in range(100):
            await pilot.pause()
            if not _modal_open(app):
                break
        assert not _modal_open(app)  # the modal was dismissed
    assert toolset.calls == []  # nothing ran


async def test_readonly_project_does_not_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_tui.app.agent, "_stream_turn", _staged_stream())
    toolset = _StubToolset()
    app = _app(monkeypatch, None)  # policy "none"
    app.toolset = cast("Any", toolset)
    async with app.run_test() as pilot:
        await pilot.press("h", "i")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not isinstance(app.screen, ConfirmModal)  # never gated
    assert toolset.calls == ["srv__write"]
