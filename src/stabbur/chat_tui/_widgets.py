"""Chat-TUI widgets: the command palette provider, modals (confirm, model picker), and the chat input."""

import json
from typing import Any

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option


class _StabburCommands(Provider):
    """Commands for the Ctrl+P palette, alongside Textual's built-ins."""

    # Built once per palette opening: Textual calls `discover` and then `search` on the same
    # provider instance, and both need the full list — building it twice did every lookup twice.
    _cache: "list[tuple[str, str, Any]] | None" = None

    def _commands(self) -> list[tuple[str, str, Any]]:
        """(title, help, callback) for every palette command, from live app state (memoized)."""
        if self._cache is None:
            self._cache = self._build_commands()
        return self._cache

    def _build_commands(self) -> list[tuple[str, str, Any]]:
        """The palette's command list. Runs on the event loop, so it must never block on I/O."""
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
        # One "switch to" entry per other model the session can actually reach. A remote attach
        # can only move between the ids the server itself serves, so list those (from the cache
        # primed at mount) — the local library is irrelevant there, and scanning it from a
        # palette provider would block the UI on a drive the session never touches.
        if app._remote is not None:
            for rid, loaded in app._remote_models_cache or []:
                if rid != app._model_name:
                    note = f"switch to {rid}" + (" (loaded on the server)" if loaded else "")
                    items.append((f"Switch model: {rid}", note, lambda n=rid: app.action_switch_model(n)))
            return items
        # Locally, hand the picked model straight to the loader: a name can name two builds
        # (GGUF *and* MLX), so the entry must carry the exact one it is labelled with. The rows come
        # from the cache the mount worker primes — scanning here would block the palette (and the
        # whole UI) on a library that can be an external drive, which is the very thing the remote
        # branch above avoids. A cold cache lists no models and starts the scan for the next open.
        for model in app._cached_switchable_models():
            if model.path != (app._model.path if app._model else None):
                items.append(
                    (
                        f"Switch model: {model.name} ({model.model_format.value})",
                        f"load {model.name} ({model.model_format.value})",
                        lambda m=model: app._switch_to(m),
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


class ModelPickerModal(ModalScreen[int | None]):
    """Pick a model with the arrow keys: Enter selects, Escape cancels.

    Rows are ``(id, note)`` — the note is a dim annotation like the format or ``loaded``.
    ``current`` is the *index* of the running row, which is marked and pre-highlighted.
    Dismisses with the chosen row's index, or ``None`` on cancel.

    Rows are addressed by index, not by name, because a library legitimately holds one model in
    several formats (keeping GGUF *and* MLX is the documented default policy), so two rows can
    carry the same name and only the position says which build is meant. For the same reason the
    options carry no widget ids: identical ids would raise ``DuplicateID`` and the picker would
    never open.
    """

    DEFAULT_CSS = """
    ModelPickerModal { align: center middle; }
    ModelPickerModal > #model-box {
        width: 72; max-width: 90%; height: auto; padding: 1 2;
        border: round #fb7185; background: $surface;
    }
    ModelPickerModal #model-title { text-style: bold; color: #fb7185; }
    ModelPickerModal OptionList {
        height: auto; max-height: 16; margin-top: 1;
        background: transparent; border: none; padding: 0;
    }
    ModelPickerModal #model-hint { color: $text-muted; margin-top: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, rows: list[tuple[str, str]], current: int | None, title: str) -> None:
        super().__init__()
        self._rows = rows
        self._current = current
        self._title = title

    def compose(self) -> ComposeResult:
        options: list[Option] = []
        for i, (rid, note) in enumerate(self._rows):
            label = Text()
            label.append("● " if i == self._current else "  ", style="#fb7185")
            label.append(rid)
            if note:
                label.append(f"   {note}", style="dim")
            options.append(Option(label))
        with Vertical(id="model-box"):
            yield Label(self._title, id="model-title")
            yield OptionList(*options)
            yield Static("↑/↓ select  ·  enter switch  ·  esc cancel", id="model-hint")

    def on_mount(self) -> None:
        picker = self.query_one(OptionList)
        if self._current is not None and 0 <= self._current < len(self._rows):
            picker.highlighted = self._current  # open on the running model
        picker.focus()

    @on(OptionList.OptionSelected)
    def _selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_index)

    def action_cancel(self) -> None:
        self.dismiss(None)


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


__all__ = ["ChatInput", "ConfirmModal", "ModelPickerModal", "_StabburCommands"]
