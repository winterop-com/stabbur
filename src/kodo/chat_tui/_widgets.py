"""Chat-TUI widgets: the command palette provider, confirm modal, and the multi-line chat input."""

import json
from typing import Any

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static, TextArea


class _KodoCommands(Provider):
    """Commands for the Ctrl+P palette, alongside Textual's built-ins."""

    def _commands(self) -> list[tuple[str, str, Any]]:
        """(title, help, callback) for every palette command, from live app state."""
        app: Any = self.app
        items: list[tuple[str, str, Any]] = [
            ("MCP servers & tools", "list the MCP servers and tools available to the model", app.action_show_mcp),
            (
                "Reconnect MCP servers",
                "respawn the MCP servers (if one died or its config changed)",
                app.action_reconnect_mcp,
            ),
            ("Copy last reply", "copy the last reply to the clipboard", app.action_copy_reply),
            ("Export transcript", "save the conversation to a markdown file (chat.md)", app.action_export),
            (
                "Export with thinking",
                "save the transcript with each turn's reasoning folded in (chat.md)",
                lambda: app.action_export(thinking=True),
            ),
            ("Sampling settings", "show sampling; change with /set <param> <value>", app.action_show_sampling),
            ("Switch model", "list the models you can switch to", app.action_show_models),
            ("Clear conversation", "clear the transcript, keep the system prompt", app.action_clear),
            ("Help", "commands + keyboard shortcuts", app.action_help),
        ]
        # One enable/disable entry per loaded MCP server.
        for srv in app._server_names():
            off = srv in app._disabled
            verb = "Enable" if off else "Disable"
            items.append(
                (
                    f"{verb} MCP server: {srv}",
                    f"turn {srv}'s tools {'on' if off else 'off'}",
                    lambda s=srv, e=off: app._mcp_toggle(s, e),
                )
            )
        # One "switch to" entry per other library model.
        for model in app._switchable_models():
            if model.name != app._model_name:
                items.append(
                    (
                        f"Switch model: {model.name}",
                        f"load {model.name} ({model.model_format.value})",
                        lambda n=model.name: app.action_switch_model(n),
                    )
                )
        return items

    async def discover(self) -> Hits:
        # Shown when the palette opens with no query typed yet.
        for title, help_text, callback in self._commands():
            yield DiscoveryHit(title, callback, help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for title, help_text, callback in self._commands():
            score = matcher.match(title)
            if score > 0:
                yield Hit(score, matcher.highlight(title), callback, help=help_text)


class ConfirmModal(ModalScreen[bool]):
    """Ask the user to approve or deny a gated (write) tool call before the agent runs it.

    Dismissed with ``True`` (Approve) or ``False`` (Deny / Escape); the ``on_confirm`` sink in
    the chat app awaits that value via ``push_screen_wait``. Shows the tool name and a compact,
    truncated view of the JSON arguments so the user sees what is about to run.
    """

    DEFAULT_CSS = """
    ConfirmModal { align: center middle; }
    ConfirmModal > #confirm-box {
        width: 72; max-width: 90%; height: auto; padding: 1 2;
        border: round #fb7185; background: $surface;
    }
    ConfirmModal #confirm-title { text-style: bold; color: #fb7185; }
    ConfirmModal #confirm-args { color: $text-muted; margin: 1 0; }
    ConfirmModal #confirm-buttons { height: auto; align-horizontal: right; }
    ConfirmModal #confirm-buttons Button { margin-left: 2; }
    """

    BINDINGS = [Binding("escape", "deny", "Deny")]

    def __init__(self, name: str, args: dict[str, Any]) -> None:
        super().__init__()
        self._tool_name = name
        self._args = args

    def compose(self) -> ComposeResult:
        try:
            rendered = json.dumps(self._args, ensure_ascii=False)
        except (TypeError, ValueError):
            rendered = repr(self._args)
        if not self._args:
            rendered = "(no arguments)"
        elif len(rendered) > 500:
            rendered = rendered[:500] + " …"
        with Vertical(id="confirm-box"):
            yield Label(f"Confirm write: {self._tool_name}", id="confirm-title")
            yield Static(rendered, id="confirm-args")
            with Horizontal(id="confirm-buttons"):
                yield Button("Deny", variant="error", id="deny")
                yield Button("Approve", variant="success", id="approve")

    @on(Button.Pressed, "#approve")
    def _approve(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#deny")
    def _deny(self) -> None:
        self.dismiss(False)

    def action_deny(self) -> None:
        self.dismiss(False)


class ChatInput(TextArea):
    """Multi-line input where Enter sends and Shift+Return inserts a newline.

    Shift+Return needs a terminal that reports it distinctly (kitty/iTerm2/VS Code
    with the enhanced keyboard protocol; Textual enables it when available). Ctrl-J
    and a trailing backslash work everywhere as fallbacks.
    """

    class Submitted(Message):
        """Posted when the user presses Enter on a non-continuation line."""

        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        # Tab completes a slash command being typed (instead of inserting a tab).
        if event.key == "tab" and self.text.startswith("/") and "\n" not in self.text:
            event.prevent_default()
            event.stop()
            complete = getattr(self.app, "_complete_slash", None)
            if complete is not None:
                complete()
            return
        if event.key in ("shift+enter", "ctrl+j"):
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        if event.key == "enter":
            # A trailing backslash at the end of the buffer is a newline (continuation),
            # not a send -- the universal fallback where Shift+Return is unavailable.
            if self.cursor_location == self.document.end and self.text.endswith("\\"):
                event.prevent_default()
                event.stop()
                self.action_delete_left()
                self.insert("\n")
                return
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.text))
            return
        await super()._on_key(event)


__all__ = ["ChatInput", "ConfirmModal", "_KodoCommands"]
