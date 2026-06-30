"""Textual model picker, shown when ``run``/``chat`` get no model argument."""

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, OptionList
from textual.widgets.option_list import Option

from local_llm.library import LibraryModel


class _Picker(App[LibraryModel]):
    """A minimal full-screen list to pick one library model."""

    TITLE = "local-llm — pick a model"
    BINDINGS = [("q", "quit", "Cancel"), ("escape", "quit", "Cancel")]
    CSS = "OptionList { height: 1fr; }"

    def __init__(self, models: list[LibraryModel]) -> None:
        super().__init__()
        self._models = models

    def compose(self) -> ComposeResult:
        """Build the option list, one row per model."""
        yield Header()
        options = [
            Option(f"{m.model_format.value:<6} {m.size_human:>9}  {m.name}", id=str(i))
            for i, m in enumerate(self._models)
        ]
        yield OptionList(*options)
        yield Footer()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Exit returning the chosen model."""
        self.exit(self._models[int(event.option.id or "0")])


def pick_model(models: list[LibraryModel]) -> LibraryModel | None:
    """Show the picker and return the chosen model, or ``None`` if cancelled."""
    if not models:
        return None
    return _Picker(models).run()
