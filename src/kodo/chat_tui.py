"""Full-screen Textual chat for ``kodo chat`` (interactive mode).

A proper TUI over the same runtime + agent loop the CLI uses: a scrolling
transcript (markdown replies, live reasoning, tool activity), a multi-line input
(Enter sends; Shift+Return, Ctrl-J, or a trailing backslash insert a newline),
and a pinned two-line status footer (model, live context usage, tools). The model
server is spawned by the caller; this app owns the MCP toolset + the streamed
agent loop, and ESC cancels an in-flight reply.
"""

import asyncio
import time
from contextlib import AsyncExitStack
from typing import Any

from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
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
    #transcript > .user { color: $text; margin-top: 1; }
    #transcript > .tools { color: $text-muted; }
    #transcript > .answer { margin-bottom: 1; }
    #transcript Collapsible { border: none; padding: 0; background: transparent; }
    #transcript Collapsible > CollapsibleTitle { color: $text-muted; padding: 0; }
    #transcript Collapsible Contents { padding: 0 0 0 2; }
    .reasoning { color: $text-muted; }
    #status { height: 2; padding: 0 2; background: $panel; color: $text-muted; }
    #input { height: auto; max-height: 10; border: round $primary; margin: 0 1 1 1; padding: 0 1; }
    #input:focus { border: round $accent; }
    """

    BINDINGS = [
        Binding("ctrl+d", "quit", "Quit", priority=True),
        Binding("escape", "cancel", "Stop", show=True),
    ]

    def __init__(
        self,
        *,
        model_name: str,
        model_format: str,
        model_target: str,
        base: str,
        servers: list[tuple[str | None, list[str]]],
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

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="transcript")
        yield Static(id="status")
        yield ChatInput(id="input", soft_wrap=True, show_line_numbers=False)

    async def on_mount(self) -> None:
        self.title = "kodo chat"
        transcript = self.query_one("#transcript", VerticalScroll)
        intro = Text()
        intro.append(self._model_name, style="bold")
        intro.append(f"  ·  {self._model_format}\n", style="dim")
        intro.append(f"api  {self._base}/v1  (OpenAI-compatible)", style="dim")
        await transcript.mount(Static(intro, classes="reasoning"))
        self._refresh_status()
        self.query_one(ChatInput).focus()
        # Connecting MCP servers can take a moment; do it after the UI is up.
        if self._servers:
            self.toolset = await self._stack.enter_async_context(mcp_tools.connect(self._servers))
            self._refresh_status()

    async def on_unmount(self) -> None:
        await self._stack.aclose()

    # -- status footer ---------------------------------------------------------

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
        n_tools = len(self.toolset.names)
        if n_tools:
            line1.append("  ·  ", style="grey37")
            line1.append(f"{n_tools} tool{'s' if n_tools != 1 else ''}", style="cyan")
        if self._queue:
            line1.append("  ·  ", style="grey37")
            line1.append(f"{len(self._queue)} queued", style="yellow")

        line2 = Text()
        line2.append("▸ ", style="cyan")
        line2.append(self._model_format, style="grey50")
        line2.append("   enter sends  ·  shift+return newline  ·  esc stops  ·  /exit", style="grey42")
        return Group(line1, line2)

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
        if text in ("/exit", "/quit", "exit", "quit"):
            self.exit()
            return
        self.query_one(ChatInput).text = ""
        # Send now if idle, otherwise queue behind the in-flight reply.
        self._queue.append(message.text)
        self._refresh_status()
        self._pump()

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
            user.append("❯ ", style="bold")
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
            answer_w = Static(Text("thinking …", style="dim italic"), classes="answer")
            transcript = self.query_one("#transcript", VerticalScroll)
            await transcript.mount(reasoning_box, tools_w, answer_w)
            self._scroll_end()

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
                    self.toolset,
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
                finalize_reasoning()
                del self.messages[mark:]
                answer_w.update(Text("(canceled)", style="yellow"))
                self._scroll_end()
                return
            except Exception as exc:  # noqa: BLE001 - surface runtime/network errors in the transcript
                finalize_reasoning()
                del self.messages[mark:]
                answer_w.update(Text(f"error: {exc}", style="red"))
                self._scroll_end()
                return

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
    servers: list[tuple[str | None, list[str]]],
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
