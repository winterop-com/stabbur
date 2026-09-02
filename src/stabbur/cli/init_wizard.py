"""The `stabbur init` wizard: one Textual form instead of a run of numbered prompts.

The old wizard was five `typer.prompt` calls that printed a list and asked for a number, and its
tool step asked for *comma-separated* numbers — a multi-select you have to type, with no way to
see what you had picked, and nothing to stop a typo silently selecting the wrong server. stabbur
already ships a Textual TUI for chat; the scaffolder gets the same treatment: one screen, arrow
keys, a real checkbox list for tools, and everything visible at once so a choice can be revised
before anything is written.

There is no "which Kokoro voice" field: which voice speaks a reply is a click in the UI, not a
property of the assistant, and asking for it here made the project look like it owned a choice it
does not. The manifest still carries a default so a fresh project can speak immediately.

Nothing here touches the disk. The wizard returns the choices and the caller scaffolds, so
quitting (escape, ctrl+c) leaves no half-made project behind.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, Label, OptionList, RadioButton, RadioSet, SelectionList

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stabbur.plugins import McpServer  # the advertised-server shape, with its description

CHAT_PROMPT = "You are a concise, helpful assistant."
VOICE_PROMPT = "You are a friendly voice assistant. Keep replies short and natural for speech."
DEFAULT_VOICE = "kokoro:af_heart"


class ModelChoice(NamedTuple):
    """One selectable model: what to write into the manifest, and how to describe it."""

    name: str
    detail: str


class WizardResult(NamedTuple):
    """What the wizard collected. ``None`` means the user quit without creating anything."""

    model: str
    mcp: list[tuple[str, str]]
    system_prompt: str
    voice: bool
    """A voice project: it gets speech-to-text as well, so the mic half of "mic in" exists."""


class InitWizard(App[WizardResult | None]):
    """The scaffolding form: kind, model, tools, prompt, voice — on one screen."""

    CSS = """
    Screen { padding: 1 2; }
    #body { height: 1fr; }
    .section { color: $text-muted; margin-top: 1; }
    .hint { color: $text-muted; }
    #models { height: auto; max-height: 12; border: round $panel-lighten-2; }
    #tools { height: auto; max-height: 10; border: round $panel-lighten-2; }
    #prompt { border: round $panel-lighten-2; }
    #actions { height: auto; margin-top: 1; }
    #actions Button { margin-right: 2; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "create", "Create", show=True, priority=True),
    ]

    def __init__(self, *, name: str, models: list[ModelChoice], servers: list[McpServer]) -> None:
        super().__init__()
        self._project_name = name
        self._models = models
        self._servers = servers

    def compose(self) -> ComposeResult:
        yield Label(f"New stabbur project: {self._project_name}")
        yield Label("Everything lives in this directory — model, config, tools.", classes="hint")
        with VerticalScroll(id="body"):
            yield Label("Kind", classes="section")
            with RadioSet(id="kind"):
                yield RadioButton("Chat — text conversation (replies can still be spoken)", value=True, id="kind-chat")
                yield RadioButton("Voice — adds speech-to-text, so the mic works too", id="kind-voice")

            yield Label("Model — downloaded into this project", classes="section")
            yield OptionList(*(f"{m.name}  ({m.detail})" for m in self._models), id="models")

            yield Label("Tools — space to toggle, MCP servers this assistant can call", classes="section")
            if self._servers:
                yield SelectionList[int](
                    *((f"{s.name} — {s.description}", i) for i, s in enumerate(self._servers)), id="tools"
                )
            else:
                yield Label("No MCP plugins installed — add them later with `stabbur mcp add`.", classes="hint")

            yield Label("System prompt", classes="section")
            yield Input(value=CHAT_PROMPT, id="prompt")
        with Horizontal(id="actions"):
            yield Button("Create project", variant="primary", id="create")
            yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        """Start on the model list: the one choice with no sensible default."""
        self.query_one("#models", OptionList).focus()
        if self._models:
            self.query_one("#models", OptionList).highlighted = 0

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Swap the prompt to match the kind — but never overwrite something the user has typed."""
        prompt = self.query_one("#prompt", Input)
        voice_kind = event.pressed.id == "kind-voice"
        if prompt.value in (CHAT_PROMPT, VOICE_PROMPT, ""):
            prompt.value = VOICE_PROMPT if voice_kind else CHAT_PROMPT

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create":
            self.action_create()
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        self.exit(None)

    def action_create(self) -> None:
        """Collect the form into a result. A model is the one thing that has to be chosen."""
        models = self.query_one("#models", OptionList)
        index = models.highlighted
        if index is None or not self._models:
            self.notify("Pick a model first.", severity="warning")
            models.focus()
            return
        picked: list[tuple[str, str]] = []
        if self._servers:
            # query_one's type argument is a runtime isinstance check, so it cannot take a
            # subscripted generic: fetch the widget untyped and narrow it here.
            tools = self.query_one("#tools")
            assert isinstance(tools, SelectionList)
            chosen: list[int] = list(tools.selected)
            picked = [(self._servers[i].name, self._servers[i].command) for i in chosen]
        self.exit(
            WizardResult(
                model=self._models[index].name,
                mcp=picked,
                system_prompt=self.query_one("#prompt", Input).value.strip() or CHAT_PROMPT,
                voice=bool(self.query_one("#kind-voice", RadioButton).value),
            )
        )


def run_wizard(*, name: str, models: list[ModelChoice], servers: list[McpServer]) -> WizardResult | None:
    """Run the wizard, returning the choices — or ``None`` if the user quit."""
    return InitWizard(name=name, models=models, servers=servers).run()
