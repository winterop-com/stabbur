"""Headless pilot tests for the `stabbur init` wizard."""

from stabbur.cli.init_wizard import CHAT_PROMPT, VOICE_PROMPT, InitWizard, ModelChoice
from stabbur.plugins import McpServer

_MODELS = [ModelChoice("pub/A-GGUF", "2 GB"), ModelChoice("pub/B-GGUF", "7 GB")]
_SERVERS = [
    McpServer(name="datetime", command="c1", description="dates"),
    McpServer(name="files", command="c2", description="files"),
    McpServer(name="web", command="c3", description="pages"),
]


def _wizard() -> InitWizard:
    return InitWizard(name="hello", models=_MODELS, servers=_SERVERS)


async def test_tools_are_a_real_multi_select() -> None:
    """Space toggles servers in a visible list — the thing the old prompt could not do.

    The previous wizard asked for *comma-separated numbers*: you typed a selection blind, and a
    typo silently picked a different server. Two toggles here must yield exactly two servers.
    """
    app = _wizard()
    async with app.run_test() as pilot:
        await pilot.press("down")  # highlight the second model
        await pilot.press("tab")  # into the tools list
        await pilot.press("space")  # datetime
        await pilot.press("down", "space")  # files
        await pilot.press("ctrl+s")
    assert app.return_value is not None
    assert app.return_value.model == "pub/B-GGUF"
    assert app.return_value.mcp == [("datetime", "c1"), ("files", "c2")]


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


async def test_creating_without_a_model_asks_rather_than_scaffolding_nothing() -> None:
    # An empty model list is the one unanswerable state; it must not exit with a blank model.
    app = InitWizard(name="hello", models=[], servers=[])
    async with app.run_test() as pilot:
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.is_running  # still on the form
        await pilot.press("escape")
    assert app.return_value is None
