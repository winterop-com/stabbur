"""CLI one-shot (`stabbur chat -p`) confirmation-gate wiring.

A write-enabled project assistant makes ``stabbur chat -p`` gate non-read-only tool calls. The
one-shot has no human to confirm, so the gate DENIES by default (fail-safe); ``--allow-writes``
supplies a blanket auto-approve sink. A read-only or absent assistant leaves everything ungated.
"""

import contextlib
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from stabbur import agent, capabilities, cli, tools
from stabbur import library as library_ops
from stabbur import project as project_mod
from stabbur import runtime as runtime_mod
from stabbur.library import LibraryModel
from stabbur.models import ModelFormat
from stabbur.project import AssistantInfo, Project
from stabbur.runtime import sampling as sampling_mod
from stabbur.runtime import serve_registry
from stabbur.runtime.sampling import ModelSampling

runner = CliRunner()


def _model() -> LibraryModel:
    p = Path("/tmp/pub/Foo-GGUF")
    return LibraryModel(
        name="pub/Foo-GGUF",
        model_format=ModelFormat.gguf,
        generative=True,
        path=p,
        load_target=p / "w.gguf",
        size_bytes=1000,
    )


class _StubToolset:
    """A toolset whose one tool is a write (not read-only) and records its executions."""

    schemas: list[dict[str, Any]] = []

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.errors: list[tuple[str, str]] = []  # like the real toolset: the one-shot prints these

    @property
    def names(self) -> set[str]:
        return set()

    def is_readonly(self, name: str) -> bool:
        return False  # every tool is treated as a write

    async def call(self, name: str, args: dict[str, Any], timeout: float | None = None) -> tools.ToolResult:
        self.calls.append(name)
        return tools.ToolResult(text="wrote")


def _staged_stream() -> Any:
    """A staged ``_stream_turn``: round 1 calls ``srv__write``, round 2 answers ``done``.

    Records each round's ``messages`` so the tool turn (executed or declined) can be inspected.
    """
    rounds = iter(
        [("", [{"id": "1", "name": "srv__write", "args": "{}"}], None, "tool_calls"), ("done", [], None, "stop")]
    )
    captured: dict[str, Any] = {}

    async def staged(
        http: Any, base_url: str, body: Any, on_token: Any, on_reasoning: Any = None
    ) -> tuple[str, list[Any], dict[str, Any] | None, str | None]:
        captured["messages"] = body["messages"]
        return next(rounds)

    staged.captured = captured  # type: ignore[attr-defined]
    return staged


def _install_stubs(monkeypatch: pytest.MonkeyPatch, proj: Project | None) -> tuple[_StubToolset, Any]:
    """Wire the CLI one-shot to a stubbed runtime + agent stream + toolset, and a fixed project."""
    toolset = _StubToolset()
    staged = _staged_stream()
    monkeypatch.setattr(project_mod, "load", lambda *a, **k: proj)
    monkeypatch.setattr(capabilities, "capabilities", lambda m: SimpleNamespace(vision=False, audio=False))
    monkeypatch.setattr(sampling_mod, "recommended", lambda m: ModelSampling(repeat_penalty=1.1))
    monkeypatch.setattr(library_ops, "find", lambda *a, **k: [_model()])
    monkeypatch.setattr(runtime_mod, "load", lambda m: SimpleNamespace(base="http://runtime"))
    monkeypatch.setattr(runtime_mod, "stop", lambda rt: None)
    monkeypatch.setattr(serve_registry, "discover", lambda *a, **k: None)

    @contextlib.asynccontextmanager
    async def _connect(servers: Any) -> AsyncGenerator[_StubToolset, None]:
        yield toolset

    monkeypatch.setattr(tools, "connect", _connect)
    monkeypatch.setattr(agent, "_stream_turn", staged)
    return toolset, staged


def _declined(staged: Any) -> bool:
    """Whether the model was fed the declined-action contract for the gated write."""
    return any(
        m.get("role") == "tool" and m.get("content") == "error: user declined this action"
        for m in staged.captured.get("messages", [])
    )


def _write_enabled() -> Project:
    return Project(model="pub/Foo-GGUF", assistant=AssistantInfo(readonly=False))


def test_write_enabled_project_denies_gated_write_without_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    toolset, staged = _install_stubs(monkeypatch, _write_enabled())
    result = runner.invoke(cli.app, ["chat", "pub/Foo-GGUF", "-p", "do it", "--mcp", "foo"])
    assert result.exit_code == 0, result.output
    assert toolset.calls == []  # the write never ran
    assert _declined(staged)  # the model got the declined contract and continued


def test_allow_writes_approves_the_gated_write(monkeypatch: pytest.MonkeyPatch) -> None:
    toolset, staged = _install_stubs(monkeypatch, _write_enabled())
    result = runner.invoke(cli.app, ["chat", "pub/Foo-GGUF", "-p", "do it", "--mcp", "foo", "--allow-writes"])
    assert result.exit_code == 0, result.output
    assert toolset.calls == ["srv__write"]  # auto-approved and executed
    assert not _declined(staged)


def test_absent_assistant_is_ungated(monkeypatch: pytest.MonkeyPatch) -> None:
    # No project (policy "none"): the call runs without --allow-writes, identical to pre-confirmation.
    toolset, staged = _install_stubs(monkeypatch, None)
    result = runner.invoke(cli.app, ["chat", "pub/Foo-GGUF", "-p", "do it", "--mcp", "foo"])
    assert result.exit_code == 0, result.output
    assert toolset.calls == ["srv__write"]
    assert not _declined(staged)


def test_readonly_assistant_is_ungated(monkeypatch: pytest.MonkeyPatch) -> None:
    # A read-only assistant gates nothing (policy "none"): the call runs without --allow-writes.
    toolset, staged = _install_stubs(monkeypatch, Project(model="pub/Foo-GGUF", assistant=AssistantInfo(readonly=True)))
    result = runner.invoke(cli.app, ["chat", "pub/Foo-GGUF", "-p", "do it", "--mcp", "foo"])
    assert result.exit_code == 0, result.output
    assert toolset.calls == ["srv__write"]
    assert not _declined(staged)


def test_target_without_prompt_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # --target only narrows on the scripted -p path; the interactive TUI would gate on the target while
    # exposing sibling write tools, so it is refused there (no -p) with a clear error, exit code 1.
    monkeypatch.setattr(project_mod, "load", lambda *a, **k: _write_enabled())
    result = runner.invoke(cli.app, ["chat", "pub/Foo-GGUF", "--target", "prod"])
    assert result.exit_code == 1
    assert "--target requires -p" in result.output
