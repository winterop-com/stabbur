"""Full-screen Textual chat for ``kodo chat`` (interactive mode).

A proper TUI over the same runtime + agent loop the CLI uses: a scrolling
transcript (markdown replies, live reasoning, tool activity), a multi-line input
(Enter sends; Shift+Return, Ctrl-J, or a trailing backslash insert a newline),
and a pinned two-line status footer (model, live context usage, tools). The model
server is spawned by the caller; this app owns the MCP toolset + the streamed
agent loop, and ESC cancels an in-flight reply.
"""

import asyncio
import random
import time
from contextlib import AsyncExitStack
from typing import Any

from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widgets import Collapsible, Static, TextArea

from kodo import agent, attach
from kodo import sampling as sampling_mod
from kodo import tools as mcp_tools


def _fmt_tokens(n: int) -> str:
    """Compact token count: 1234 -> 1.2K, 262144 -> 256K."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return f"{n / 1_000_000:.1f}M".replace(".0M", "M")


# Whimsical "working" words shown while the model thinks (one picked per turn), à la Claude Code.
_GERUNDS = (
    "Percolating",
    "Noodling",
    "Conjuring",
    "Ruminating",
    "Marinating",
    "Cogitating",
    "Simmering",
    "Pondering",
    "Tinkering",
    "Brewing",
    "Musing",
    "Whirring",
    "Moonwalking",
    "Computing",
    "Scheming",
    "Puzzling",
    "Deliberating",
    "Contemplating",
    "Synthesizing",
    "Wrangling",
)

# Braille spinner frames for the "thinking" indicator.
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Slash commands typed in the input, for the in-line autocomplete menu. Each is
# (display, completion, help): the completion is what Tab fills in (a trailing space
# where an argument follows). The palette (Ctrl+P) is generated separately from live app state.
_SLASH_COMMANDS: tuple[tuple[str, str, str], ...] = (
    ("/mcp", "/mcp", "MCP servers + tools (alias /tools)"),
    ("/mcp on <server>", "/mcp on ", "enable a server's tools"),
    ("/mcp off <server>", "/mcp off ", "disable a server's tools"),
    ("/mcp reconnect", "/mcp reconnect", "respawn the MCP servers"),
    ("/copy", "/copy", "copy the last reply"),
    ("/clear", "/clear", "clear the conversation"),
    ("/help", "/help", "commands + keyboard shortcuts"),
    ("/exit", "/exit", "quit"),
)


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


class ChatApp(App[None]):
    """The kodo chat TUI: transcript + input + pinned status footer."""

    CSS = """
    Screen { layers: base; }
    #transcript { height: 1fr; padding: 1 2; scrollbar-size-vertical: 0; }
    #transcript > .user { color: $text-muted; margin-top: 2; }
    #transcript > .tools { color: $text-muted; }
    #transcript > .answer { margin-bottom: 1; }
    #transcript Collapsible { border: none; padding: 0; background: transparent; }
    #transcript Collapsible > CollapsibleTitle { color: $text-muted; padding: 0; }
    #transcript Collapsible Contents { padding: 0 0 0 2; }
    .reasoning { color: $text-muted; }
    #transcript > .intro { margin-bottom: 1; }
    #status { height: 3; padding: 1 2 0 2; color: $text-muted; }
    #suggest { height: auto; max-height: 8; padding: 0 3; margin: 0 1; color: $text-muted; display: none; }
    #input { height: auto; max-height: 10; border: round #3a3a3a; margin: 0 1 1 1; padding: 0 1; }
    #input:focus { border: round #fb7185; }
    """

    COMMANDS = App.COMMANDS | {_KodoCommands}

    BINDINGS = [
        Binding("ctrl+d", "quit", "Quit", priority=True),
        Binding("ctrl+p", "command_palette", "Commands", show=True, priority=True),
        Binding("escape", "cancel", "Stop", show=True),
        # A full-screen TUI captures the mouse, so the terminal's own click-drag selection
        # doesn't reach the text. Give an explicit "copy the last reply" shortcut; Textual's
        # own click-drag + ctrl+c selection also works, and holding Option (macOS terminals)
        # falls back to native terminal selection.
        Binding("ctrl+y", "copy_reply", "Copy reply", show=True, priority=True),
    ]

    def __init__(
        self,
        *,
        model_name: str,
        model_format: str,
        model_target: str,
        base: str,
        servers: list[tuple[str | None, list[str], dict[str, str]]],
        system_prompt: str,
        images: list[str],
        audios: list[str],
        max_tokens: int | None,
        ctx_max: int | None,
        sampling: "sampling_mod.ModelSampling",
    ) -> None:
        super().__init__()
        self._model_name = model_name
        self._model_format = model_format
        self._model_target = model_target  # OpenAI ``model`` field value (mlx-vlm needs it)
        self._base = base
        self._servers = servers
        self._max_tokens = max_tokens
        self._ctx_max = ctx_max
        self._sampling = sampling
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}] if system_prompt else []
        self._pending_images: list[str] | None = images
        self._pending_audios: list[str] | None = audios
        self.ctx_used: int | None = None
        self.toolset = mcp_tools.MCPToolset()  # replaced once MCP servers connect
        self._stack = AsyncExitStack()
        self._busy = False
        self._queue: list[str] = []  # prompts submitted while a reply is streaming
        self._disabled: set[str] = set()  # MCP server prefixes the user turned off

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="transcript")
        yield Static(id="suggest")  # slash-command autocomplete menu (above the input)
        yield ChatInput(id="input", soft_wrap=True, show_line_numbers=False)
        yield Static(id="status")

    async def on_mount(self) -> None:
        self.title = "kodo chat"
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.mount(Static(self._intro(), classes="intro"))
        self._refresh_status()
        self.query_one(ChatInput).focus()
        # Connecting MCP servers can take a moment; do it after the UI is up.
        if self._servers:
            self.toolset = await self._stack.enter_async_context(mcp_tools.connect(self._servers))
            self._refresh_status()

    async def on_unmount(self) -> None:
        await self._stack.aclose()

    # -- status footer ---------------------------------------------------------

    def _intro(self) -> Group:
        """The welcome block shown on start: kodo wordmark + version, then model + endpoint."""
        from importlib.metadata import version as _pkg_version  # noqa: PLC0415

        try:
            ver = _pkg_version("kodo")
        except Exception:  # noqa: BLE001 - version is cosmetic; never block the intro on it
            ver = ""

        title = Text()
        title.append("♥ ", style="#f43f5e")  # heartbeat — kodo is 鼓動, "heartbeat/pulse"
        title.append("kodo", style="bold #f43f5e")
        if ver:
            title.append(f"  v{ver}", style="grey50")
        title.append("   local models, on your own hardware", style="grey42")

        model = Text()
        model.append(self._model_name, style="bold")
        model.append(f"   {self._model_format}", style="grey50")

        api = Text(f"{self._base}/v1", style="grey42")
        api.append("   OpenAI-compatible", style="grey35")

        return Group(title, Text(), model, api)

    def _status_renderable(self) -> Group:
        line1 = Text()
        line1.append(self._model_name, style="grey62")
        if self._ctx_max:
            used = self.ctx_used or 0
            pct = used / self._ctx_max
            color = "green" if pct < 0.5 else "yellow" if pct < 0.85 else "red"
            window = f"{round(self._ctx_max / 1024)}K" if self._ctx_max >= 1024 else str(self._ctx_max)
            line1.append("  ·  ", style="grey37")
            line1.append(f"{_fmt_tokens(used)}/{window}", style=color)
            line1.append(f" ({pct * 100:.0f}%)", style="grey50")
        n_tools = len(self._active_toolset().names)
        n_off = len(self._disabled)
        if n_tools or n_off:
            line1.append("  ·  ", style="grey37")
            line1.append(f"{n_tools} tool{'s' if n_tools != 1 else ''}", style="cyan")
            if n_off:
                line1.append(f" ({n_off} off)", style="grey42")
        if self._queue:
            line1.append("  ·  ", style="grey37")
            line1.append(f"{len(self._queue)} queued", style="yellow")

        line2 = Text("enter send  ·  ^p commands  ·  /help  ·  esc stop  ·  /exit", style="grey42")
        return Group(line1, line2)

    def _last_reply_text(self) -> str | None:
        """The text of the most recent assistant reply (skips tool-call turns), if any."""
        for message in reversed(self.messages):
            if message.get("role") == "assistant":
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
        return None

    def action_copy_reply(self) -> None:
        """Copy the last assistant reply to the clipboard (works over SSH via OSC 52)."""
        text = self._last_reply_text()
        if text:
            self.copy_to_clipboard(text)
            self.notify(f"Copied last reply ({len(text)} chars)", timeout=2)
        else:
            self.notify("No reply to copy yet.", severity="warning", timeout=2)

    # -- slash-command autocomplete ---------------------------------------------

    def _match_commands(self, text: str) -> list[tuple[str, str, str]]:
        """Slash commands whose name prefixes the (single-line) text being typed."""
        query = text.lstrip("/").lower()
        return [c for c in _SLASH_COMMANDS if c[0].lstrip("/").lower().startswith(query)]

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._update_suggestions()

    def _update_suggestions(self) -> None:
        """Show/refresh the autocomplete menu while a slash command is being typed."""
        suggest = self.query_one("#suggest", Static)
        text = self.query_one(ChatInput).text
        matches = self._match_commands(text) if text.startswith("/") and "\n" not in text else []
        if not matches:
            suggest.display = False
            return
        body = Text()
        for i, (display, _complete, help_text) in enumerate(matches):
            if i:
                body.append("\n")
            body.append(f"{display:<20}", style="#fb7185")
            body.append(f"  {help_text}", style="grey42")
        body.append("\ntab", style="grey50")
        body.append(" complete  ·  ", style="grey37")
        body.append("enter", style="grey50")
        body.append(" run", style="grey37")
        suggest.update(body)
        suggest.display = True

    def _complete_slash(self) -> None:
        """Tab: fill the input with the matching command (or the common prefix of several)."""
        input_w = self.query_one(ChatInput)
        matches = self._match_commands(input_w.text)
        if not matches:
            return
        completions = [c[1] for c in matches]
        if len(completions) == 1:
            new = completions[0]
        else:
            new = completions[0]
            for other in completions[1:]:
                while not other.startswith(new):
                    new = new[:-1]
        if new and new != input_w.text:
            input_w.text = new
            input_w.move_cursor(input_w.document.end)
        self._update_suggestions()

    # -- commands / MCP management ----------------------------------------------

    def _post(self, renderable: Any) -> None:
        """Mount a muted command-output block in the transcript."""
        transcript = self.query_one("#transcript", VerticalScroll)
        transcript.mount(Static(renderable, classes="reasoning"))
        transcript.scroll_end(animate=False)

    def _server_names(self) -> list[str]:
        """Distinct MCP server prefixes in the loaded toolset, in load order."""
        seen: list[str] = []
        for schema in self.toolset.schemas:
            srv = schema["function"]["name"].split("__")[0]
            if srv not in seen:
                seen.append(srv)
        return seen

    def _active_toolset(self) -> mcp_tools.MCPToolset:
        """What the model sees: the full toolset minus any disabled servers."""
        if not self._disabled:
            return self.toolset
        names = {
            s["function"]["name"]
            for s in self.toolset.schemas
            if s["function"]["name"].split("__")[0] not in self._disabled
        }
        return self.toolset.subset(names)

    def action_show_mcp(self) -> None:
        """List MCP servers + their tools (with on/off state) in the transcript."""
        from collections import defaultdict  # noqa: PLC0415

        servers = self._server_names()
        if not servers:
            self._post(Text("No MCP tools loaded — add servers with `kodo mcp add <name>`.", style="grey50"))
            return
        by_server: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for s in self.toolset.schemas:
            srv, _, tool = s["function"]["name"].partition("__")
            desc = (s["function"].get("description", "") or "").splitlines()[0]
            by_server[srv].append((tool or s["function"]["name"], desc))
        body = Text()
        body.append(
            f"MCP  ·  {len(self._active_toolset().names)} tool(s) active · {len(servers)} server(s)\n", "grey50"
        )
        for srv in servers:
            off = srv in self._disabled
            body.append(f"\n{srv}", style="bold grey42" if off else "bold #fb7185")
            body.append("  (disabled)\n" if off else "\n", style="grey42")
            for tool, desc in by_server[srv]:
                body.append(f"  {tool}", style="grey35" if off else "grey66")
                if desc:
                    body.append(f"   {desc[:60]}", style="grey42")
                body.append("\n")
        body.append("\n/mcp on <server>  ·  /mcp off <server>  ·  /mcp reconnect", style="grey42")
        self._post(body)

    def _mcp_toggle(self, server: str, enable: bool) -> None:
        if server not in self._server_names():
            self._post(Text(f"no such server {server!r} — /mcp to list", style="grey50"))
            return
        self._disabled.discard(server) if enable else self._disabled.add(server)
        self._refresh_status()
        self.action_show_mcp()

    def action_reconnect_mcp(self) -> None:
        """Respawn the project's MCP servers (useful if one died or its config changed)."""
        if not self._servers:
            self._post(Text("No MCP servers to reconnect.", style="grey50"))
            return
        if self._busy:
            self.notify("Stop the current reply first (Esc).", severity="warning", timeout=2)
            return
        self.run_worker(self._reconnect_mcp(), group="mcp")

    async def _reconnect_mcp(self) -> None:
        self.notify("Reconnecting MCP servers…", timeout=2)
        try:
            await self._stack.aclose()
        except Exception:  # noqa: BLE001 - closing a dead connection can raise; ignore
            pass
        self._stack = AsyncExitStack()
        self.toolset = mcp_tools.MCPToolset()
        self._refresh_status()
        try:
            self.toolset = await self._stack.enter_async_context(mcp_tools.connect(self._servers))
        except Exception as exc:  # noqa: BLE001 - surface a failed reconnect instead of hanging
            self._post(Text(f"reconnect failed: {exc}", style="red"))
        self._refresh_status()
        self._post(Text(f"Reconnected — {len(self.toolset.names)} tool(s).", style="grey50"))

    def action_clear(self) -> None:
        """Clear the transcript (keep the intro) and reset the conversation (keep the system prompt)."""
        if self._busy:
            self.notify("Stop the current reply first (Esc).", severity="warning", timeout=2)
            return
        transcript = self.query_one("#transcript", VerticalScroll)
        for child in list(transcript.children):
            if "intro" not in child.classes:
                child.remove()
        self.messages = [m for m in self.messages if m.get("role") == "system"]
        self.ctx_used = None
        self._refresh_status()

    def action_help(self) -> None:
        """Show slash commands + keyboard shortcuts in the transcript."""
        body = Text()
        body.append("commands\n", style="bold #fb7185")
        for cmd, desc in (
            ("/mcp", "MCP servers + tools (alias /tools)"),
            ("/mcp on|off <server>", "enable / disable a server's tools"),
            ("/mcp reconnect", "respawn the MCP servers"),
            ("/copy", "copy the last reply"),
            ("/clear", "clear the conversation"),
            ("/help", "this help"),
            ("/exit", "quit"),
        ):
            body.append(f"  {cmd}", style="grey66")
            body.append(f"   {desc}\n", style="grey42")
        body.append("\nkeys\n", style="bold #fb7185")
        for key, desc in (("ctrl+p", "command palette"), ("ctrl+y", "copy reply"), ("esc", "stop"), ("ctrl+d", "quit")):
            body.append(f"  {key}", style="grey66")
            body.append(f"   {desc}\n", style="grey42")
        self._post(body)

    def _refresh_status(self) -> None:
        self.query_one("#status", Static).update(self._status_renderable())

    # -- input / generation ----------------------------------------------------

    def action_cancel(self) -> None:
        # ESC stops the current reply and drops anything queued behind it.
        self._queue.clear()
        if self._busy:
            self.workers.cancel_group(self, "gen")
            self._refresh_status()

    def on_chat_input_submitted(self, message: ChatInput.Submitted) -> None:
        text = message.text.strip()
        if not text:
            return
        if text.startswith("/") or text in ("exit", "quit"):
            self.query_one(ChatInput).text = ""
            self._update_suggestions()
            self._run_command(text)
            return
        self.query_one(ChatInput).text = ""
        self._update_suggestions()
        # Send now if idle, otherwise queue behind the in-flight reply.
        self._queue.append(message.text)
        self._refresh_status()
        self._pump()

    def _run_command(self, raw: str) -> None:
        """Handle a ``/command`` typed in the input (not sent to the model)."""
        parts = raw.lstrip("/").split()
        name = parts[0].lower() if parts else ""
        arg = parts[1].lower() if len(parts) > 1 else ""
        if name in ("exit", "quit"):
            self.exit()
        elif name in ("mcp", "tools", "t"):
            if arg == "reconnect":
                self.action_reconnect_mcp()
            elif arg in ("on", "enable") and len(parts) > 2:
                self._mcp_toggle(parts[2], enable=True)
            elif arg in ("off", "disable") and len(parts) > 2:
                self._mcp_toggle(parts[2], enable=False)
            else:
                self.action_show_mcp()
        elif name in ("help", "h", "?"):
            self.action_help()
        elif name in ("copy", "y"):
            self.action_copy_reply()
        elif name in ("clear", "reset"):
            self.action_clear()
        else:
            self._post(Text(f"unknown command {raw!r} — /help for commands", style="grey50"))

    def _pump(self) -> None:
        """Start the next queued prompt if nothing is generating."""
        if self._busy or not self._queue:
            return
        self._busy = True
        raw_text = self._queue.pop(0)
        self._refresh_status()
        self.run_worker(self._generate(raw_text), group="gen")

    async def _append(self, widget: Static) -> Static:
        await self.query_one("#transcript", VerticalScroll).mount(widget)
        return widget

    def _scroll_end(self) -> None:
        self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

    async def _generate(self, raw_text: str) -> None:
        input_w = self.query_one(ChatInput)  # kept enabled so the user can queue more
        try:
            text, imgs, auds, files = attach.split_input_media(raw_text)
            imgs = (self._pending_images or []) + imgs
            auds = (self._pending_audios or []) + auds
            self._pending_images = None
            self._pending_audios = None
            content_text = attach.inline_files(text, files)
            if not content_text and not imgs and not auds:
                return

            user = Text()
            user.append("❯ ", style="bold #fb7185")
            user.append(raw_text or "(attachments)")
            extras = []
            if imgs:
                extras.append(f"{len(imgs)} image")
            if auds:
                extras.append(f"{len(auds)} audio")
            if files:
                extras.append(f"{len(files)} file")
            if extras:
                user.append(f"   [{', '.join(extras)}]", style="dim")
            await self._append(Static(user, classes="user"))

            mark = len(self.messages)
            self.messages.append(
                {"role": "user", "content": agent.user_content(content_text, imgs or None, auds or None)}
            )

            reasoning_body = Static("", classes="reasoning")
            reasoning_box = Collapsible(reasoning_body, title="thinking …", collapsed=False)
            reasoning_box.display = False  # shown only if the model streams reasoning
            tools_w = Static("", classes="tools")
            tools_w.display = False
            answer_w = Static(Text(""), classes="answer")
            transcript = self.query_one("#transcript", VerticalScroll)
            await transcript.mount(reasoning_box, tools_w, answer_w)
            self._scroll_end()

            # Animated "thinking" placeholder — a whimsical word + elapsed timer, until the first
            # answer token replaces it (à la Claude Code's "· Moonwalking… (26s · thinking more)").
            think_word = random.choice(_GERUNDS)
            think_start = time.monotonic()
            think_timer: Any = None

            def _think_tick() -> None:
                elapsed = time.monotonic() - think_start
                frame = _SPINNER[int(elapsed / 0.08) % len(_SPINNER)]  # spins independent of the 1s clock
                secs = round(elapsed)
                label = Text(f"{frame} ", style="#fb7185")
                label.append(f"{think_word}… ", style="#fb7185")
                label.append(f"({secs}s{' · thinking more' if secs >= 10 else ''})", style="grey42")
                answer_w.update(label)

            def _stop_think() -> None:
                nonlocal think_timer
                if think_timer is not None:
                    think_timer.stop()
                    think_timer = None

            _think_tick()
            think_timer = self.set_interval(0.08, _think_tick)

            answer_buf: list[str] = []
            reasoning_buf: list[str] = []
            tool_lines: list[str] = []
            last_render = 0.0
            reason_start: float | None = None
            reason_collapsed = False

            def finalize_reasoning() -> None:
                # Once the answer (or tool call) starts, collapse the thinking block
                # under a "thought for Ns" summary the user can re-open. Idempotent.
                nonlocal reason_collapsed
                if reasoning_box.display and not reason_collapsed:
                    reason_collapsed = True
                    elapsed = time.monotonic() - (reason_start or time.monotonic())
                    reasoning_box.title = f"thought for {max(1, round(elapsed))}s"
                    reasoning_box.collapsed = True

            def on_token(tok: str) -> None:
                nonlocal last_render
                _stop_think()  # first answer token — drop the animated placeholder
                finalize_reasoning()
                answer_buf.append(tok)
                now = time.monotonic()
                if now - last_render > 0.08:  # throttle markdown re-render
                    last_render = now
                    answer_w.update(Markdown("".join(answer_buf)))
                    self._scroll_end()

            def on_reasoning(tok: str) -> None:
                nonlocal reason_start
                if reason_start is None:
                    reason_start = time.monotonic()
                    reasoning_box.display = True
                reasoning_buf.append(tok)
                reasoning_body.update(Text("".join(reasoning_buf), style="grey42"))
                self._scroll_end()

            def on_event(kind: str, detail: str) -> None:
                finalize_reasoning()  # a tool call ends the thinking phase too
                marker = "⚙ " if kind == "call" else "↳ "
                tool_lines.append(marker + detail[:200])
                tools_w.display = True
                tools_w.update(Text("\n".join(tool_lines), style="grey50"))
                self._scroll_end()

            def on_usage(usage: dict[str, Any]) -> None:
                total = usage.get("total_tokens")
                if isinstance(total, int):
                    self.ctx_used = total
                else:
                    pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
                    if isinstance(pt, int) and isinstance(ct, int):
                        self.ctx_used = pt + ct
                self._refresh_status()

            try:
                reply = await agent.run(
                    self._base,
                    self.messages,
                    self._active_toolset(),  # honor any servers the user disabled
                    self._max_tokens,
                    on_event,
                    on_token,
                    on_reasoning=on_reasoning,
                    on_usage=on_usage,
                    temperature=self._sampling.temperature,
                    top_p=self._sampling.top_p,
                    top_k=self._sampling.top_k,
                    min_p=self._sampling.min_p,
                    repeat_penalty=self._sampling.repeat_penalty,
                    model=self._model_target,  # required by mlx-vlm; ignored by llama-server/mlx-lm
                )
            except asyncio.CancelledError:  # ESC: drop the partial turn, keep the session
                _stop_think()
                finalize_reasoning()
                del self.messages[mark:]
                answer_w.update(Text("(canceled)", style="yellow"))
                self._scroll_end()
                return
            except Exception as exc:  # noqa: BLE001 - surface runtime/network errors in the transcript
                _stop_think()
                finalize_reasoning()
                del self.messages[mark:]
                answer_w.update(Text(f"error: {exc}", style="red"))
                self._scroll_end()
                return

            _stop_think()
            finalize_reasoning()  # reasoning-only replies (no answer tokens) still collapse
            if (reply or "").strip():
                answer_w.update(Markdown(reply))
            else:
                answer_w.update(Text("(no response)", style="yellow"))
            self._scroll_end()
        finally:
            self._busy = False
            input_w.focus()
            self._refresh_status()
            self._pump()  # drain the next queued prompt, if any


def run_interactive(
    *,
    model_name: str,
    model_format: str,
    model_target: str,
    base: str,
    servers: list[tuple[str | None, list[str], dict[str, str]]],
    system_prompt: str,
    images: list[str],
    audios: list[str],
    max_tokens: int | None,
    ctx_max: int | None,
    sampling: sampling_mod.ModelSampling,
) -> None:
    """Build and run the chat TUI (blocking) against an already-serving model."""
    ChatApp(
        model_name=model_name,
        model_format=model_format,
        model_target=model_target,
        base=base,
        servers=servers,
        system_prompt=system_prompt,
        images=images,
        audios=audios,
        max_tokens=max_tokens,
        ctx_max=ctx_max,
        sampling=sampling,
    ).run()
