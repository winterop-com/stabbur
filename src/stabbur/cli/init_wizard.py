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

The voices are not a choice either — every project gets Kokoro, VoxCPM2 and Whisper, so it can
speak and transcribe the day it is made, even one that binds no model yet. The screen says what
that costs rather than asking; `--no-voices` is the way out for someone who means it.

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
    """One selectable model: what to write into the manifest, how to describe it, its cost."""

    name: str
    detail: str
    size_gb: float = 0.0


class WizardResult(NamedTuple):
    """What the wizard collected. ``None`` means the user quit without creating anything."""

    model: str
    """The model to bind, or ``""`` for a project that binds none yet."""
    mcp: list[tuple[str, str]]
    system_prompt: str
    voice: bool
    """A voice project: only the system prompt differs — every project gets the voices."""


class InitWizard(App[WizardResult | None]):
    """The scaffolding form: kind, model, tools, prompt, voice — on one screen."""

    CSS = """
    Screen { padding: 1 2; }
    #body { height: 1fr; }
    .section { color: $text-muted; margin-top: 1; }
    .hint { color: $text-muted; }
    #models { height: auto; max-height: 12; border: round $panel-lighten-2; }
    #tools { height: auto; max-height: 10; border: round $panel-lighten-2; }
    #total { color: $text-muted; margin-top: 1; }
    #prompt { border: round $panel-lighten-2; }
    #actions { height: auto; margin-top: 1; }
    #actions Button { margin-right: 2; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "create", "Create", show=True, priority=True),
    ]

    def __init__(
        self,
        *,
        name: str,
        models: list[ModelChoice],
        servers: list[McpServer],
        voices_gb: float = 0.0,
    ) -> None:
        super().__init__()
        self._project_name = name
        # A project that binds nothing yet is a real answer: you may already have a model
        # elsewhere, or want the scaffold now and the weights later. The voices still come, so
        # such a project can speak and transcribe on the day it is made.
        self._models = [ModelChoice("", "No model yet — bind one later with `stabbur configure`", 0.0), *models]
        self._servers = servers
        self._voices_gb = voices_gb

    def compose(self) -> ComposeResult:
        yield Label(f"New stabbur project: {self._project_name}")
        yield Label("Everything lives in this directory — model, voices, config, tools.", classes="hint")
        with VerticalScroll(id="body"):
            yield Label("Kind", classes="section")
            with RadioSet(id="kind"):
                yield RadioButton("Chat — text conversation (replies can still be spoken)", value=True, id="kind-chat")
                yield RadioButton("Voice — a spoken-first assistant (prompt tuned for speech)", id="kind-voice")

            yield Label("Model — downloaded into this project", classes="section")
            yield OptionList(
                *(f"{m.name}  ({m.detail})" if m.name else m.detail for m in self._models),
                id="models",
            )

            yield Label("", id="total")

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
        """Open on the recommended model — index 1, since row 0 is "no model yet"."""
        models = self.query_one("#models", OptionList)
        models.highlighted = 1 if len(self._models) > 1 else 0
        models.focus()
        self._refresh_total()

    def _refresh_total(self) -> None:
        """Say what this will fetch, in one number, before anything is fetched.

        The screen promises everything lives in this directory; how much "everything" is has to be
        on it, or the first honest account of the cost arrives as a progress bar. The voices are
        not itemized as choices because they are not choices — every project speaks and listens.
        """
        index = self.query_one("#models", OptionList).highlighted or 0
        model = self._models[index] if index < len(self._models) else self._models[0]
        total = model.size_gb + self._voices_gb
        voices = f"the voices ({self._voices_gb:.1f} GB)" if self._voices_gb else ""
        model_part = f"{model.name.split('/')[-1]} ({model.size_gb:.1f} GB)" if model.name else ""
        both = " + ".join(p for p in (model_part, voices) if p)
        self.query_one("#total", Label).update(f"Downloads about {total:.1f} GB — {both}" if both else "")

    def on_option_list_option_highlighted(self, _event: OptionList.OptionHighlighted) -> None:
        self._refresh_total()

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        """Retune the prompt to the kind — never overwriting something the user has typed."""
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
        """Collect the form into a result."""
        index = self.query_one("#models", OptionList).highlighted or 0
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


def run_wizard(
    *, name: str, models: list[ModelChoice], servers: list[McpServer], voices_gb: float = 0.0
) -> WizardResult | None:
    """Run the wizard, returning the choices — or ``None`` if the user quit."""
    return InitWizard(name=name, models=models, servers=servers, voices_gb=voices_gb).run()
