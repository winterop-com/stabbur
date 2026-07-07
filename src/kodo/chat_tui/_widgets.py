"""Chat-TUI widgets: the command palette provider and the multi-line chat input."""

from typing import Any

from textual import events
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.message import Message
from textual.widgets import TextArea


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


__all__ = ["ChatInput", "_KodoCommands"]
