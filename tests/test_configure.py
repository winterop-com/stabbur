"""Headless pilot tests for `stabbur configure`, and the plan it applies."""

import json
import tomllib
from pathlib import Path

import pytest

from stabbur import mcpservers, project
from stabbur.cli import project as project_cli
from stabbur.cli.configure_tui import ConfigureApp, LibraryEntry, ModelOption, VoiceOption
from stabbur.plugins import McpServer

_MODELS = [
    ModelOption("pub/Bound-GGUF", "gguf · 2 GB", present=True),
    ModelOption("pub/Better-GGUF", "gguf · 7 GB", present=False),
]
_SERVERS = [
    McpServer(name="datetime", command="c1", description="dates"),
    McpServer(name="files", command="c2", description="files"),
]
_VOICES = [
    VoiceOption("kokoro", "Kokoro — tts, ~310 MB", present=True),
    VoiceOption("whisper", "Whisper — stt, ~1.5 GB", present=False),
]
_LIBRARY = [LibraryEntry("pub/Bound-GGUF", "2 GB"), LibraryEntry("pub/Old-GGUF", "9 GB")]


def _app(**overrides: object) -> ConfigureApp:
    kwargs: dict[str, object] = {
        "name": "mybot",
        "models": _MODELS,
        "current_model": "pub/Bound-GGUF",
        "system_prompt": "Be brief.",
        "chat_voice": "kokoro:af_heart",
        "voice_enabled": True,
        "servers": _SERVERS,
        "enabled_tools": {"datetime"},
        "voices": _VOICES,
        "library": _LIBRARY,
    }
    kwargs.update(overrides)
    return ConfigureApp(**kwargs)  # type: ignore[arg-type]


async def test_it_opens_on_what_the_project_uses_and_saves_it_unchanged() -> None:
    """Saving without touching anything must describe the project as it already is.

    A configure screen that quietly rewrites settings you did not look at is worse than no
    configure screen: the safe action (save) has to be the identity.
    """
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+s")
    plan = app.return_value
    assert plan is not None
    assert plan.model == "pub/Bound-GGUF"  # the bound model was highlighted, not the first row
    assert plan.system_prompt == "Be brief."
    assert plan.tools == [("datetime", "c1")]  # pre-checked from .mcp.json, still checked
    assert plan.pull_voices == []  # nothing added, so nothing downloads
    assert plan.remove_models == []


async def test_only_missing_voices_are_downloaded() -> None:
    # A voice the project already holds is checked on open; re-pulling it would turn a settings
    # change into a multi-gigabyte download.
    app = _app()
    async with app.run_test() as pilot:
        app.query_one("#voices").select_all()  # type: ignore[attr-defined]
        await pilot.pause()
        await pilot.press("ctrl+s")
    assert app.return_value is not None
    assert app.return_value.pull_voices == ["whisper"]  # kokoro was already present


async def test_quitting_changes_nothing() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press("escape")
    assert app.return_value is None


async def test_a_project_with_nothing_to_list_still_saves() -> None:
    # No plugins installed, an empty library, no voice models available: the empty states are
    # Labels rather than SelectionLists, so the save path must not query widgets that aren't there.
    app = _app(servers=[], enabled_tools=set(), voices=[], library=[])
    async with app.run_test() as pilot:
        await pilot.press("ctrl+s")
    assert app.return_value is not None
    assert app.return_value.tools == []


def test_apply_plan_rewrites_the_manifest_and_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The plan is applied to files: manifest first, then tools, then the disk-heavy work.

    Settings are what the user came for; a download that fails afterwards must not lose them.
    """
    (tmp_path / "stabbur.toml").write_text(
        'libraries = ["library"]\n[project]\nmodel = "pub/Bound-GGUF"\nsystem_prompt = "Be brief."\n'
    )
    mcpservers.add(mcpservers.McpServer(name="datetime", command="c1"), glob=False, project_dir=tmp_path)
    monkeypatch.chdir(tmp_path)
    proj = project.load()
    assert proj is not None

    monkeypatch.setattr(project_cli.library_ops, "scan", lambda *a, **k: [])
    monkeypatch.setattr(project_cli.library_ops, "roots", lambda *a, **k: [tmp_path / "library"])
    pulled: list[str] = []
    monkeypatch.setattr(
        project_cli.catalog_ops,
        "pull",
        lambda source, name, **k: pulled.append(name) or _Result(),  # type: ignore[func-returns-value]
    )

    plan = project_cli.configure_tui.ConfigurePlan(
        model="pub/Better-GGUF",
        system_prompt="Answer in Norwegian.",
        chat_voice="kokoro:af_sky",
        voice_enabled=False,
        tools=[("files", "c2")],  # datetime dropped, files added
        pull_voices=["whisper"],
        remove_models=[],
    )
    project_cli._apply_plan(proj, plan)

    parsed = tomllib.loads((tmp_path / "stabbur.toml").read_text())
    assert parsed["project"]["model"] == "pub/Better-GGUF"
    assert parsed["project"]["system_prompt"] == "Answer in Norwegian."
    assert parsed["project"]["chat_voice"] == "kokoro:af_sky"
    assert parsed["voice"]["enabled"] is False
    assert parsed["libraries"] == ["library"]  # untouched: the project's own store stays its own

    servers = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert set(servers) == {"files"}  # the deselected one is gone, the new one is written
    assert pulled == ["whisper"]


class _Result:
    """Stand-in for a PullResult: only the field the apply path prints."""

    size_human = "1 GB"
