"""Headless pilot tests for the `stabbur init` wizard."""

from types import SimpleNamespace

import pytest

from stabbur.cli.init_wizard import CHAT_PROMPT, VOICE_PROMPT, InitWizard, ModelChoice
from stabbur.plugins import McpServer

_MODELS = [ModelChoice("pub/A-GGUF", "2 GB", 2.0), ModelChoice("pub/B-GGUF", "7 GB", 7.0)]
_SERVERS = [
    McpServer(name="datetime", command="c1", description="dates"),
    McpServer(name="files", command="c2", description="files"),
    McpServer(name="web", command="c3", description="pages"),
]


def _wizard() -> InitWizard:
    return InitWizard(name="hello", models=_MODELS, servers=_SERVERS, voices_gb=5.0)


async def test_tools_are_a_real_multi_select() -> None:
    """Space toggles servers in a visible list — the thing the old prompt could not do.

    The previous wizard asked for *comma-separated numbers*: you typed a selection blind, and a
    typo silently picked a different server. Two toggles here must yield exactly two servers.
    """
    app = _wizard()
    async with app.run_test() as pilot:
        await pilot.press("down")  # past "no model yet", onto the second real model
        await pilot.press("tab")  # into the tools list
        await pilot.press("space")  # datetime
        await pilot.press("down", "space")  # files
        await pilot.press("ctrl+s")
    assert app.return_value is not None
    assert app.return_value.model == "pub/B-GGUF"
    assert app.return_value.mcp == [("datetime", "c1"), ("files", "c2")]


async def test_the_kind_travels_so_the_prompt_matches_it() -> None:
    # The kind tunes the system prompt; every project gets the voices either way.
    app = _wizard()
    async with app.run_test() as pilot:
        app.query_one("#kind-voice").value = True  # type: ignore[attr-defined]
        await pilot.pause()
        await pilot.press("ctrl+s")
    assert app.return_value is not None
    assert app.return_value.voice is True


async def test_quitting_returns_nothing_so_no_project_is_written() -> None:
    # The caller scaffolds only on a result: escape must leave it with nothing to write.
    app = _wizard()
    async with app.run_test() as pilot:
        await pilot.press("escape")
    assert app.return_value is None


async def test_the_kind_switches_the_prompt_but_never_overwrites_an_edit() -> None:
    # Picking "Voice" retunes the default prompt; a prompt the user has typed is theirs to keep.
    app = _wizard()
    async with app.run_test() as pilot:
        await pilot.press("tab")  # focus moves off the model list
        prompt = app.query_one("#prompt")
        app.query_one("#kind-voice").value = True  # type: ignore[attr-defined]
        await pilot.pause()
        assert prompt.value == VOICE_PROMPT  # type: ignore[attr-defined]

        prompt.value = "Mine."  # type: ignore[attr-defined]
        app.query_one("#kind-chat").value = True  # type: ignore[attr-defined]
        await pilot.pause()
        assert prompt.value == "Mine."  # type: ignore[attr-defined]
        assert prompt.value != CHAT_PROMPT  # type: ignore[attr-defined]


async def test_a_project_can_bind_no_model_and_still_be_worth_making() -> None:
    """ "No model yet" is a real answer: the voices still come, so the project can speak and listen.

    Someone may already have the weights elsewhere, or want the scaffold now and the model later —
    and a wizard that refuses to proceed without a 7 GB download makes that a fight.
    """
    app = _wizard()
    async with app.run_test() as pilot:
        await pilot.press("up")  # the wizard opens on the recommended model; step to "no model yet"
        await pilot.press("ctrl+s")
    assert app.return_value is not None
    assert app.return_value.model == ""


async def test_the_screen_says_what_it_will_download() -> None:
    # The cost of a project is about ten gigabytes; a screen that fetches that without naming it
    # decides for you. The total names the model and the voices, and follows the highlight.
    app = _wizard()
    async with app.run_test() as pilot:
        await pilot.pause()
        with_model = str(app.query_one("#total").render())
        await pilot.press("up")  # "no model yet"
        await pilot.pause()
        without = str(app.query_one("#total").render())
        await pilot.press("escape")
    assert "7.0 GB" in with_model and "the voices" in with_model  # 2 GB model + 5 GB voices
    assert "5.0 GB" in without and "gemma" not in without


async def test_an_upstream_lists_its_models_and_downloads_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Typing an upstream replaces the model list with what that server serves.

    Offering this machine's curated downloads for a remote project would write a model id the
    remote may not have — the names rarely match — and the total must drop to the voices, since
    nothing about the model is downloaded here.
    """
    import httpx

    def fake_get(url: str, timeout: float = 5.0) -> object:
        assert url == "http://gpu-box:8080/v1/models"
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": [{"id": "gemma-remote"}, {"id": "qwen-remote"}]},
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    app = _wizard()
    async with app.run_test() as pilot:
        field = app.query_one("#upstream")
        field.value = "http://gpu-box:8080/v1"  # type: ignore[attr-defined]
        field.focus()
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause()
            if any(m.detail == "on the upstream" for m in app._models):
                break
        assert [m.name for m in app._models if m.name] == ["gemma-remote", "qwen-remote"]
        assert "5.0 GB" in str(app.query_one("#total").render())  # the voices only
        await pilot.press("ctrl+s")

    assert app.return_value is not None
    assert app.return_value.upstream == "http://gpu-box:8080/v1"
    assert app.return_value.model == "gemma-remote"


async def test_an_unreachable_upstream_keeps_the_local_list(monkeypatch: pytest.MonkeyPatch) -> None:
    # A typo or a box that is off must not empty the screen: the local models stay selectable and
    # the label says what happened.
    import httpx

    def boom(url: str, timeout: float = 5.0) -> object:
        raise OSError("no route to host")

    monkeypatch.setattr(httpx, "get", boom)
    app = _wizard()
    async with app.run_test() as pilot:
        field = app.query_one("#upstream")
        field.value = "http://nowhere:9/v1"  # type: ignore[attr-defined]
        field.focus()
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause()
            if "unreachable" in str(app.query_one("#model-label").render()):
                break
        assert [m.name for m in app._models if m.name] == ["pub/A-GGUF", "pub/B-GGUF"]
        await pilot.press("escape")
