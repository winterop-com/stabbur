"""The `stabbur configure` screen: change a project after it exists.

`stabbur init` asks its questions once, at a moment when you know least about what you are
building. Everything it settles — the model, the prompt, which tools, which voices — is a thing
you find out you wanted differently on day two, and until now the answer was "hand-edit
stabbur.toml, then work out which files that implies".

This is the same idea as the init wizard and deliberately the same shape: a form, a save, and
nothing written until you say so. It computes a *plan* rather than acting — the caller performs
the downloads and deletions afterwards, in the terminal, where progress belongs and where a
failure is legible. That also makes the whole thing testable without a filesystem.

Project-scoped on purpose: there is no "configure" for a machine, only `stabbur config` for the
two machine defaults, and a screen that silently edited a different scope depending on where it
was run would be a trap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button,
    Input,
    Label,
    OptionList,
    SelectionList,
    Switch,
    TabbedContent,
    TabPane,
    TextArea,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from stabbur.plugins import McpServer


class ModelOption(NamedTuple):
    """A model the project could bind: in its library already, or downloadable."""

    name: str
    detail: str
    present: bool


class VoiceOption(NamedTuple):
    """A voice model the project could hold, and whether it holds it now."""

    id: str
    label: str
    present: bool


class LibraryEntry(NamedTuple):
    """Something on disk in the project's library, offered for removal."""

    name: str
    size_human: str


class ConfigurePlan(NamedTuple):
    """What the user asked for. Nothing here has happened yet — the caller performs it."""

    model: str
    system_prompt: str
    chat_voice: str | None
    voice_enabled: bool
    tools: list[tuple[str, str]]
    """The MCP servers that should be in ``.mcp.json`` afterwards (name, command)."""
    pull_voices: list[str]
    """Registry voice ids to download into the project."""
    remove_models: list[str]
    """Library model names to delete from the project."""


class ConfigureApp(App[ConfigurePlan | None]):
    """Edit a project's assistant: model, prompt, tools, voice, and what its library holds."""

    CSS = """
    Screen { padding: 1 2; }
    .hint { color: $text-muted; }
    .section { color: $text-muted; margin-top: 1; }
    #models, #tools, #voices, #library { height: auto; max-height: 12; border: round $panel-lighten-2; }
    #prompt { height: 8; border: round $panel-lighten-2; }
    #voice-id { border: round $panel-lighten-2; }
    #actions { height: auto; margin-top: 1; }
    #actions Button { margin-right: 2; }
    .row { height: auto; }
    .row Label { padding: 1 1 0 0; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "save", "Save", show=True, priority=True),
    ]

    def __init__(
        self,
        *,
        name: str,
        models: list[ModelOption],
        current_model: str,
        system_prompt: str,
        chat_voice: str | None,
        voice_enabled: bool,
        servers: list[McpServer],
        enabled_tools: set[str],
        voices: list[VoiceOption],
        library: list[LibraryEntry],
    ) -> None:
        super().__init__()
        self._project_name = name
        self._models = models
        self._current_model = current_model
        self._prompt = system_prompt
        self._chat_voice = chat_voice
        self._voice_enabled = voice_enabled
        self._servers = servers
        self._enabled = enabled_tools
        self._voices = voices
        self._library = library

    def compose(self) -> ComposeResult:
        yield Label(f"Configure {self._project_name}")
        yield Label("Ctrl-S saves; nothing is written or downloaded until then.", classes="hint")
        with TabbedContent(id="tabs"):
            with TabPane("Assistant", id="tab-assistant"), VerticalScroll():
                yield Label("Model", classes="section")
                yield OptionList(
                    *(f"{m.name}  ({m.detail})" + ("" if m.present else "  — will download") for m in self._models),
                    id="models",
                )
                yield Label("System prompt", classes="section")
                yield TextArea(self._prompt, id="prompt")

            with TabPane("Tools", id="tab-tools"), VerticalScroll():
                yield Label("Space to toggle — MCP servers this assistant can call", classes="section")
                if self._servers:
                    yield SelectionList[int](
                        *(
                            (f"{s.name} — {s.description}", i, s.name in self._enabled)
                            for i, s in enumerate(self._servers)
                        ),
                        id="tools",
                    )
                else:
                    yield Label("No MCP plugins installed.", classes="hint")

            with TabPane("Voice", id="tab-voice"), VerticalScroll():
                with Horizontal(classes="row"):
                    yield Label("Voice surface enabled  ")
                    yield Switch(value=self._voice_enabled, id="voice-enabled")
                yield Label("Reply voice — a Kokoro id, or model:<id> for a TTS model", classes="section")
                yield Input(value=self._chat_voice or "", placeholder="kokoro:af_heart", id="voice-id")
                yield Label("Voice models in this project — space to toggle", classes="section")
                if self._voices:
                    yield SelectionList[int](
                        *((v.label, i, v.present) for i, v in enumerate(self._voices)), id="voices"
                    )
                else:
                    yield Label("No voice models are available to add.", classes="hint")

            with TabPane("Library", id="tab-library"), VerticalScroll():
                yield Label("Select to remove from this project's library", classes="section")
                if self._library:
                    yield SelectionList[int](
                        *((f"{e.name}  ({e.size_human})", i) for i, e in enumerate(self._library)), id="library"
                    )
                else:
                    yield Label("This project's library is empty.", classes="hint")
        with Horizontal(id="actions"):
            yield Button("Save", variant="primary", id="save")
            yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        """Highlight the bound model, so the list opens on what the project uses today."""
        models = self.query_one("#models", OptionList)
        current = next((i for i, m in enumerate(self._models) if m.name == self._current_model), 0)
        if self._models:
            models.highlighted = current
        models.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        else:
            self.action_cancel()

    def action_cancel(self) -> None:
        self.exit(None)

    def _selected(self, widget_id: str) -> list[int]:
        """The checked indices of a SelectionList that may not exist (nothing to list)."""
        found = self.query(f"#{widget_id}")
        if not found:
            return []
        widget = found.first()
        assert isinstance(widget, SelectionList)
        return list(widget.selected)

    def action_save(self) -> None:
        models = self.query_one("#models", OptionList)
        index = models.highlighted
        if index is None or not self._models:
            self.notify("Pick a model first.", severity="warning")
            self.query_one("#tabs", TabbedContent).active = "tab-assistant"
            return
        chosen_tools = [(self._servers[i].name, self._servers[i].command) for i in self._selected("tools")]
        wanted = {self._voices[i].id for i in self._selected("voices")}
        voice_id = self.query_one("#voice-id", Input).value.strip()
        self.exit(
            ConfigurePlan(
                model=self._models[index].name,
                system_prompt=self.query_one("#prompt", TextArea).text.strip(),
                chat_voice=voice_id or None,
                voice_enabled=bool(self.query_one("#voice-enabled", Switch).value),
                tools=chosen_tools,
                # Only what isn't there yet: re-pulling a model the project already holds would
                # turn a settings change into a multi-gigabyte download.
                pull_voices=[v.id for v in self._voices if v.id in wanted and not v.present],
                remove_models=[self._library[i].name for i in self._selected("library")],
            )
        )


def run_configure(**kwargs: object) -> ConfigurePlan | None:
    """Run the configure screen, returning the plan — or ``None`` if the user quit."""
    return ConfigureApp(**kwargs).run()  # type: ignore[arg-type]
